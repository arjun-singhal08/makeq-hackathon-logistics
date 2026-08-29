import importlib
import json
import multiprocessing
import sys
from dataclasses import asdict
from datetime import date, time

import pytest
from sqlalchemy.exc import IntegrityError

from food_service import (
    FoodServiceError,
    cancel_food_ticket,
    complete_food_ticket,
    create_food_ticket,
    hash_claim_token,
    no_show_food_ticket,
    requeue_food_ticket,
    verify_claim_token,
)
from models import (
    Event,
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
    initialize_database,
    utc_now,
)
from tests.process_workers import (
    concurrent_reservation_worker,
    create_ticket_worker,
    read_ticket_worker,
)


def reserve(setup, *, quantity=1, participant_name="Team Alpha", ticket_type="TEAM"):
    return create_food_ticket(
        event_id=setup.event_id,
        meal_service_id=setup.meal_service_id,
        meal_option_id=setup.meal_option_id,
        pickup_window_id=setup.pickup_window_id,
        participant_name=participant_name,
        ticket_type=ticket_type,
        quantity=quantity,
    )


def load_ticket(public_id):
    return db.session.execute(
        db.select(Ticket).where(Ticket.public_id == public_id)
    ).scalar_one()


def mark_called(public_id):
    ticket = load_ticket(public_id)
    ticket.status = TicketStatus.CALLED
    ticket.called_at = utc_now()
    db.session.commit()


def movement_quantities(ticket_id, movement_type):
    return db.session.execute(
        db.select(InventoryMovement.quantity)
        .where(
            InventoryMovement.ticket_id == ticket_id,
            InventoryMovement.movement_type == movement_type,
        )
        .order_by(InventoryMovement.id)
    ).scalars().all()


def child_result(context, target, args, *, timeout=30):
    output_queue = context.Queue()
    process = context.Process(target=target, args=(*args, output_queue))
    process.start()
    result = output_queue.get(timeout=timeout)
    process.join(timeout=timeout)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail(f"Child process {target.__name__} did not exit")
    assert process.exitcode == 0, result
    output_queue.close()
    return result


# Required scenario 1.
def test_create_event(app):
    event = Event(name="NUS Food Test Hackathon", active=True)
    db.session.add(event)
    db.session.commit()

    stored = db.session.get(Event, event.id)
    assert stored.name == "NUS Food Test Hackathon"
    assert stored.active is True
    assert stored.created_at is not None


# Required scenario 2.
def test_create_meal_service(app):
    event = Event(name="Meal Service Test Event", active=True)
    db.session.add(event)
    db.session.flush()
    meal = MealService(
        event_id=event.id,
        name="Late Night Meal",
        service_date=date(2026, 8, 30),
        start_time=time(22, 0),
        end_time=time(23, 30),
        location="Atrium",
        status=MealServiceStatus.DRAFT,
    )
    db.session.add(meal)
    db.session.commit()

    stored = db.session.get(MealService, meal.id)
    assert stored.event_id == event.id
    assert stored.name == "Late Night Meal"
    assert stored.service_date == date(2026, 8, 30)
    assert stored.start_time == time(22, 0)
    assert stored.end_time == time(23, 30)
    assert stored.location == "Atrium"
    assert stored.status == MealServiceStatus.DRAFT


# Required scenario 3.
def test_create_arbitrary_meal_options(app, food_setup):
    setup = food_setup(option_quantity=12, option_name="Gluten-free")
    second = MealOption(
        meal_service_id=setup.meal_service_id,
        queue_id=setup.queue_id,
        name="Kosher",
        description="Kosher dinner option",
        planned_quantity=7,
        received_quantity=7,
        available_quantity=7,
        active=True,
    )
    db.session.add(second)
    db.session.commit()

    options = db.session.execute(
        db.select(MealOption)
        .where(MealOption.meal_service_id == setup.meal_service_id)
        .order_by(MealOption.id)
    ).scalars().all()
    assert [option.name for option in options] == ["Gluten-free", "Kosher"]
    assert [option.planned_quantity for option in options] == [12, 7]
    assert all(option.active for option in options)


