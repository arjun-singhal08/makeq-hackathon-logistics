"""Transactional food reservation and ticket lifecycle operations."""

import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError, OperationalError

from identity_service import IdentityServiceError, resolve_reservation_identity
from models import (
    InventoryMovement,
    InventoryMovementType,
    MealOption,
    MealService,
    MealServiceStatus,
    PickupWindow,
    Queue,
    Ticket,
    TicketStatus,
    db,
    utc_now,
)


MAX_TICKET_QUANTITY = 1000
ACTIVE_STATUSES = (TicketStatus.CALLED, TicketStatus.IN_SERVICE)


class FoodServiceError(Exception):
    """Expected domain failure that can be shown safely to a participant or operator."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ReservationResult:
    ticket_id: int
    public_id: str
    claim_token: str


def parse_quantity(value, *, field_name="Quantity"):
    if isinstance(value, bool):
        raise FoodServiceError("INVALID_QUANTITY", f"{field_name} must be a whole number.")
    try:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or any(character not in "0123456789" for character in stripped):
                raise ValueError
            quantity = int(stripped)
        elif isinstance(value, int):
            quantity = value
        else:
            raise ValueError
    except (TypeError, ValueError):
        raise FoodServiceError("INVALID_QUANTITY", f"{field_name} must be a whole number.")

    if quantity <= 0:
        raise FoodServiceError("INVALID_QUANTITY", f"{field_name} must be at least 1.")
    if quantity > MAX_TICKET_QUANTITY:
        raise FoodServiceError(
            "INVALID_QUANTITY",
            f"{field_name} cannot exceed {MAX_TICKET_QUANTITY}.",
        )
    return quantity


def hash_claim_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_claim_token(token, stored_hash):
    if not token or not stored_hash:
        return False
    return secrets.compare_digest(hash_claim_token(token), stored_hash)


def _new_public_id():
    for _attempt in range(10):
        candidate = f"MQ-{secrets.token_hex(6).upper()}"
        exists = db.session.execute(
            db.select(Ticket.id).where(Ticket.public_id == candidate)
        ).scalar_one_or_none()
        if exists is None:
            return candidate
    raise FoodServiceError("IDENTIFIER_FAILURE", "Could not create a ticket identifier.")


def _load_reservation_context(event_id, meal_service_id, meal_option_id, pickup_window_id):
    meal = db.session.execute(
        db.select(MealService).where(
            MealService.id == meal_service_id,
            MealService.event_id == event_id,
        )
    ).scalar_one_or_none()
    if meal is None:
        raise FoodServiceError("RELATIONSHIP_MISMATCH", "Choose a valid meal service.")
    if meal.status != MealServiceStatus.OPEN:
        raise FoodServiceError("MEAL_NOT_OPEN", "That meal service is not open.")

    option = db.session.execute(
        db.select(MealOption).where(
            MealOption.id == meal_option_id,
            MealOption.meal_service_id == meal.id,
            MealOption.active.is_(True),
        )
    ).scalar_one_or_none()
    if option is None:
        raise FoodServiceError("RELATIONSHIP_MISMATCH", "Choose a valid active meal option.")

    window = db.session.execute(
        db.select(PickupWindow).where(
            PickupWindow.id == pickup_window_id,
            PickupWindow.meal_service_id == meal.id,
            PickupWindow.active.is_(True),
        )
    ).scalar_one_or_none()
    if window is None:
        raise FoodServiceError("RELATIONSHIP_MISMATCH", "Choose a valid active pickup window.")

    queue = db.session.execute(
        db.select(Queue).where(
            Queue.id == option.queue_id,
            Queue.event_id == event_id,
            Queue.active.is_(True),
        )
    ).scalar_one_or_none()
    if queue is None:
        raise FoodServiceError("RELATIONSHIP_MISMATCH", "The meal option has no active queue.")
    return meal, option, window, queue


def _allocate(option_id, window_id, meal_service_id, quantity, now):
    service_result = db.session.execute(
        db.update(MealService)
        .where(
            MealService.id == meal_service_id,
            MealService.status == MealServiceStatus.OPEN,
        )
        .values(updated_at=now)
    )
    if service_result.rowcount != 1:
        raise FoodServiceError("MEAL_NOT_OPEN", "That meal service is not open.")

    option_result = db.session.execute(
        db.update(MealOption)
        .where(
            MealOption.id == option_id,
            MealOption.meal_service_id == meal_service_id,
            MealOption.active.is_(True),
            MealOption.available_quantity >= quantity,
        )
        .values(
            available_quantity=MealOption.available_quantity - quantity,
            allocated_quantity=MealOption.allocated_quantity + quantity,
            updated_at=now,
        )
    )
    if option_result.rowcount != 1:
        raise FoodServiceError(
            "OPTION_UNAVAILABLE", "There is not enough of that meal option available."
        )

    window_result = db.session.execute(
        db.update(PickupWindow)
        .where(
            PickupWindow.id == window_id,
            PickupWindow.meal_service_id == meal_service_id,
            PickupWindow.active.is_(True),
            PickupWindow.reserved_quantity
            + PickupWindow.collected_quantity
            + quantity
            <= PickupWindow.capacity,
        )
        .values(
            reserved_quantity=PickupWindow.reserved_quantity + quantity,
            updated_at=now,
        )
    )
    if window_result.rowcount != 1:
        raise FoodServiceError("WINDOW_FULL", "That pickup window does not have enough capacity.")


def create_food_ticket(
    *,
    event_id,
    meal_service_id,
    meal_option_id,
    pickup_window_id,
    participant_name,
    ticket_type,
    quantity,
    priority=0,
    notes="",
    participant_id=None,
    team_id=None,
):
    quantity = parse_quantity(quantity)
    participant_name = "" if participant_name is None else str(participant_name).strip()
    ticket_type = "" if ticket_type is None else str(ticket_type).strip().upper()
    if ticket_type not in {"INDIVIDUAL", "TEAM"}:
        raise FoodServiceError("INVALID_TICKET_TYPE", "Choose a valid ticket type.")

    try:
        participant, team, registered_name = resolve_reservation_identity(
            event_id=event_id,
            participant_id=participant_id,
            team_id=team_id,
        )
        if registered_name is not None:
            participant_name = registered_name
        if not participant_name or len(participant_name) > 120:
            raise FoodServiceError(
                "INVALID_PARTICIPANT",
                "Enter a participant or team name of 120 characters or fewer.",
            )
        meal, option, window, queue = _load_reservation_context(
            event_id, meal_service_id, meal_option_id, pickup_window_id
        )
        now = utc_now()
        _allocate(option.id, window.id, meal.id, quantity, now)

        claim_token = secrets.token_urlsafe(32)
        ticket = Ticket(
            public_id=_new_public_id(),
            claim_token_hash=hash_claim_token(claim_token),
            event_id=event_id,
            queue_id=queue.id,
            participant_id=participant.id if participant else None,
            team_id=team.id if team else None,
            meal_service_id=meal.id,
            meal_option_id=option.id,
            pickup_window_id=window.id,
            participant_name=participant_name,
            ticket_type=ticket_type,
            quantity=quantity,
            collected_quantity=0,
            reservation_active=True,
            status=TicketStatus.WAITING,
            priority=priority,
            notes=notes,
            created_at=now,
            queued_at=now,
            updated_at=now,
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add(
            InventoryMovement(
                meal_option_id=option.id,
                ticket_id=ticket.id,
                movement_type=InventoryMovementType.ALLOCATED,
                quantity=quantity,
                reason=f"Reserved for ticket {ticket.public_id}",
                created_at=now,
            )
        )
        db.session.commit()
        return ReservationResult(ticket.id, ticket.public_id, claim_token)
    except IdentityServiceError as error:
        db.session.rollback()
        raise FoodServiceError(error.code, error.message) from error
    except FoodServiceError:
        db.session.rollback()
        raise
    except (IntegrityError, OperationalError) as error:
        db.session.rollback()
        raise FoodServiceError(
            "RESERVATION_CONFLICT", "The reservation changed concurrently. Please try again."
        ) from error


def _get_food_ticket(public_id):
    ticket = db.session.execute(
        db.select(Ticket).where(Ticket.public_id == public_id).with_for_update()
    ).scalar_one_or_none()
    if ticket is None or ticket.meal_service_id is None:
        raise FoodServiceError("TICKET_NOT_FOUND", "Food ticket not found.")
    return ticket


def _release_reservation(ticket, target_status, timestamp_field):
    allowed = (
        (TicketStatus.CALLED,)
        if target_status == TicketStatus.NO_SHOW
        else (TicketStatus.WAITING, TicketStatus.CALLED, TicketStatus.IN_SERVICE)
    )
    if ticket.status not in allowed or not ticket.reservation_active:
        raise FoodServiceError(
            "INVALID_TRANSITION",
            f"This ticket cannot be marked {target_status.value.replace('_', ' ').lower()} now.",
        )

    now = utc_now()
    values = {
        "status": target_status,
        "reservation_active": False,
        "completed_at": None,
        "updated_at": now,
        timestamp_field: now,
    }
    ticket_result = db.session.execute(
        db.update(Ticket)
        .where(
            Ticket.id == ticket.id,
            Ticket.status == ticket.status,
            Ticket.reservation_active.is_(True),
        )
        .values(**values)
    )
    if ticket_result.rowcount != 1:
        raise FoodServiceError("ALREADY_PROCESSED", "Another operator updated this ticket.")

    option_result = db.session.execute(
        db.update(MealOption)
        .where(
            MealOption.id == ticket.meal_option_id,
            MealOption.allocated_quantity >= ticket.quantity,
        )
        .values(
            available_quantity=MealOption.available_quantity + ticket.quantity,
            allocated_quantity=MealOption.allocated_quantity - ticket.quantity,
            released_quantity=MealOption.released_quantity + ticket.quantity,
            updated_at=now,
        )
    )
    window_result = db.session.execute(
        db.update(PickupWindow)
        .where(
            PickupWindow.id == ticket.pickup_window_id,
            PickupWindow.reserved_quantity >= ticket.quantity,
        )
        .values(
            reserved_quantity=PickupWindow.reserved_quantity - ticket.quantity,
            updated_at=now,
        )
    )
    if option_result.rowcount != 1 or window_result.rowcount != 1:
        raise FoodServiceError("INVENTORY_CONFLICT", "The reservation totals are inconsistent.")

    db.session.add(
        InventoryMovement(
            meal_option_id=ticket.meal_option_id,
            ticket_id=ticket.id,
            movement_type=InventoryMovementType.RELEASED,
            quantity=ticket.quantity,
            reason=f"{target_status.value.replace('_', ' ').title()} ticket {ticket.public_id}",
            created_at=now,
        )
    )


def cancel_food_ticket(public_id):
    try:
        ticket = _get_food_ticket(public_id)
        _release_reservation(ticket, TicketStatus.CANCELLED, "cancelled_at")
        db.session.commit()
    except FoodServiceError:
        db.session.rollback()
        raise
    except (IntegrityError, OperationalError) as error:
        db.session.rollback()
        raise FoodServiceError("INVENTORY_CONFLICT", "The cancellation could not be saved.") from error


def no_show_food_ticket(public_id):
    try:
        ticket = _get_food_ticket(public_id)
        _release_reservation(ticket, TicketStatus.NO_SHOW, "no_show_at")
        db.session.commit()
    except FoodServiceError:
        db.session.rollback()
        raise
    except (IntegrityError, OperationalError) as error:
        db.session.rollback()
        raise FoodServiceError("INVENTORY_CONFLICT", "The no-show could not be saved.") from error


def complete_food_ticket(public_id, collected_quantity=None):
    try:
        ticket = _get_food_ticket(public_id)
        if ticket.status not in ACTIVE_STATUSES or not ticket.reservation_active:
            raise FoodServiceError("INVALID_TRANSITION", "This ticket cannot be completed now.")
        actual = ticket.quantity if collected_quantity is None else parse_quantity(
            collected_quantity, field_name="Collected quantity"
        )
        if actual > ticket.quantity:
            raise FoodServiceError(
                "INVALID_QUANTITY", "Collected quantity cannot exceed the reservation."
            )
        released = ticket.quantity - actual
        now = utc_now()

        ticket_result = db.session.execute(
            db.update(Ticket)
            .where(
                Ticket.id == ticket.id,
                Ticket.status == ticket.status,
                Ticket.reservation_active.is_(True),
            )
            .values(
                status=TicketStatus.COMPLETED,
                collected_quantity=actual,
                reservation_active=False,
                completed_at=now,
                updated_at=now,
            )
        )
        option_result = db.session.execute(
            db.update(MealOption)
            .where(
                MealOption.id == ticket.meal_option_id,
                MealOption.allocated_quantity >= ticket.quantity,
            )
            .values(
                available_quantity=MealOption.available_quantity + released,
                allocated_quantity=MealOption.allocated_quantity - ticket.quantity,
                collected_quantity=MealOption.collected_quantity + actual,
                released_quantity=MealOption.released_quantity + released,
                updated_at=now,
            )
        )
        window_result = db.session.execute(
            db.update(PickupWindow)
            .where(
                PickupWindow.id == ticket.pickup_window_id,
                PickupWindow.reserved_quantity >= ticket.quantity,
            )
            .values(
                reserved_quantity=PickupWindow.reserved_quantity - ticket.quantity,
                collected_quantity=PickupWindow.collected_quantity + actual,
                updated_at=now,
            )
        )
        if ticket_result.rowcount != 1 or option_result.rowcount != 1 or window_result.rowcount != 1:
            raise FoodServiceError("INVENTORY_CONFLICT", "The collection totals are inconsistent.")

        db.session.add(
            InventoryMovement(
                meal_option_id=ticket.meal_option_id,
                ticket_id=ticket.id,
                movement_type=InventoryMovementType.COLLECTED,
                quantity=actual,
                reason=f"Collected for ticket {ticket.public_id}",
                created_at=now,
            )
        )
        if released:
            db.session.add(
                InventoryMovement(
                    meal_option_id=ticket.meal_option_id,
                    ticket_id=ticket.id,
                    movement_type=InventoryMovementType.RELEASED,
                    quantity=released,
                    reason=f"Uncollected balance for ticket {ticket.public_id}",
                    created_at=now,
                )
            )
        db.session.commit()
    except FoodServiceError:
        db.session.rollback()
        raise
    except (IntegrityError, OperationalError) as error:
        db.session.rollback()
        raise FoodServiceError("INVENTORY_CONFLICT", "The collection could not be saved.") from error


def requeue_food_ticket(public_id):
    try:
        ticket = _get_food_ticket(public_id)
        now = utc_now()
        if ticket.status in ACTIVE_STATUSES and ticket.reservation_active:
            result = db.session.execute(
                db.update(Ticket)
                .where(Ticket.id == ticket.id, Ticket.status == ticket.status)
                .values(
                    status=TicketStatus.WAITING,
                    queued_at=now,
                    called_at=None,
                    completed_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise FoodServiceError("ALREADY_PROCESSED", "Another operator updated this ticket.")
        elif ticket.status in {TicketStatus.CANCELLED, TicketStatus.NO_SHOW} and not ticket.reservation_active:
            meal, option, window, _queue = _load_reservation_context(
                ticket.event_id,
                ticket.meal_service_id,
                ticket.meal_option_id,
                ticket.pickup_window_id,
            )
            _allocate(option.id, window.id, meal.id, ticket.quantity, now)
            result = db.session.execute(
                db.update(Ticket)
                .where(
                    Ticket.id == ticket.id,
                    Ticket.status == ticket.status,
                    Ticket.reservation_active.is_(False),
                )
                .values(
                    status=TicketStatus.WAITING,
                    reservation_active=True,
                    collected_quantity=0,
                    queued_at=now,
                    called_at=None,
                    completed_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise FoodServiceError("ALREADY_PROCESSED", "Another operator updated this ticket.")
            db.session.add(
                InventoryMovement(
                    meal_option_id=ticket.meal_option_id,
                    ticket_id=ticket.id,
                    movement_type=InventoryMovementType.ALLOCATED,
                    quantity=ticket.quantity,
                    reason=f"Requeued ticket {ticket.public_id}",
                    created_at=now,
                )
            )
        else:
            raise FoodServiceError("INVALID_TRANSITION", "This ticket cannot be requeued now.")
        db.session.commit()
    except FoodServiceError:
        db.session.rollback()
        raise
    except (IntegrityError, OperationalError) as error:
        db.session.rollback()
        raise FoodServiceError("RESERVATION_CONFLICT", "The ticket could not be requeued.") from error
