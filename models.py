import sqlite3
from datetime import datetime, time, timezone
from enum import Enum

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError


db = SQLAlchemy()

DEFAULT_EVENT_NAME = "MakeQ Demo Hackathon"
INITIAL_QUEUES = (
    ("Normal", "Standard meal collection"),
    ("Halal", "Halal meal collection"),
    ("Vegetarian", "Vegetarian meal collection"),
    ("Vegan", "Vegan meal collection"),
)
STARTER_OPTIONS = (
    ("Standard", "Normal"),
    ("Halal", "Halal"),
    ("Vegetarian", "Vegetarian"),
    ("Vegan", "Vegan"),
)


def utc_now():
    return datetime.now(timezone.utc)


@sqlalchemy_event.listens_for(Engine, "connect")
def configure_sqlite_connection(dbapi_connection, _connection_record):
    """Keep SQLite integrity and contention behavior close to PostgreSQL."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


class TicketStatus(str, Enum):
    WAITING = "WAITING"
    CALLED = "CALLED"
    IN_SERVICE = "IN_SERVICE"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"
    CANCELLED = "CANCELLED"


class MealServiceStatus(str, Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


class InventoryMovementType(str, Enum):
    RECEIVED = "RECEIVED"
    ALLOCATED = "ALLOCATED"
    RELEASED = "RELEASED"
    COLLECTED = "COLLECTED"
    WASTED = "WASTED"
    ADJUSTED = "ADJUSTED"


class Event(db.Model):
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    queues = db.relationship(
        "Queue", back_populates="event", cascade="all, delete-orphan", passive_deletes=True
    )
    tickets = db.relationship(
        "Ticket", back_populates="event", cascade="all, delete-orphan", passive_deletes=True
    )
    announcements = db.relationship(
        "Announcement",
        back_populates="event",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    meal_services = db.relationship(
        "MealService",
        back_populates="event",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    participants = db.relationship(
        "Participant",
        back_populates="event",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    teams = db.relationship(
        "Team",
        back_populates="event",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    team_reservations = db.relationship(
        "TeamReservation",
        back_populates="event",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Queue(db.Model):
    """Operational queue retained as the adapter for the current MakeQ UI."""

    __tablename__ = "queues"
    __table_args__ = (
        db.UniqueConstraint("event_id", "name", name="uq_queue_event_name"),
        db.Index("uq_queue_id_event", "id", "event_id", unique=True),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(240), nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    paused = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    event = db.relationship("Event", back_populates="queues")
    tickets = db.relationship(
        "Ticket",
        back_populates="queue",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="Ticket.queue_id",
    )
    meal_options = db.relationship("MealOption", back_populates="queue")


class Team(db.Model):
    __tablename__ = "teams"
    __table_args__ = (
        db.UniqueConstraint("event_id", "name", name="uq_team_event_name"),
        db.CheckConstraint("length(trim(name)) > 0", name="ck_team_name_nonempty"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    event = db.relationship("Event", back_populates="teams")
    members = db.relationship("Participant", back_populates="team")
    tickets = db.relationship("Ticket", back_populates="team")
    reservations = db.relationship("TeamReservation", back_populates="team")


class Participant(db.Model):
    __tablename__ = "participants"
    __table_args__ = (
        db.UniqueConstraint("event_id", "name", name="uq_participant_event_name"),
        db.UniqueConstraint(
            "event_id", "organizer_identifier", name="uq_participant_event_identifier"
        ),
        db.CheckConstraint("length(trim(name)) > 0", name="ck_participant_name_nonempty"),
        db.CheckConstraint(
            "organizer_identifier IS NULL OR length(trim(organizer_identifier)) > 0",
            name="ck_participant_identifier_nonempty",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id = db.Column(
        db.Integer, db.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name = db.Column(db.String(120), nullable=False)
    organizer_identifier = db.Column(db.String(120), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    event = db.relationship("Event", back_populates="participants")
    team = db.relationship("Team", back_populates="members")
    tickets = db.relationship("Ticket", back_populates="participant")
    team_meal_preferences = db.relationship(
        "TeamMealPreference", back_populates="participant"
    )


class MealService(db.Model):
    __tablename__ = "meal_services"
    __table_args__ = (
        db.UniqueConstraint(
            "event_id", "service_date", "name", name="uq_meal_service_event_date_name"
        ),
        db.UniqueConstraint("id", "event_id", name="uq_meal_service_id_event"),
        db.CheckConstraint("end_time > start_time", name="ck_meal_service_time_order"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = db.Column(db.String(120), nullable=False)
    service_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    location = db.Column(db.String(160), nullable=False, default="")
    status = db.Column(
        db.Enum(
            MealServiceStatus,
            name="meal_service_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=MealServiceStatus.DRAFT,
        index=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    event = db.relationship("Event", back_populates="meal_services")
    options = db.relationship(
        "MealOption",
        back_populates="meal_service",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    pickup_windows = db.relationship(
        "PickupWindow",
        back_populates="meal_service",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tickets = db.relationship("Ticket", back_populates="meal_service", viewonly=True)
    team_reservations = db.relationship("TeamReservation", back_populates="meal_service")


class MealOption(db.Model):
    __tablename__ = "meal_options"
    __table_args__ = (
        db.UniqueConstraint("meal_service_id", "name", name="uq_meal_option_service_name"),
        db.UniqueConstraint(
            "id", "meal_service_id", "queue_id", name="uq_meal_option_ticket_scope"
        ),
        db.CheckConstraint("planned_quantity >= 0", name="ck_meal_option_planned_nonnegative"),
        db.CheckConstraint("received_quantity >= 0", name="ck_meal_option_received_nonnegative"),
        db.CheckConstraint("available_quantity >= 0", name="ck_meal_option_available_nonnegative"),
        db.CheckConstraint("allocated_quantity >= 0", name="ck_meal_option_allocated_nonnegative"),
        db.CheckConstraint("collected_quantity >= 0", name="ck_meal_option_collected_nonnegative"),
        db.CheckConstraint("released_quantity >= 0", name="ck_meal_option_released_nonnegative"),
        db.CheckConstraint("wasted_quantity >= 0", name="ck_meal_option_wasted_nonnegative"),
        db.CheckConstraint(
            "received_quantity + adjusted_quantity >= 0",
            name="ck_meal_option_adjusted_stock_nonnegative",
        ),
        db.CheckConstraint(
            "available_quantity + allocated_quantity + collected_quantity + "
            "wasted_quantity = received_quantity + adjusted_quantity",
            name="ck_meal_option_inventory_balance",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    meal_service_id = db.Column(
        db.Integer,
        db.ForeignKey("meal_services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    queue_id = db.Column(
        db.Integer, db.ForeignKey("queues.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(240), nullable=False, default="")
    planned_quantity = db.Column(db.Integer, nullable=False, default=0)
    received_quantity = db.Column(db.Integer, nullable=False, default=0)
    adjusted_quantity = db.Column(db.Integer, nullable=False, default=0)
    available_quantity = db.Column(db.Integer, nullable=False, default=0)
    allocated_quantity = db.Column(db.Integer, nullable=False, default=0)
    collected_quantity = db.Column(db.Integer, nullable=False, default=0)
    released_quantity = db.Column(db.Integer, nullable=False, default=0)
    wasted_quantity = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    meal_service = db.relationship("MealService", back_populates="options")
    queue = db.relationship("Queue", back_populates="meal_options")
    tickets = db.relationship("Ticket", back_populates="meal_option", viewonly=True)
    inventory_movements = db.relationship(
        "InventoryMovement",
        back_populates="meal_option",
        passive_deletes="all",
    )
    team_meal_preferences = db.relationship(
        "TeamMealPreference", back_populates="meal_option"
    )


class PickupWindow(db.Model):
    __tablename__ = "pickup_windows"
    __table_args__ = (
        db.UniqueConstraint(
            "meal_service_id", "start_time", "end_time", name="uq_pickup_window_service_time"
        ),
        db.UniqueConstraint("id", "meal_service_id", name="uq_pickup_window_id_service"),
        db.CheckConstraint("end_time > start_time", name="ck_pickup_window_time_order"),
        db.CheckConstraint("capacity >= 0", name="ck_pickup_window_capacity_nonnegative"),
        db.CheckConstraint(
            "reserved_quantity >= 0", name="ck_pickup_window_reserved_nonnegative"
        ),
        db.CheckConstraint(
            "collected_quantity >= 0", name="ck_pickup_window_collected_nonnegative"
        ),
        db.CheckConstraint(
            "reserved_quantity + collected_quantity <= capacity",
            name="ck_pickup_window_capacity_limit",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    meal_service_id = db.Column(
        db.Integer,
        db.ForeignKey("meal_services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    reserved_quantity = db.Column(db.Integer, nullable=False, default=0)
    collected_quantity = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    meal_service = db.relationship("MealService", back_populates="pickup_windows")
    tickets = db.relationship("Ticket", back_populates="pickup_window", viewonly=True)
    team_reservations = db.relationship("TeamReservation", back_populates="pickup_window")


class TeamReservation(db.Model):
    """One coordinated team preference submission; it does not allocate food yet."""

    __tablename__ = "team_reservations"
    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_team_reservation_quantity_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id = db.Column(
        db.Integer, db.ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    meal_service_id = db.Column(
        db.Integer,
        db.ForeignKey("meal_services.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    pickup_window_id = db.Column(
        db.Integer,
        db.ForeignKey("pickup_windows.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    event = db.relationship("Event", back_populates="team_reservations")
    team = db.relationship("Team", back_populates="reservations")
    meal_service = db.relationship("MealService", back_populates="team_reservations")
    pickup_window = db.relationship("PickupWindow", back_populates="team_reservations")
    preferences = db.relationship(
        "TeamMealPreference",
        back_populates="team_reservation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TeamMealPreference(db.Model):
    __tablename__ = "team_meal_preferences"
    __table_args__ = (
        db.UniqueConstraint(
            "team_reservation_id", "participant_id", name="uq_team_reservation_participant"
        ),
        db.CheckConstraint("quantity > 0", name="ck_team_meal_preference_quantity_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    team_reservation_id = db.Column(
        db.Integer,
        db.ForeignKey("team_reservations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    participant_id = db.Column(
        db.Integer,
        db.ForeignKey("participants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    meal_option_id = db.Column(
        db.Integer,
        db.ForeignKey("meal_options.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    team_reservation = db.relationship("TeamReservation", back_populates="preferences")
    participant = db.relationship("Participant", back_populates="team_meal_preferences")
    meal_option = db.relationship("MealOption", back_populates="team_meal_preferences")


class Ticket(db.Model):
    __tablename__ = "tickets"
    __table_args__ = (
        db.Index("ix_ticket_queue_status", "queue_id", "status"),
        db.Index("ix_ticket_queue_order", "queue_id", "priority", "queued_at", "id"),
        db.Index(
            "uq_ticket_active_per_queue",
            "queue_id",
            unique=True,
            sqlite_where=db.text("status IN ('CALLED', 'IN_SERVICE')"),
            postgresql_where=db.text("status IN ('CALLED', 'IN_SERVICE')"),
        ),
        db.CheckConstraint("quantity > 0", name="ck_ticket_quantity_positive"),
        db.CheckConstraint(
            "collected_quantity >= 0 AND collected_quantity <= quantity",
            name="ck_ticket_collected_quantity_valid",
        ),
        db.CheckConstraint(
            "(meal_service_id IS NULL AND meal_option_id IS NULL AND pickup_window_id IS NULL) "
            "OR (meal_service_id IS NOT NULL AND meal_option_id IS NOT NULL AND "
            "pickup_window_id IS NOT NULL AND claim_token_hash IS NOT NULL)",
            name="ck_ticket_food_links_complete",
        ),
        db.CheckConstraint(
            "NOT reservation_active OR (meal_service_id IS NOT NULL AND "
            "status IN ('WAITING', 'CALLED', 'IN_SERVICE'))",
            name="ck_ticket_active_reservation_status",
        ),
        db.CheckConstraint(
            "participant_id IS NULL OR team_id IS NULL",
            name="ck_ticket_registered_identity_exclusive",
        ),
        db.ForeignKeyConstraint(
            ["queue_id", "event_id"],
            ["queues.id", "queues.event_id"],
            name="fk_ticket_queue_event",
            ondelete="CASCADE",
        ),
        db.ForeignKeyConstraint(
            ["meal_service_id", "event_id"],
            ["meal_services.id", "meal_services.event_id"],
            name="fk_ticket_meal_service_event",
            ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["meal_option_id", "meal_service_id", "queue_id"],
            ["meal_options.id", "meal_options.meal_service_id", "meal_options.queue_id"],
            name="fk_ticket_meal_option_scope",
            ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["pickup_window_id", "meal_service_id"],
            ["pickup_windows.id", "pickup_windows.meal_service_id"],
            name="fk_ticket_pickup_window_service",
            ondelete="RESTRICT",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(24), nullable=False, unique=True, index=True)
    claim_token_hash = db.Column(db.String(64), unique=True, index=True)
    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    queue_id = db.Column(
        db.Integer, db.ForeignKey("queues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    participant_id = db.Column(
        db.Integer,
        db.ForeignKey("participants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    team_id = db.Column(
        db.Integer,
        db.ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    meal_service_id = db.Column(
        db.Integer, index=True
    )
    meal_option_id = db.Column(
        db.Integer, index=True
    )
    pickup_window_id = db.Column(
        db.Integer, index=True
    )
    participant_name = db.Column(db.String(120), nullable=False)
    ticket_type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    collected_quantity = db.Column(db.Integer, nullable=False, default=0)
    reservation_active = db.Column(db.Boolean, nullable=False, default=False, index=True)
    status = db.Column(
        db.Enum(
            TicketStatus,
            name="ticket_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=TicketStatus.WAITING,
        index=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    queued_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    called_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    cancelled_at = db.Column(db.DateTime(timezone=True))
    no_show_at = db.Column(db.DateTime(timezone=True))
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    priority = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text, nullable=False, default="")

    event = db.relationship("Event", back_populates="tickets")
    queue = db.relationship("Queue", back_populates="tickets", foreign_keys=[queue_id])
    participant = db.relationship("Participant", back_populates="tickets")
    team = db.relationship("Team", back_populates="tickets")
    meal_service = db.relationship("MealService", back_populates="tickets", viewonly=True)
    meal_option = db.relationship("MealOption", back_populates="tickets", viewonly=True)
    pickup_window = db.relationship("PickupWindow", back_populates="tickets", viewonly=True)
    inventory_movements = db.relationship(
        "InventoryMovement", back_populates="ticket", passive_deletes="all"
    )


class InventoryMovement(db.Model):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        db.CheckConstraint("quantity != 0", name="ck_inventory_movement_quantity_nonzero"),
        db.CheckConstraint(
            "movement_type = 'ADJUSTED' OR quantity > 0",
            name="ck_inventory_movement_positive_magnitude",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    meal_option_id = db.Column(
        db.Integer,
        db.ForeignKey("meal_options.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("tickets.id", ondelete="RESTRICT"), index=True
    )
    movement_type = db.Column(
        db.Enum(
            InventoryMovementType,
            name="inventory_movement_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        index=True,
    )
    quantity = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(240), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    meal_option = db.relationship("MealOption", back_populates="inventory_movements")
    ticket = db.relationship("Ticket", back_populates="inventory_movements")


class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message = db.Column(db.String(240), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = db.Column(db.DateTime(timezone=True))

    event = db.relationship("Event", back_populates="announcements")


def initialize_database():
    """Idempotently add local starter configuration; migrations own the schema."""
    try:
        if Event.query.first() is not None:
            return False
    except Exception:
        return False

    event = db.session.execute(
        db.select(Event).where(Event.name == DEFAULT_EVENT_NAME)
    ).scalar_one_or_none()
    if event is None:
        # Preserve the pre-food demo event without treating an arbitrary real
        # event as a target for development seed data.
        event = db.session.execute(
            db.select(Event).where(Event.name == "MakeQ Demo Event")
        ).scalar_one_or_none()
    created = event is None
    if event is None:
        event = Event(name=DEFAULT_EVENT_NAME, active=True)
        db.session.add(event)
        db.session.flush()

    queues_by_name = {
        queue.name: queue
        for queue in db.session.execute(
            db.select(Queue).where(Queue.event_id == event.id)
        ).scalars()
    }
    for name, description in INITIAL_QUEUES:
        if name not in queues_by_name:
            queue = Queue(event_id=event.id, name=name, description=description, active=True)
            db.session.add(queue)
            db.session.flush()
            queues_by_name[name] = queue

    meal = db.session.execute(
        db.select(MealService)
        .where(MealService.event_id == event.id, MealService.name == "Dinner")
        .order_by(MealService.service_date, MealService.id)
        .limit(1)
    ).scalar_one_or_none()
    if meal is None:
        meal = MealService(
            event_id=event.id,
            name="Dinner",
            service_date=utc_now().date(),
            start_time=time(18, 30),
            end_time=time(19, 30),
            location="Main food collection point",
            status=MealServiceStatus.OPEN,
        )
        db.session.add(meal)
        db.session.flush()

    existing_options = {
        option.name
        for option in db.session.execute(
            db.select(MealOption).where(MealOption.meal_service_id == meal.id)
        ).scalars()
    }
    for option_name, queue_name in STARTER_OPTIONS:
        if option_name in existing_options:
            continue
        option = MealOption(
            meal_service_id=meal.id,
            queue_id=queues_by_name[queue_name].id,
            name=option_name,
            description=f"{option_name} dinner option",
            planned_quantity=100,
            received_quantity=100,
            available_quantity=100,
            active=True,
        )
        db.session.add(option)
        db.session.flush()
        db.session.add(
            InventoryMovement(
                meal_option_id=option.id,
                movement_type=InventoryMovementType.RECEIVED,
                quantity=100,
                reason="Local starter inventory",
            )
        )

    first_window = db.session.execute(
        db.select(PickupWindow.id)
        .where(PickupWindow.meal_service_id == meal.id)
        .limit(1)
    ).scalar_one_or_none()
    if first_window is None:
        for start_time, end_time in (
            (time(18, 30), time(18, 45)),
            (time(18, 45), time(19, 0)),
            (time(19, 0), time(19, 15)),
        ):
            db.session.add(
                PickupWindow(
                    meal_service_id=meal.id,
                    start_time=start_time,
                    end_time=end_time,
                    capacity=100,
                    active=True,
                )
            )

    try:
        db.session.commit()
        return created
    except Exception:
        # Another setup process may have inserted the same starter records.
        db.session.rollback()
        return False