# Required scenario 4.
def test_create_pickup_windows(app, food_setup):
    setup = food_setup(window_capacity=30)
    second = PickupWindow(
        meal_service_id=setup.meal_service_id,
        start_time=time(18, 45),
        end_time=time(19, 0),
        capacity=45,
        active=True,
    )
    db.session.add(second)
    db.session.commit()

    windows = db.session.execute(
        db.select(PickupWindow)
        .where(PickupWindow.meal_service_id == setup.meal_service_id)
        .order_by(PickupWindow.start_time)
    ).scalars().all()
    assert [(window.start_time, window.end_time) for window in windows] == [
        (time(18, 30), time(18, 45)),
        (time(18, 45), time(19, 0)),
    ]
    assert [window.capacity for window in windows] == [30, 45]


# Required scenario 5.
def test_create_food_ticket_with_food_relationships(app, food_setup):
    setup = food_setup()
    result = reserve(setup, participant_name="Alex", ticket_type="INDIVIDUAL")
    ticket = db.session.get(Ticket, result.ticket_id)

    assert ticket.public_id == result.public_id
    assert ticket.event_id == setup.event_id
    assert ticket.meal_service_id == setup.meal_service_id
    assert ticket.meal_option_id == setup.meal_option_id
    assert ticket.pickup_window_id == setup.pickup_window_id
    assert ticket.queue_id == setup.queue_id
    assert ticket.status == TicketStatus.WAITING
    assert ticket.reservation_active is True


# Required scenario 6.
def test_team_ticket_stores_explicit_quantity(app, food_setup):
    setup = food_setup()
    result = reserve(setup, quantity=4, ticket_type="TEAM")
    ticket = db.session.get(Ticket, result.ticket_id)
    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)

    assert ticket.ticket_type == "TEAM"
    assert ticket.quantity == 4
    assert option.allocated_quantity == 4
    assert window.reserved_quantity == 4


# Required scenario 7.
def test_private_claim_token_generation_and_verification(app, food_setup):
    setup = food_setup()
    first = reserve(setup, participant_name="First")
    second = reserve(setup, participant_name="Second")
    first_ticket = db.session.get(Ticket, first.ticket_id)
    second_ticket = db.session.get(Ticket, second.ticket_id)

    assert first.claim_token != second.claim_token
    assert len(first.claim_token) >= 40
    assert first.claim_token != first.public_id
    assert first_ticket.claim_token_hash == hash_claim_token(first.claim_token)
    assert second_ticket.claim_token_hash == hash_claim_token(second.claim_token)
    assert verify_claim_token(first.claim_token, first_ticket.claim_token_hash)
    assert not verify_claim_token(second.claim_token, first_ticket.claim_token_hash)
    assert first.claim_token not in first_ticket.claim_token_hash


# Required scenario 8.
def test_private_claim_token_never_appears_in_public_api(
    app, food_setup, database_uri, monkeypatch
):
    setup = food_setup()
    result = reserve(setup, participant_name="Private Token Sentinel")
    token_hash = db.session.get(Ticket, result.ticket_id).claim_token_hash
    db.session.remove()

    monkeypatch.setenv("MAKEQ_DATABASE_URL", database_uri)
    monkeypatch.setenv("MAKEQ_SECRET_KEY", "api-privacy-test-secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    app_module.app.config["TESTING"] = True
    try:
        response = app_module.app.test_client().get("/api/queues")
        assert response.status_code == 200
        serialized = json.dumps(response.get_json(), sort_keys=True)
        assert result.public_id in serialized
        assert result.claim_token not in serialized
        assert token_hash not in serialized
        assert "claim_token" not in serialized.lower()
    finally:
        with app_module.app.app_context():
            db.session.remove()
            db.engine.dispose()
        sys.modules.pop("app", None)


# Required scenario 9.
def test_food_reservation_allocates_inventory(app, food_setup):
    setup = food_setup(option_quantity=10, window_capacity=10)
    result = reserve(setup, quantity=4)
    ticket = db.session.get(Ticket, result.ticket_id)
    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)

    assert option.available_quantity == 6
    assert option.allocated_quantity == 4
    assert window.reserved_quantity == 4
    assert movement_quantities(ticket.id, InventoryMovementType.ALLOCATED) == [4]


