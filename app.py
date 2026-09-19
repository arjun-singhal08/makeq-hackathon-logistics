import os
import sys
from datetime import time, timezone
from functools import wraps

import click
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_migrate import Migrate, upgrade
from sqlalchemy import inspect, or_
from sqlalchemy.exc import IntegrityError, OperationalError

from identity_service import (
    IdentityServiceError,
    create_participant,
    create_team,
    event_has_registered_identities,
    set_participant_active,
    set_participant_team,
    set_team_active,
)
from food_service import (
    FoodServiceError,
    cancel_food_ticket,
    complete_food_ticket,
    create_food_ticket,
    no_show_food_ticket,
    parse_quantity,
    requeue_food_ticket,
)
from team_preference_service import (
    TeamPreferenceError,
    create_team_reservation,
    preference_breakdown,
)
from models import (
    Announcement,
    Event,
    InventoryMovement,
    InventoryMovementType,
    MealOption,
    MealService,
    MealServiceStatus,
    Participant,
    PickupWindow,
    Queue,
    Team,
    Ticket,
    TicketStatus,
    db,
    initialize_database,
    utc_now,
)


app = Flask(__name__)

configured_secret_key = (
    os.getenv("MAKEQ_SECRET_KEY")
    or os.getenv("LUNCHLINE_SECRET_KEY")
    or os.getenv("SECRET_KEY")
)
production_mode = (
    os.getenv("MAKEQ_ENV", "").lower() == "production"
    or os.getenv("RENDER", "").lower() in {"1", "true", "yes"}
)
if production_mode and not configured_secret_key:
    configured_secret_key = "makeq-production-secret-key-render-default"

app.secret_key = configured_secret_key or os.urandom(32)

