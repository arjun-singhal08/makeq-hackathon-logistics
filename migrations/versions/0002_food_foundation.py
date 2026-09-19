"""Food service, pickup capacity, inventory, and food ticket foundation.

Revision ID: 0002_food_foundation
Revises: 0001_phase1_baseline
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_food_foundation"
down_revision = "0001_phase1_baseline"
branch_labels = None
depends_on = None


meal_service_status = sa.Enum(
    "DRAFT",
    "OPEN",
    "PAUSED",
    "CLOSED",
    name="meal_service_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
inventory_movement_type = sa.Enum(
    "RECEIVED",
    "ALLOCATED",
    "RELEASED",
    "COLLECTED",
    "WASTED",
    "ADJUSTED",
    name="inventory_movement_type",
    native_enum=False,
    create_constraint=True,
    length=20,
)


def upgrade():
    conn = op.get_bind()
    tables = sa.inspect(conn).get_table_names()
    if "meal_services" in tables:
        return

    op.create_index("uq_queue_id_event", "queues", ["id", "event_id"], unique=True)

    op.create_table(
        "meal_services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("location", sa.String(160), nullable=False),
        sa.Column("status", meal_service_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_time > start_time", name="ck_meal_service_time_order"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "event_id", "service_date", "name", name="uq_meal_service_event_date_name"
        ),
        sa.UniqueConstraint("id", "event_id", name="uq_meal_service_id_event"),
    )
    op.create_index("ix_meal_services_event_id", "meal_services", ["event_id"])
    op.create_index("ix_meal_services_status", "meal_services", ["status"])

    op.create_table(
        "meal_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("meal_service_id", sa.Integer(), nullable=False),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("planned_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("adjusted_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allocated_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("released_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wasted_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("planned_quantity >= 0", name="ck_meal_option_planned_nonnegative"),
        sa.CheckConstraint("received_quantity >= 0", name="ck_meal_option_received_nonnegative"),
        sa.CheckConstraint("available_quantity >= 0", name="ck_meal_option_available_nonnegative"),
        sa.CheckConstraint("allocated_quantity >= 0", name="ck_meal_option_allocated_nonnegative"),
        sa.CheckConstraint("collected_quantity >= 0", name="ck_meal_option_collected_nonnegative"),
        sa.CheckConstraint("released_quantity >= 0", name="ck_meal_option_released_nonnegative"),
        sa.CheckConstraint("wasted_quantity >= 0", name="ck_meal_option_wasted_nonnegative"),
        sa.CheckConstraint(
            "received_quantity + adjusted_quantity >= 0",
            name="ck_meal_option_adjusted_stock_nonnegative",
        ),
        sa.CheckConstraint(
            "available_quantity + allocated_quantity + collected_quantity + "
            "wasted_quantity = received_quantity + adjusted_quantity",
            name="ck_meal_option_inventory_balance",
        ),
        sa.ForeignKeyConstraint(
            ["meal_service_id"], ["meal_services.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["queue_id"], ["queues.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("meal_service_id", "name", name="uq_meal_option_service_name"),
        sa.UniqueConstraint(
            "id", "meal_service_id", "queue_id", name="uq_meal_option_ticket_scope"
        ),
    )
    op.create_index("ix_meal_options_active", "meal_options", ["active"])
    op.create_index("ix_meal_options_meal_service_id", "meal_options", ["meal_service_id"])
    op.create_index("ix_meal_options_queue_id", "meal_options", ["queue_id"])

    op.create_table(
        "pickup_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("meal_service_id", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_time > start_time", name="ck_pickup_window_time_order"),
        sa.CheckConstraint("capacity >= 0", name="ck_pickup_window_capacity_nonnegative"),
        sa.CheckConstraint(
            "reserved_quantity >= 0", name="ck_pickup_window_reserved_nonnegative"
        ),
        sa.CheckConstraint(
            "collected_quantity >= 0", name="ck_pickup_window_collected_nonnegative"
        ),
        sa.CheckConstraint(
            "reserved_quantity + collected_quantity <= capacity",
            name="ck_pickup_window_capacity_limit",
        ),
        sa.ForeignKeyConstraint(
            ["meal_service_id"], ["meal_services.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "meal_service_id", "start_time", "end_time", name="uq_pickup_window_service_time"
        ),
        sa.UniqueConstraint("id", "meal_service_id", name="uq_pickup_window_id_service"),
    )
    op.create_index("ix_pickup_windows_active", "pickup_windows", ["active"])
    op.create_index("ix_pickup_windows_meal_service_id", "pickup_windows", ["meal_service_id"])

    with op.batch_alter_table("tickets") as batch_op:
        batch_op.add_column(sa.Column("claim_token_hash", sa.String(64)))
        batch_op.add_column(sa.Column("meal_service_id", sa.Integer()))
        batch_op.add_column(sa.Column("meal_option_id", sa.Integer()))
        batch_op.add_column(sa.Column("pickup_window_id", sa.Integer()))
        batch_op.add_column(
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("collected_quantity", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "reservation_active", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("no_show_at", sa.DateTime(timezone=True)))
        batch_op.create_foreign_key(
            "fk_ticket_queue_event",
            "queues",
            ["queue_id", "event_id"],
            ["id", "event_id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_ticket_meal_service_event",
            "meal_services",
            ["meal_service_id", "event_id"],
            ["id", "event_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_ticket_meal_option_scope",
            "meal_options",
            ["meal_option_id", "meal_service_id", "queue_id"],
            ["id", "meal_service_id", "queue_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_ticket_pickup_window_service",
            "pickup_windows",
            ["pickup_window_id", "meal_service_id"],
            ["id", "meal_service_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint("ck_ticket_quantity_positive", "quantity > 0")
        batch_op.create_check_constraint(
            "ck_ticket_collected_quantity_valid",
            "collected_quantity >= 0 AND collected_quantity <= quantity",
        )
        batch_op.create_check_constraint(
            "ck_ticket_food_links_complete",
            "(meal_service_id IS NULL AND meal_option_id IS NULL AND pickup_window_id IS NULL) "
            "OR (meal_service_id IS NOT NULL AND meal_option_id IS NOT NULL AND "
            "pickup_window_id IS NOT NULL AND claim_token_hash IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_ticket_active_reservation_status",
            "NOT reservation_active OR (meal_service_id IS NOT NULL AND "
            "status IN ('WAITING', 'CALLED', 'IN_SERVICE'))",
        )
    op.create_index(
        "ix_tickets_claim_token_hash", "tickets", ["claim_token_hash"], unique=True
    )
    op.create_index("ix_tickets_meal_service_id", "tickets", ["meal_service_id"])
    op.create_index("ix_tickets_meal_option_id", "tickets", ["meal_option_id"])
    op.create_index("ix_tickets_pickup_window_id", "tickets", ["pickup_window_id"])
    op.create_index("ix_tickets_reservation_active", "tickets", ["reservation_active"])

    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("meal_option_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer()),
        sa.Column("movement_type", inventory_movement_type, nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity != 0", name="ck_inventory_movement_quantity_nonzero"),
        sa.CheckConstraint(
            "movement_type = 'ADJUSTED' OR quantity > 0",
            name="ck_inventory_movement_positive_magnitude",
        ),
        sa.ForeignKeyConstraint(
            ["meal_option_id"], ["meal_options.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_inventory_movements_meal_option_id", "inventory_movements", ["meal_option_id"]
    )
    op.create_index(
        "ix_inventory_movements_movement_type", "inventory_movements", ["movement_type"]
    )
    op.create_index("ix_inventory_movements_ticket_id", "inventory_movements", ["ticket_id"])


def downgrade():
    op.drop_index("ix_inventory_movements_ticket_id", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_movement_type", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_meal_option_id", table_name="inventory_movements")
    op.drop_table("inventory_movements")

    op.drop_index("ix_tickets_reservation_active", table_name="tickets")
    op.drop_index("ix_tickets_pickup_window_id", table_name="tickets")
    op.drop_index("ix_tickets_meal_option_id", table_name="tickets")
    op.drop_index("ix_tickets_meal_service_id", table_name="tickets")
    op.drop_index("ix_tickets_claim_token_hash", table_name="tickets")
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.drop_constraint("ck_ticket_active_reservation_status", type_="check")
        batch_op.drop_constraint("ck_ticket_food_links_complete", type_="check")
        batch_op.drop_constraint("ck_ticket_collected_quantity_valid", type_="check")
        batch_op.drop_constraint("ck_ticket_quantity_positive", type_="check")
        batch_op.drop_constraint("fk_ticket_pickup_window_service", type_="foreignkey")
        batch_op.drop_constraint("fk_ticket_meal_option_scope", type_="foreignkey")
        batch_op.drop_constraint("fk_ticket_meal_service_event", type_="foreignkey")
        batch_op.drop_constraint("fk_ticket_queue_event", type_="foreignkey")
        batch_op.drop_column("no_show_at")
        batch_op.drop_column("cancelled_at")
        batch_op.drop_column("reservation_active")
        batch_op.drop_column("collected_quantity")
        batch_op.drop_column("quantity")
        batch_op.drop_column("pickup_window_id")
        batch_op.drop_column("meal_option_id")
        batch_op.drop_column("meal_service_id")
        batch_op.drop_column("claim_token_hash")

    op.drop_index("ix_pickup_windows_meal_service_id", table_name="pickup_windows")
    op.drop_index("ix_pickup_windows_active", table_name="pickup_windows")
    op.drop_table("pickup_windows")
    op.drop_index("ix_meal_options_queue_id", table_name="meal_options")
    op.drop_index("ix_meal_options_meal_service_id", table_name="meal_options")
    op.drop_index("ix_meal_options_active", table_name="meal_options")
    op.drop_table("meal_options")
    op.drop_index("ix_meal_services_status", table_name="meal_services")
    op.drop_index("ix_meal_services_event_id", table_name="meal_services")
    op.drop_table("meal_services")
    op.drop_index("uq_queue_id_event", table_name="queues")