# Required scenario 10.
def test_food_allocation_cannot_exceed_available_quantity(app, food_setup):
    setup = food_setup(option_quantity=5, window_capacity=100)
    reserve(setup, quantity=4, participant_name="First")

    with pytest.raises(FoodServiceError) as captured:
        reserve(setup, quantity=2, participant_name="Second")
    assert captured.value.code == "OPTION_UNAVAILABLE"

    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)
    assert db.session.scalar(db.select(db.func.count(Ticket.id))) == 1
    assert option.available_quantity == 1
    assert option.allocated_quantity == 4
    assert window.reserved_quantity == 4
    assert db.session.scalar(
        db.select(db.func.count(InventoryMovement.id)).where(
            InventoryMovement.movement_type == InventoryMovementType.ALLOCATED
        )
    ) == 1


# Required scenario 11.
def test_pickup_window_capacity_cannot_be_exceeded(app, food_setup):
    setup = food_setup(option_quantity=100, window_capacity=5)
    reserve(setup, quantity=4, participant_name="First")

    with pytest.raises(FoodServiceError) as captured:
        reserve(setup, quantity=2, participant_name="Second")
    assert captured.value.code == "WINDOW_FULL"

    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)
    assert db.session.scalar(db.select(db.func.count(Ticket.id))) == 1
    assert option.available_quantity == 96
    assert option.allocated_quantity == 4
    assert window.reserved_quantity == 4


# Required scenario 12.
def test_cancellation_releases_reserved_food_and_capacity_once(app, food_setup):
    setup = food_setup(option_quantity=10, window_capacity=10)
    result = reserve(setup, quantity=4)
    cancel_food_ticket(result.public_id)

    ticket = load_ticket(result.public_id)
    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)
    assert ticket.status == TicketStatus.CANCELLED
    assert ticket.reservation_active is False
    assert ticket.cancelled_at is not None
    assert option.available_quantity == 10
    assert option.allocated_quantity == 0
    assert option.released_quantity == 4
    assert window.reserved_quantity == 0
    assert movement_quantities(ticket.id, InventoryMovementType.RELEASED) == [4]

    with pytest.raises(FoodServiceError) as captured:
        cancel_food_ticket(result.public_id)
    assert captured.value.code == "INVALID_TRANSITION"
    assert movement_quantities(ticket.id, InventoryMovementType.RELEASED) == [4]


# Required scenario 13.
def test_completion_records_actual_collected_quantity(app, food_setup):
    setup = food_setup(option_quantity=10, window_capacity=10)
    result = reserve(setup, quantity=4)
    mark_called(result.public_id)
    complete_food_ticket(result.public_id, collected_quantity=3)

    ticket = load_ticket(result.public_id)
    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)
    assert ticket.status == TicketStatus.COMPLETED
    assert ticket.collected_quantity == 3
    assert ticket.completed_at is not None
    assert ticket.reservation_active is False
    assert option.available_quantity == 7
    assert option.allocated_quantity == 0
    assert option.collected_quantity == 3
    assert option.released_quantity == 1
    assert window.reserved_quantity == 0
    assert window.collected_quantity == 3
    assert movement_quantities(ticket.id, InventoryMovementType.COLLECTED) == [3]
    assert movement_quantities(ticket.id, InventoryMovementType.RELEASED) == [1]


# Required scenario 14.
def test_no_show_releases_reservation_and_retains_ticket(app, food_setup):
    setup = food_setup(option_quantity=10, window_capacity=10)
    result = reserve(setup, quantity=3)
    mark_called(result.public_id)
    no_show_food_ticket(result.public_id)

    ticket = load_ticket(result.public_id)
    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)
    assert ticket.status == TicketStatus.NO_SHOW
    assert ticket.no_show_at is not None
    assert ticket.reservation_active is False
    assert option.available_quantity == 10
    assert option.allocated_quantity == 0
    assert option.released_quantity == 3
    assert window.reserved_quantity == 0
    assert movement_quantities(ticket.id, InventoryMovementType.RELEASED) == [3]