database_url = os.getenv("MAKEQ_DATABASE_URL") or os.getenv("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url and database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
if not database_url:
    os.makedirs(app.instance_path, exist_ok=True)
    database_url = f"sqlite:///{os.path.join(app.instance_path, 'makeq.db')}"

app.config.update(
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=64 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("MAKEQ_SECURE_COOKIES", "").lower() in {"1", "true", "yes"},
)
db.init_app(app)
migrate = Migrate(app, db, compare_type=True, render_as_batch=True)

ADMIN_USERNAME = (
    os.getenv("MAKEQ_ADMIN_USERNAME")
    or os.getenv("LUNCHLINE_ADMIN_USERNAME")
    or "admin"
)
ADMIN_PASSWORD = (
    os.getenv("MAKEQ_ADMIN_PASSWORD")
    or os.getenv("LUNCHLINE_ADMIN_PASSWORD")
    or "hackathon2026"
)


# ======================================================================
# CORS SUPPORT FOR PUBLIC APIS
# ======================================================================
@app.after_request
def add_cors_headers(response):
    if request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


def admin_required(view):
    @wraps(view)
    def protected_view(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Organizer authentication required for that action.", "warning")
            return redirect(url_for("admin_deck"))
        return view(*args, **kwargs)

    return protected_view


def redirect_to_deck_or_index():
    referrer = request.referrer or ""
    if "/admin" in referrer:
        return redirect(url_for("admin_deck"))
    return redirect(url_for("index"))


ACTIVE_TICKET_STATUSES = (TicketStatus.CALLED, TicketStatus.IN_SERVICE)
TERMINAL_TICKET_STATUSES = (
    TicketStatus.COMPLETED,
    TicketStatus.NO_SHOW,
    TicketStatus.CANCELLED,
)


def get_active_event():
    return db.session.execute(
        db.select(Event)
        .where(Event.active.is_(True))
        .order_by(Event.id)
        .limit(1)
    ).scalar_one_or_none()


def get_event_queues(event):
    if event is None:
        return []
    return db.session.execute(
        db.select(Queue)
        .where(Queue.event_id == event.id, Queue.active.is_(True))
        .order_by(Queue.id)
    ).scalars().all()


def get_queue_for_event(event, queue_name):
    if event is None:
        return None
    return db.session.execute(
        db.select(Queue).where(
            Queue.event_id == event.id,
            Queue.name == queue_name,
            Queue.active.is_(True),
        )
    ).scalar_one_or_none()


def get_open_meal_service(event):
    if event is None:
        return None
    return db.session.execute(
        db.select(MealService)
        .where(
            MealService.event_id == event.id,
            MealService.status == MealServiceStatus.OPEN,
        )
        .order_by(MealService.service_date, MealService.start_time, MealService.id)
        .limit(1)
    ).scalar_one_or_none()


def get_all_meal_services(event):
    if event is None:
        return []
    return db.session.execute(
        db.select(MealService)
        .where(MealService.event_id == event.id)
        .order_by(MealService.start_time, MealService.id)
    ).scalars().all()


def get_meal_options(meal):
    if meal is None:
        return []
    return db.session.execute(
        db.select(MealOption)
        .where(MealOption.meal_service_id == meal.id, MealOption.active.is_(True))
        .order_by(MealOption.id)
    ).scalars().all()


def get_pickup_windows(meal):
    if meal is None:
        return []
    return db.session.execute(
        db.select(PickupWindow)
        .where(
            PickupWindow.meal_service_id == meal.id,
            PickupWindow.active.is_(True),
        )
        .order_by(PickupWindow.start_time, PickupWindow.id)
    ).scalars().all()


def get_event_participants(event, *, active_only=False):
    if event is None:
        return []
    statement = db.select(Participant).where(Participant.event_id == event.id)
    if active_only:
        statement = statement.where(Participant.active.is_(True))
    return db.session.execute(statement.order_by(Participant.name, Participant.id)).scalars().all()


def get_event_teams(event, *, active_only=False):
    if event is None:
        return []
    statement = db.select(Team).where(Team.event_id == event.id)
    if active_only:
        statement = statement.where(Team.active.is_(True))
    return db.session.execute(statement.order_by(Team.name, Team.id)).scalars().all()


def timestamp_label(value):
    return value.strftime("%H:%M UTC") if value else ""


def utc_isoformat(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ticket_view(ticket):
    view = {
        "id": ticket.public_id,
        "label": ticket.participant_name,
        "type": ticket.ticket_type.replace("_", " ").title(),
        "status": ticket.status.value,
        "priority": ticket.priority,
        "joined_at": timestamp_label(ticket.created_at),
        "quantity": ticket.quantity,
    }
    if ticket.meal_option is not None:
        view.update(
            {
                "meal": ticket.meal_service.name if ticket.meal_service else "",
                "meal_option": ticket.meal_option.name,
                "pickup_window": (
                    f"{ticket.pickup_window.start_time.strftime('%H:%M')}–"
                    f"{ticket.pickup_window.end_time.strftime('%H:%M')}"
                    if ticket.pickup_window else ""
                ),
            }
        )
    return view


def queue_snapshot(event):
    snapshot = {}
    for queue in get_event_queues(event):
        waiting = db.session.execute(
            db.select(Ticket)
            .where(
                Ticket.queue_id == queue.id,
                Ticket.event_id == event.id,
                Ticket.status == TicketStatus.WAITING,
            )
            .order_by(Ticket.priority.desc(), Ticket.queued_at, Ticket.id)
        ).scalars().all()
        current = db.session.execute(
            db.select(Ticket)
            .where(
                Ticket.queue_id == queue.id,
                Ticket.event_id == event.id,
                Ticket.status.in_(ACTIVE_TICKET_STATUSES),
            )
            .order_by(Ticket.called_at.desc(), Ticket.id)
            .limit(1)
        ).scalar_one_or_none()
        snapshot[queue.name] = {
            "id": queue.id,
            "description": queue.description,
            "waiting": [ticket_view(ticket) for ticket in waiting],
            "now_serving": ticket_view(current) if current else None,
            "paused": queue.paused,
        }
    return snapshot


def active_announcements(event):
    if event is None:
        return []
    now = utc_now()
    records = db.session.execute(
        db.select(Announcement)
        .where(
            Announcement.event_id == event.id,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
        )
        .order_by(Announcement.created_at.desc(), Announcement.id.desc())
    ).scalars().all()
    return [
        {
            "id": announcement.id,
            "message": announcement.message,
            "posted_at": timestamp_label(announcement.created_at),
            "created_at": utc_isoformat(announcement.created_at),
        }
        for announcement in records
    ]


def recent_tickets(event, limit=16):
    if event is None:
        return []
    records = db.session.execute(
        db.select(Ticket)
        .where(
            Ticket.event_id == event.id,
            Ticket.status.in_(TERMINAL_TICKET_STATUSES),
        )
        .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        .limit(limit)
    ).scalars().all()
    return [
        {
            **ticket_view(ticket),
            "queue_name": ticket.queue.name if ticket.queue else "General",
            "can_requeue": ticket.status in {TicketStatus.NO_SHOW, TicketStatus.CANCELLED},
        }
        for ticket in records
    ]


def transition_ticket(ticket, action):
    action = action.strip().lower().replace("-", "_")
    now = utc_now()

    if action == "start" and ticket.status == TicketStatus.CALLED:
        return True, f"{ticket.public_id} is now in service.", {
            "status": TicketStatus.IN_SERVICE,
            "updated_at": now,
        }

    if action == "complete" and ticket.status in ACTIVE_TICKET_STATUSES:
        return True, f"{ticket.public_id} completed.", {
            "status": TicketStatus.COMPLETED,
            "completed_at": now,
            "updated_at": now,
        }

    if action == "no_show" and ticket.status == TicketStatus.CALLED:
        return True, f"{ticket.public_id} marked as no-show.", {
            "status": TicketStatus.NO_SHOW,
            "completed_at": None,
            "updated_at": now,
        }

    if action == "cancel" and ticket.status in {
        TicketStatus.WAITING,
        TicketStatus.CALLED,
        TicketStatus.IN_SERVICE,
    }:
        return True, f"{ticket.public_id} cancelled.", {
            "status": TicketStatus.CANCELLED,
            "completed_at": None,
            "updated_at": now,
        }

    if action == "requeue" and ticket.status in {
        TicketStatus.CALLED,
        TicketStatus.IN_SERVICE,
        TicketStatus.NO_SHOW,
        TicketStatus.CANCELLED,
    }:
        return True, f"{ticket.public_id} returned to the end of the queue.", {
            "status": TicketStatus.WAITING,
            "queued_at": now,
            "called_at": None,
            "completed_at": None,
            "updated_at": now,
        }

    return (
        False,
        f"{action.replace('_', ' ').title()} is not valid from {ticket.status.value}.",
        {},
    )


# ======================================================================
# AUTOMATIC IDEMPOTENT DATABASE INITIALIZATION & SEEDING
# ======================================================================
DEFAULT_EVENT_NAME = "MakeQ Demo Hackathon"


def init_db_and_seed(flask_app):
    """Automatically and idempotently check core tables and seed default demo data."""
    with flask_app.app_context():
        # 1. Safe idempotent table creation
        try:
            inspector = inspect(db.engine)
            existing_tables = inspector.get_table_names()
            if "events" not in existing_tables:
                db.create_all()
        except (OperationalError, ProgrammingError) as exc:
            flask_app.logger.warning(
                f"Database schema initialization caught expected exception: {exc}"
            )
        except Exception as exc:
            flask_app.logger.warning(f"Database schema check warning: {exc}")

        # 2. Safe idempotent seeding: only call initialize_database() if Event.query.first() is None
        try:
            is_testing = (
                flask_app.config.get("TESTING")
                or os.getenv("TESTING", "").lower() in {"1", "true", "yes"}
                or "pytest" in sys.modules
                or "PYTEST_CURRENT_TEST" in os.environ
            )
            if is_testing:
                return

            if Event.query.first() is None:
                initialize_database()
        except (OperationalError, ProgrammingError) as exc:
            db.session.rollback()
            flask_app.logger.warning(f"Database seeding skipped due to db collision: {exc}")
        except Exception as exc:
            db.session.rollback()
            flask_app.logger.warning(f"Database auto-initialization skipped or failed: {exc}")


_db_initialized = False

@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        _db_initialized = True
        is_testing = (
            app.config.get("TESTING")
            or "pytest" in sys.modules
            or "PYTEST_CURRENT_TEST" in os.environ
        )
        if not is_testing:
            init_db_and_seed(app)


# Trigger auto-initialization on startup when not under test suite
if (
    "pytest" not in sys.modules
    and "PYTEST_CURRENT_TEST" not in os.environ
    and os.getenv("TESTING", "").lower() not in {"1", "true", "yes"}
):
    init_db_and_seed(app)


# ======================================================================
# APPLICATION ROUTE HANDLERS
# ======================================================================

@app.get("/")
def index():
    """Participant booking view with active meal status and live queue monitor."""
    event = get_active_event()
    event_queues = get_event_queues(event)
    meal_service = get_open_meal_service(event)
    meal_options = get_meal_options(meal_service)
    pickup_windows = get_pickup_windows(meal_service)
    registered_identity_required = (
        event_has_registered_identities(event.id) if event is not None else False
    )
    registered_participants = get_event_participants(event, active_only=True)
    registered_teams = get_event_teams(event, active_only=True)
    registration_available = (
        (not registered_identity_required or bool(registered_participants or registered_teams))
        and any(option.available_quantity > 0 for option in meal_options)
        and any(
            window.capacity - window.reserved_quantity - window.collected_quantity > 0
            for window in pickup_windows
        )
    )
    is_admin = session.get("is_admin", False)
    queues = queue_snapshot(event)

    return render_template(
        "index.html",
        event_name=event.name if event else "MakeQ Hackathon",
        queue_options=[queue.name for queue in event_queues],
        meal_service=meal_service,
        meal_options=meal_options,
        pickup_windows=pickup_windows,
        registered_identity_required=registered_identity_required,
        registered_participants=registered_participants,
        registered_teams=registered_teams,
        all_participants=get_event_participants(event),
        all_teams=get_event_teams(event),
        registration_available=registration_available,
        reservation_receipt=session.pop("reservation_receipt", None),
        team_preference_receipt=session.pop("team_preference_receipt", None),
        queues=queues,
        announcements=active_announcements(event),
        is_admin=is_admin,
    )


@app.post("/register")
def register():
    """Processes individual and team reservations, updates inventory atomically, and issues a claim receipt."""
    event = get_active_event()
    participant_name = (
        request.form.get("participant_name")
        or request.form.get("group_name")
        or ""
    ).strip()
    submitted_type = request.form.get("ticket_type", "individual").strip().lower()
    ticket_types = {
        "individual": "INDIVIDUAL",
        "solo": "INDIVIDUAL",
        "team": "TEAM",
        "group": "TEAM",
    }

    meal = get_open_meal_service(event)
    if event is None or meal is None:
        flash("No meal service is currently open for reservations.", "danger")
        return redirect(url_for("index"))
    if submitted_type not in ticket_types:
        flash("Choose a valid ticket type.", "danger")
        return redirect(url_for("index"))

    participant_id = None
    team_id = None
    if event is not None and event_has_registered_identities(event.id):
        identity_type = request.form.get("identity_type", "").strip().lower()
        identity_value = request.form.get("registered_identity_id", "").strip()
        if not identity_type and ":" in identity_value:
            identity_type, identity_value = identity_value.split(":", 1)
            identity_type = identity_type.strip().lower()
        if identity_type in {"participant", "team"} and identity_value:
            try:
                identity_id = int(identity_value)
                if identity_type == "participant":
                    participant_id = identity_id
                else:
                    team_id = identity_id
                submitted_type = "individual" if identity_type == "participant" else "team"
            except ValueError:
                flash("Choose a valid registered participant or team.", "danger")
                return redirect(url_for("index"))

    try:
        meal_service_id = int(request.form.get("meal_service_id") or meal.id)
        window_value = request.form.get("pickup_window_id")
        if window_value:
            pickup_window_id = int(window_value)
        else:
            fallback_window = get_pickup_windows(meal)
            if not fallback_window:
                raise FoodServiceError(
                    "RELATIONSHIP_MISMATCH", "No pickup window is available for this meal."
                )
            pickup_window_id = fallback_window[0].id
    except (TypeError, ValueError):
        flash("Choose a valid pickup window.", "danger")
        return redirect(url_for("index"))
    except FoodServiceError as error:
        flash(error.message, "danger")
        return redirect(url_for("index"))

    # Team reservation flow
    if team_id is not None:
        prefix = f"member_option_{team_id}_"
        member_preferences = []
        try:
            for field_name, option_id in request.form.items():
                if field_name.startswith(prefix):
                    member_id = int(field_name.removeprefix(prefix))
                    member_preferences.append(
                        {"participant_id": member_id, "meal_option_id": int(option_id)}
                    )
        except ValueError:
            flash("Choose a valid meal option for every team member.", "danger")
            return redirect(url_for("index"))
        try:
            team_reservation = create_team_reservation(
                event_id=event.id,
                team_id=team_id,
                meal_service_id=meal_service_id,
                pickup_window_id=pickup_window_id,
                member_preferences=member_preferences,
            )
        except TeamPreferenceError as error:
            flash(error.message, "danger")
            return redirect(url_for("index"))

        selected_meal = db.session.get(MealService, meal_service_id)
        selected_window = db.session.get(PickupWindow, pickup_window_id)
        session["team_preference_receipt"] = {
            "team_name": team_reservation.team.name,
            "meal_name": selected_meal.name,
            "service_date": selected_meal.service_date.strftime("%d %b %Y"),
            "pickup_window": (
                f"{selected_window.start_time.strftime('%H:%M')}–"
                f"{selected_window.end_time.strftime('%H:%M')}"
            ),
            "location": selected_meal.location,
            "quantity": team_reservation.quantity,
            "breakdown": preference_breakdown(team_reservation),
        }
        return redirect(url_for("index"))

    # Individual / standard food ticket flow
    try:
        option_value = request.form.get("meal_option_id")
        if option_value:
            meal_option_id = int(option_value)
        else:
            queue_name = (
                request.form.get("queue_name") or request.form.get("dietary") or ""
            ).strip()
            fallback_option = db.session.execute(
                db.select(MealOption)
                .join(Queue, MealOption.queue_id == Queue.id)
                .where(
                    MealOption.meal_service_id == meal.id,
                    MealOption.active.is_(True),
                    Queue.name == queue_name,
                )
                .limit(1)
            ).scalar_one_or_none()
            if fallback_option is None:
                raise FoodServiceError("RELATIONSHIP_MISMATCH", "Choose a valid meal option.")
            meal_option_id = fallback_option.id
        quantity = parse_quantity(request.form.get("quantity", "1"))
    except (TypeError, ValueError):
        flash("Choose a valid meal option and pickup window.", "danger")
        return redirect(url_for("index"))
    except FoodServiceError as error:
        flash(error.message, "danger")
        return redirect(url_for("index"))

    try:
        result = create_food_ticket(
            event_id=event.id,
            meal_service_id=meal_service_id,
            meal_option_id=meal_option_id,
            pickup_window_id=pickup_window_id,
            participant_name=participant_name,
            ticket_type=ticket_types[submitted_type],
            quantity=quantity,
            participant_id=participant_id,
            team_id=team_id,
        )
    except FoodServiceError as error:
        flash(error.message, "danger")
        return redirect(url_for("index"))

    selected_meal = db.session.get(MealService, meal_service_id)
    selected_option = db.session.get(MealOption, meal_option_id)
    selected_window = db.session.get(PickupWindow, pickup_window_id)

    # Calculate queue position
    target_queue_id = selected_option.queue_id
    tickets_ahead = db.session.execute(
        db.select(db.func.count(Ticket.id)).where(
            Ticket.queue_id == target_queue_id,
            Ticket.event_id == event.id,
            Ticket.status == TicketStatus.WAITING,
            Ticket.id < result.ticket_id,
        )
    ).scalar() or 0
    estimated_wait = max(2, (tickets_ahead + 1) * 2)

    session["reservation_receipt"] = {
        "public_id": result.public_id,
        "claim_token": result.claim_token,
        "event_name": event.name,
        "meal_name": selected_meal.name,
        "service_date": selected_meal.service_date.strftime("%d %b %Y"),
        "option_name": selected_option.name,
        "quantity": quantity,
        "pickup_window": (
            f"{selected_window.start_time.strftime('%H:%M')}–"
            f"{selected_window.end_time.strftime('%H:%M')}"
        ),
        "location": selected_meal.location,
        "queue_name": selected_option.queue.name if selected_option.queue else "Standard",
        "tickets_ahead": tickets_ahead,
        "estimated_wait": estimated_wait,
    }
    flash(f"🎉 Ticket {result.public_id} confirmed! Your meal reservation is secured.", "success")
    return redirect(url_for("index"))


# ======================================================================
# ORGANIZER CONTROL DECK (/admin)
# ======================================================================

@app.get("/admin")
def admin_deck():
    """Authenticated organizer control deck."""
    event = get_active_event()
    is_admin = session.get("is_admin", False)

    if not is_admin:
        return render_template(
            "admin.html",
            is_admin=False,
            event_name=event.name if event else "MakeQ Organizer Deck",
            admin_username=ADMIN_USERNAME,
            admin_password=ADMIN_PASSWORD,
        )

    all_services = get_all_meal_services(event)
    open_service = get_open_meal_service(event)
    queues = queue_snapshot(event)
    announcements = active_announcements(event)
    recent = recent_tickets(event, limit=20)

    # Compute inventory depletion metrics for active meal
    inventory_summary = []
    if open_service:
        options = get_meal_options(open_service)
        for opt in options:
            initial = opt.received_quantity or opt.planned_quantity or 1
            available = opt.available_quantity
            reserved = max(0, opt.allocated_quantity - opt.collected_quantity)
            collected = opt.collected_quantity
            pct_remaining = round((available / initial) * 100) if initial > 0 else 0
            if pct_remaining > 35:
                status_color = "emerald"
            elif pct_remaining > 15:
                status_color = "amber"
            else:
                status_color = "rose"

            inventory_summary.append({
                "id": opt.id,
                "name": opt.name,
                "queue_name": opt.queue.name if opt.queue else "General",
                "initial": initial,
                "available": available,
                "reserved": reserved,
                "collected": collected,
                "pct_remaining": pct_remaining,
                "status_color": status_color,
            })

    total_waiting = sum(len(q["waiting"]) for q in queues.values())
    total_active = sum(1 for q in queues.values() if q["now_serving"])
    total_served = db.session.execute(
        db.select(db.func.count(Ticket.id)).where(
            Ticket.event_id == event.id if event else False,
            Ticket.status == TicketStatus.COMPLETED,
        )
    ).scalar() or 0

    return render_template(
        "admin.html",
        is_admin=True,
        event_name=event.name if event else "MakeQ Organizer Deck",
        all_services=all_services,
        open_service=open_service,
        inventory_summary=inventory_summary,
        queues=queues,
        announcements=announcements,
        recent_tickets=recent,
        total_waiting=total_waiting,
        total_active=total_active,
        total_served=total_served,
        admin_username=ADMIN_USERNAME,
        admin_password=ADMIN_PASSWORD,
    )


@app.post("/admin/login")
def admin_login():
    """Authenticate organizer with configured credentials."""
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session["is_admin"] = True
        flash("Admin controls unlocked. Welcome, Organizer!", "success")
        return redirect(url_for("admin_deck"))
    else:
        session.pop("is_admin", None)
        flash("Invalid organizer credentials.", "danger")
        return redirect(url_for("admin_deck"))


@app.post("/admin/logout")
@admin_required
def admin_logout():
    """Lock organizer controls."""
    session.pop("is_admin", None)
    flash("Organizer controls locked successfully.", "info")
    return redirect(url_for("admin_deck"))


@app.post("/admin/call-next")
@admin_required
def call_next():
    """One-click 'Call Next Ticket' per dietary queue."""
    event = get_active_event()
    queue_name = (
        request.form.get("queue_name") or request.form.get("dietary") or ""
    ).strip()
    queue = db.session.execute(
        db.select(Queue)
        .where(
            Queue.event_id == event.id if event else False,
            Queue.name == queue_name,
            Queue.active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if queue is None:
        flash("Choose a valid queue.", "danger")
        return redirect_to_deck_or_index()
    if queue.paused:
        flash(f"{queue.name} queue is paused.", "warning")
        return redirect_to_deck_or_index()

    current = db.session.execute(
        db.select(Ticket)
        .where(
            Ticket.queue_id == queue.id,
            Ticket.event_id == event.id,
            Ticket.status.in_(ACTIVE_TICKET_STATUSES),
        )
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if current is not None:
        flash(
            f"Please resolve active ticket {current.public_id} before calling another in {queue.name}.",
            "warning",
        )
        return redirect_to_deck_or_index()

    ticket = db.session.execute(
        db.select(Ticket)
        .where(
            Ticket.queue_id == queue.id,
            Ticket.event_id == event.id,
            Ticket.status == TicketStatus.WAITING,
        )
        .order_by(Ticket.priority.desc(), Ticket.queued_at, Ticket.id)
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if ticket is None:
        flash(f"The {queue.name} queue has no waiting tickets.", "info")
        return redirect_to_deck_or_index()

    called_at = utc_now()
    try:
        result = db.session.execute(
            db.update(Ticket)
            .where(Ticket.id == ticket.id, Ticket.status == TicketStatus.WAITING)
            .values(
                status=TicketStatus.CALLED,
                called_at=called_at,
                completed_at=None,
                updated_at=called_at,
            )
        )
        if result.rowcount != 1:
            db.session.rollback()
            flash("Another operator updated the queue. Please try again.", "warning")
            return redirect_to_deck_or_index()
        db.session.commit()
    except (IntegrityError, OperationalError):
        db.session.rollback()
        flash("Another ticket is already active in this queue.", "warning")
        return redirect_to_deck_or_index()

    flash(f"🔔 Now serving ticket {ticket.public_id} at {queue.name} Station!", "success")
    return redirect_to_deck_or_index()


@app.post("/admin/broadcast-leftovers")
@admin_required
def broadcast_leftovers():
    """Instant broadcast: announce second-round leftovers to participants and live display."""
    event = get_active_event()
    if event is None:
        flash("No active event found.", "danger")
        return redirect_to_deck_or_index()

    message = request.form.get("message", "").strip() or (
        "📢 SECOND ROUND OPEN: Leftovers & unclaimed meal boxes are now available at the Main Counter "
        "for all participants! First-come, first-served."
    )
    announcement = Announcement(event_id=event.id, message=message)
    db.session.add(announcement)
    db.session.commit()

    flash("📢 Second-round leftovers broadcasted to all participant screens and stage displays!", "success")
    return redirect_to_deck_or_index()


@app.post("/admin/call-second-round")
@admin_required
def call_second_round():
    """Legacy or direct second-round trigger, redirects to leftovers broadcast."""
    return broadcast_leftovers()


@app.post("/admin/update-queue")
@admin_required
def update_queue():
    """Toggle Pause / Resume or purge waiting tickets for a queue."""
    event = get_active_event()
    queue_name = (
        request.form.get("queue_name") or request.form.get("dietary") or ""
    ).strip()
    action = request.form.get("action", "").strip()
    queue = get_queue_for_event(event, queue_name)

    if queue is None:
        flash("Choose a valid queue to update.", "danger")
        return redirect_to_deck_or_index()

    if action == "pause":
        queue.paused = True
        flash(f"⏸️ {queue.name} queue paused.", "warning")
    elif action == "resume":
        queue.paused = False
        flash(f"▶️ {queue.name} queue resumed.", "success")
    elif action in {"purge", "cancel_waiting"}:
        try:
            amount = min(100, max(0, int(request.form.get("amount", "0"))))
        except ValueError:
            amount = 0
        ticket_rows = db.session.execute(
            db.select(Ticket.id, Ticket.public_id, Ticket.meal_service_id)
            .where(
                Ticket.queue_id == queue.id,
                Ticket.event_id == event.id,
                Ticket.status == TicketStatus.WAITING,
            )
            .order_by(Ticket.priority.desc(), Ticket.queued_at, Ticket.id)
            .limit(amount)
        ).all()
        cancelled = 0
        for row in ticket_rows:
            try:
                if row.meal_service_id:
                    cancel_food_ticket(row.public_id)
                else:
                    db.session.execute(
                        db.update(Ticket)
                        .where(Ticket.id == row.id, Ticket.status == TicketStatus.WAITING)
                        .values(status=TicketStatus.CANCELLED, updated_at=utc_now())
                    )
                cancelled += 1
            except FoodServiceError:
                continue
        flash(f"Cancelled {cancelled} waiting ticket(s) in {queue.name}.", "warning")
    else:
        flash("Choose a valid queue action.", "danger")
        return redirect_to_deck_or_index()

    db.session.commit()
    return redirect_to_deck_or_index()


@app.post("/admin/meal-service/<int:service_id>/activate")
@admin_required
def activate_meal_service(service_id):
    """Switch active meal service (e.g. from Breakfast to Lunch or Dinner)."""
    event = get_active_event()
    target_service = db.session.get(MealService, service_id)
    if not target_service or target_service.event_id != (event.id if event else -1):
        flash("Meal service not found.", "danger")
        return redirect_to_deck_or_index()

    # Close all other services for this event, open target
    services = db.session.execute(
        db.select(MealService).where(MealService.event_id == event.id)
    ).scalars().all()
    for s in services:
        if s.id == target_service.id:
            s.status = MealServiceStatus.OPEN
        else:
            if s.status == MealServiceStatus.OPEN:
                s.status = MealServiceStatus.CLOSED
    db.session.commit()
    flash(f"🍽️ Activated {target_service.name} as the open meal service!", "success")
    return redirect_to_deck_or_index()


@app.post("/admin/ticket/<string:public_id>/<string:action>")
@admin_required
def update_ticket_status(public_id, action):
    """Progress ticket through its lifecycle (start, complete, no-show, requeue, cancel)."""
    event = get_active_event()
    ticket = db.session.execute(
        db.select(Ticket)
        .where(Ticket.public_id == public_id, Ticket.event_id == event.id if event else False)
        .with_for_update()
    ).scalar_one_or_none()
    if ticket is None:
        flash("Ticket not found.", "danger")
        return redirect_to_deck_or_index()

    normalized_action = action.strip().lower().replace("-", "_")
    if ticket.meal_service_id is not None and normalized_action in {
        "cancel",
        "complete",
        "no_show",
        "requeue",
    }:
        try:
            if normalized_action == "cancel":
                cancel_food_ticket(public_id)
                message = f"Ticket {public_id} cancelled; inventory released."
            elif normalized_action == "complete":
                actual = request.form.get("collected_quantity") or None
                complete_food_ticket(public_id, actual)
                message = f"Ticket {public_id} marked COMPLETED. Meal issued!"
            elif normalized_action == "no_show":
                no_show_food_ticket(public_id)
                message = f"Ticket {public_id} marked NO-SHOW; meal returned to pool."
            else:
                requeue_food_ticket(public_id)
                message = f"Ticket {public_id} returned to the end of the line."
        except FoodServiceError as error:
            flash(error.message, "warning")
            return redirect_to_deck_or_index()
        flash(message, "success")
        return redirect_to_deck_or_index()

    expected_status = ticket.status
    changed, message, values = transition_ticket(ticket, action)
    if not changed:
        db.session.rollback()
        flash(message, "warning")
        return redirect_to_deck_or_index()

    try:
        result = db.session.execute(
            db.update(Ticket)
            .where(
                Ticket.id == ticket.id,
                Ticket.event_id == event.id,
                Ticket.status == expected_status,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            db.session.rollback()
            flash("Another operator updated this ticket. Please try again.", "warning")
            return redirect_to_deck_or_index()
        db.session.commit()
    except (IntegrityError, OperationalError):
        db.session.rollback()
        flash("That transition conflicts with the active queue state.", "warning")
        return redirect_to_deck_or_index()

    flash(message, "success")
    return redirect_to_deck_or_index()


@app.post("/admin/announcement")
@admin_required
def post_announcement():
    """Create and broadcast a new organizer announcement."""
    event = get_active_event()
    message = request.form.get("message", "").strip()
    if not message:
        flash("Enter an announcement message first.", "danger")
    elif len(message) > 240:
        flash("Announcements must be 240 characters or fewer.", "danger")
    elif event is None:
        flash("No active event is available.", "danger")
    else:
        db.session.add(Announcement(event_id=event.id, message=message))
        db.session.commit()
        flash("Announcement broadcasted successfully.", "success")
    return redirect_to_deck_or_index()


@app.post("/admin/announcement/<int:announcement_id>/delete")
@admin_required
def delete_announcement(announcement_id):
    """Delete a specific announcement."""
    event = get_active_event()
    announcement = db.session.execute(
        db.select(Announcement).where(
            Announcement.id == announcement_id,
            Announcement.event_id == event.id if event else False,
        )
    ).scalar_one_or_none()
    if announcement is not None:
        db.session.delete(announcement)
        db.session.commit()
        flash("Announcement deleted.", "info")
    else:
        flash("Announcement was already cleared.", "info")
    return redirect_to_deck_or_index()


@app.post("/admin/announcement/clear")
@admin_required
def clear_announcements():
    """Clear all active announcements."""
    event = get_active_event()
    if event is not None:
        db.session.execute(
            db.delete(Announcement).where(Announcement.event_id == event.id)
        )
        db.session.commit()
    flash("All announcements cleared.", "info")
    return redirect_to_deck_or_index()


def _optional_positive_id(value, *, field_name):
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError as error:
        raise IdentityServiceError("INVALID_INPUT", f"Choose a valid {field_name}.") from error
    if parsed <= 0:
        raise IdentityServiceError("INVALID_INPUT", f"Choose a valid {field_name}.")
    return parsed


@app.post("/admin/participants")
@admin_required
def add_participant():
    event = get_active_event()
    if event is None:
        flash("No active event is available.", "danger")
        return redirect_to_deck_or_index()
    try:
        participant = create_participant(
            event_id=event.id,
            name=request.form.get("name"),
            organizer_identifier=request.form.get("organizer_identifier"),
            team_id=_optional_positive_id(request.form.get("team_id"), field_name="team"),
        )
        flash(f"Registered {participant.name}.", "success")
    except IdentityServiceError as error:
        flash(error.message, "danger")
    return redirect_to_deck_or_index()


@app.post("/admin/teams")
@admin_required
def add_team():
    event = get_active_event()
    if event is None:
        flash("No active event is available.", "danger")
        return redirect_to_deck_or_index()
    try:
        team = create_team(event_id=event.id, name=request.form.get("name"))
        flash(f"Created team {team.name}.", "success")
    except IdentityServiceError as error:
        flash(error.message, "danger")
    return redirect_to_deck_or_index()


@app.post("/admin/participants/<int:participant_id>/team")
@admin_required
def update_participant_team(participant_id):
    event = get_active_event()
    if event is None:
        flash("No active event is available.", "danger")
        return redirect_to_deck_or_index()
    try:
        team_id = _optional_positive_id(request.form.get("team_id"), field_name="team")
        participant = set_participant_team(
            event_id=event.id, participant_id=participant_id, team_id=team_id
        )
        if participant.team is None:
            flash(f"Removed {participant.name} from their team.", "info")
        else:
            flash(f"Assigned {participant.name} to {participant.team.name}.", "success")
    except IdentityServiceError as error:
        flash(error.message, "danger")
    return redirect_to_deck_or_index()


@app.post("/admin/participants/<int:participant_id>/active")
@admin_required
def update_participant_active(participant_id):
    event = get_active_event()
    if event is None:
        flash("No active event is available.", "danger")
        return redirect_to_deck_or_index()
    try:
        active = request.form.get("active") == "true"
        participant = set_participant_active(
            event_id=event.id, participant_id=participant_id, active=active
        )
        flash(
            f"{participant.name} is now {'active' if participant.active else 'inactive'}.",
            "success" if participant.active else "warning",
        )
    except IdentityServiceError as error:
        flash(error.message, "danger")
    return redirect_to_deck_or_index()


@app.post("/admin/teams/<int:team_id>/active")
@admin_required
def update_team_active(team_id):
    event = get_active_event()
    if event is None:
        flash("No active event is available.", "danger")
        return redirect_to_deck_or_index()
    try:
        active = request.form.get("active") == "true"
        team = set_team_active(event_id=event.id, team_id=team_id, active=active)
        flash(
            f"{team.name} is now {'active' if team.active else 'inactive'}.",
            "success" if team.active else "warning",
        )
    except IdentityServiceError as error:
        flash(error.message, "danger")
    return redirect_to_deck_or_index()


# ======================================================================
# FULL-SCREEN STAGE DISPLAY & REAL-TIME APIS
# ======================================================================

@app.get("/display")
def display():
    """Full-screen stage display view for venue projectors and TVs."""
    event = get_active_event()
    queues = queue_snapshot(event)
    total_waiting = sum(len(q["waiting"]) for q in queues.values())
    announcements = active_announcements(event)

    return render_template(
        "display.html",
        event_name=event.name if event else "MakeQ Hackathon",
        queues=queues,
        total_waiting=total_waiting,
        announcements=announcements,
    )


@app.get("/api/queues")
def queues_api():
    """Return clean JSON status snapshots with CORS enabled."""
    event = get_active_event()
    dashboard_queues = queue_snapshot(event)
    public_queues = {
        queue_name: {
            "paused": queue["paused"],
            "waiting_count": len(queue["waiting"]),
            "next_up": [
                {"public_id": ticket["id"]} for ticket in queue["waiting"][:5]
            ],
            "now_serving": (
                {
                    "public_id": queue["now_serving"]["id"],
                    "status": queue["now_serving"]["status"],
                }
                if queue["now_serving"]
                else None
            ),
        }
        for queue_name, queue in dashboard_queues.items()
    }
    announcements = [
        {
            "id": announcement["id"],
            "message": announcement["message"],
            "created_at": announcement["created_at"],
            "posted_at": announcement.get("posted_at", ""),
        }
        for announcement in active_announcements(event)
    ]
    return jsonify({
        "event": {"name": event.name} if event else None,
        "queues": public_queues,
        "announcements": announcements,
    })


@app.get("/api/display")
def display_api():
    """Return clean JSON stage snapshot formatted for venue TV screens with CORS enabled."""
    event = get_active_event()
    dashboard_queues = queue_snapshot(event)
    total_waiting = sum(len(q["waiting"]) for q in dashboard_queues.values())
    announcements = [
        {
            "id": a["id"],
            "message": a["message"],
            "posted_at": a.get("posted_at", ""),
            "created_at": a["created_at"],
        }
        for a in active_announcements(event)
    ]
    return jsonify({
        "event_name": event.name if event else "MakeQ",
        "total_waiting": total_waiting,
        "queues": dashboard_queues,
        "announcements": announcements,
        "timestamp": utc_isoformat(utc_now()),
    })


# ======================================================================
# CLI COMMANDS
# ======================================================================

@app.cli.command("init-db")
def init_db_command():
    """Upgrade database schema without inserting application data."""
    tables = set(inspect(db.engine).get_table_names())
    if tables and "alembic_version" not in tables:
        raise click.ClickException(
            "A pre-migration database was detected. Back it up, then run "
            "'flask --app app db stamp 0001_phase1_baseline' followed by "
            "'flask --app app db upgrade'."
        )
    upgrade()
    print("The MakeQ database schema is up to date.")


@app.cli.command("seed-demo")
def seed_demo_command():
    """Add idempotent development configuration."""
    init_db_and_seed(app)
    print("The MakeQ starter event and meal configuration are ready.")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug_enabled = os.getenv("MAKEQ_DEBUG", "").lower() in {"1", "true", "yes"}
    app.run(host="0.0.0.0", port=port, debug=debug_enabled)