# Required scenario 15.
def test_requeue_reacquires_food_and_capacity_without_new_ticket(app, food_setup):
    setup = food_setup(option_quantity=10, window_capacity=10)
    result = reserve(setup, quantity=3)
    original = load_ticket(result.public_id)
    original_id = original.id
    original_hash = original.claim_token_hash
    cancel_food_ticket(result.public_id)
    requeue_food_ticket(result.public_id)

    ticket = load_ticket(result.public_id)
    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)
    assert ticket.id == original_id
    assert ticket.claim_token_hash == original_hash
    assert ticket.status == TicketStatus.WAITING
    assert ticket.reservation_active is True
    assert option.available_quantity == 7
    assert option.allocated_quantity == 3
    assert window.reserved_quantity == 3
    assert movement_quantities(ticket.id, InventoryMovementType.ALLOCATED) == [3, 3]
    assert movement_quantities(ticket.id, InventoryMovementType.RELEASED) == [3]
    assert db.session.scalar(db.select(db.func.count(Ticket.id))) == 1


# Required scenario 16.
def test_fresh_initialization_contains_no_fake_tickets(app):
    assert initialize_database() is True
    assert initialize_database() is False

    assert db.session.scalar(db.select(db.func.count(Ticket.id))) == 0
    assert db.session.scalar(db.select(db.func.count(Event.id))) == 1
    assert db.session.scalar(db.select(db.func.count(MealService.id))) == 1
    assert db.session.scalar(db.select(db.func.count(MealOption.id))) == 4
    assert db.session.scalar(db.select(db.func.count(PickupWindow.id))) == 3
    assert db.session.scalar(
        db.select(db.func.count(InventoryMovement.id)).where(
            InventoryMovement.movement_type.in_(
                [InventoryMovementType.ALLOCATED, InventoryMovementType.COLLECTED]
            )
        )
    ) == 0


# Required scenario 17.
def test_food_ticket_survives_true_application_process_restart(
    app, food_setup, database_uri
):
    setup = food_setup(option_quantity=10, window_capacity=10)
    setup_payload = asdict(setup)
    db.session.remove()
    context = multiprocessing.get_context("spawn")

    created = child_result(
        context,
        create_ticket_worker,
        (database_uri, setup_payload, 4),
    )
    assert created["kind"] == "ok", created

    observed = child_result(
        context,
        read_ticket_worker,
        (database_uri, created["public_id"]),
    )
    assert observed == {
        "kind": "ok",
        "public_id": created["public_id"],
        "quantity": 4,
        "status": "WAITING",
        "event_id": setup.event_id,
        "meal_service_id": setup.meal_service_id,
        "meal_option_id": setup.meal_option_id,
        "pickup_window_id": setup.pickup_window_id,
        "option_allocated": 4,
        "window_reserved": 4,
        "movement_count": 1,
    }


# Required scenario 18.
def test_invalid_quantities_are_rejected_without_side_effects(app, food_setup):
    setup = food_setup(option_quantity=10, window_capacity=10)
    invalid_values = [None, "", "abc", "1.5", 1.5, True, [], {}, 1001]

    for value in invalid_values:
        with pytest.raises(FoodServiceError) as captured:
            reserve(setup, quantity=value)
        assert captured.value.code == "INVALID_QUANTITY"

    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)
    assert db.session.scalar(db.select(db.func.count(Ticket.id))) == 0
    assert option.available_quantity == 10
    assert option.allocated_quantity == 0
    assert window.reserved_quantity == 0


# Required scenario 19.
def test_zero_and_negative_quantities_are_rejected_by_service_and_database(
    app, food_setup
):
    setup = food_setup(option_quantity=10, window_capacity=10)
    for value in [0, -1, -999]:
        with pytest.raises(FoodServiceError) as captured:
            reserve(setup, quantity=value)
        assert captured.value.code == "INVALID_QUANTITY"

    invalid_ticket = Ticket(
        public_id="MQ-DATABASE-CHECK",
        claim_token_hash="a" * 64,
        event_id=setup.event_id,
        queue_id=setup.queue_id,
        meal_service_id=setup.meal_service_id,
        meal_option_id=setup.meal_option_id,
        pickup_window_id=setup.pickup_window_id,
        participant_name="Constraint Test",
        ticket_type="INDIVIDUAL",
        quantity=0,
        collected_quantity=0,
        status=TicketStatus.WAITING,
    )
    db.session.add(invalid_ticket)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert db.session.scalar(db.select(db.func.count(Ticket.id))) == 0


# Required scenario 20.
@pytest.mark.parametrize(
    ("option_quantity", "window_capacity", "expected_code"),
    [
        (5, 100, "OPTION_UNAVAILABLE"),
        (100, 5, "WINDOW_FULL"),
    ],
)
def test_concurrent_sqlite_reservations_cannot_overbook_food_or_capacity(
    app,
    food_setup,
    database_uri,
    option_quantity,
    window_capacity,
    expected_code,
):
    setup = food_setup(
        option_quantity=option_quantity,
        window_capacity=window_capacity,
    )
    setup_payload = asdict(setup)
    db.session.remove()

    context = multiprocessing.get_context("spawn")
    ready_queue = context.Queue()
    output_queue = context.Queue()
    start_event = context.Event()
    processes = [
        context.Process(
            target=concurrent_reservation_worker,
            args=(
                database_uri,
                setup_payload,
                4,
                ready_queue,
                start_event,
                output_queue,
                participant_name,
            ),
        )
        for participant_name in ("Concurrent Team A", "Concurrent Team B")
    ]
    for process in processes:
        process.start()
    assert ready_queue.get(timeout=20) is True
    assert ready_queue.get(timeout=20) is True
    start_event.set()

    results = [output_queue.get(timeout=30), output_queue.get(timeout=30)]
    for process in processes:
        process.join(timeout=30)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            pytest.fail("Concurrent reservation worker did not exit")
        assert process.exitcode == 0, results

    successes = [result for result in results if result["kind"] == "ok"]
    failures = [result for result in results if result["kind"] == "error"]
    assert len(successes) == 1, results
    assert len(failures) == 1, results
    assert failures[0]["code"] in {expected_code, "RESERVATION_CONFLICT"}

    db.session.remove()
    tickets = db.session.execute(
        db.select(Ticket).where(Ticket.meal_service_id == setup.meal_service_id)
    ).scalars().all()
    option = db.session.get(MealOption, setup.meal_option_id)
    window = db.session.get(PickupWindow, setup.pickup_window_id)
    allocated_movements = db.session.execute(
        db.select(InventoryMovement).where(
            InventoryMovement.meal_option_id == setup.meal_option_id,
            InventoryMovement.movement_type == InventoryMovementType.ALLOCATED,
        )
    ).scalars().all()
    assert len(tickets) == 1
    assert tickets[0].quantity == 4
    assert option.allocated_quantity == 4
    assert option.available_quantity == option_quantity - 4
    assert window.reserved_quantity == 4
    assert len(allocated_movements) == 1
    assert allocated_movements[0].quantity == 4

    ready_queue.close()
    output_queue.close()


def test_database_rejects_mismatched_food_ticket_relationships(app, food_setup):
    first = food_setup(option_name="Standard")
    second = food_setup(option_name="Halal")
    mismatched = Ticket(
        public_id="MQ-MISMATCH",
        claim_token_hash="b" * 64,
        event_id=first.event_id,
        queue_id=first.queue_id,
        meal_service_id=first.meal_service_id,
        meal_option_id=second.meal_option_id,
        pickup_window_id=first.pickup_window_id,
        participant_name="Constraint Test",
        ticket_type="INDIVIDUAL",
        quantity=1,
        collected_quantity=0,
        status=TicketStatus.WAITING,
    )
    db.session.add(mismatched)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
