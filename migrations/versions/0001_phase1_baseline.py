"""Phase 1 persistent queue baseline.

Revision ID: 0001_phase1_baseline
Revises: None
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_phase1_baseline"
down_revision = None
branch_labels = None
depends_on = None


ticket_status = sa.Enum(
    "WAITING",
    "CALLED",
    "IN_SERVICE",
    "COMPLETED",
    "NO_SHOW",
    "CANCELLED",
    name="ticket_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)


def upgrade():
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("name", name="uq_events_name"),
    )
    op.create_index("ix_events_active", "events", ["active"])

    op.create_table(
        "queues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("event_id", "name", name="uq_queue_event_name"),
    )
    op.create_index("ix_queues_active", "queues", ["active"])
    op.create_index("ix_queues_event_id", "queues", ["event_id"])

    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(24), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        sa.Column("participant_name", sa.String(120), nullable=False),
        sa.Column("ticket_type", sa.String(20), nullable=False),
        sa.Column("status", ticket_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["queue_id"], ["queues.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_tickets_event_id", "tickets", ["event_id"])
    op.create_index("ix_tickets_public_id", "tickets", ["public_id"], unique=True)
    op.create_index("ix_tickets_queue_id", "tickets", ["queue_id"])
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_ticket_queue_status", "tickets", ["queue_id", "status"])
    op.create_index(
        "ix_ticket_queue_order",
        "tickets",
        ["queue_id", "priority", "queued_at", "id"],
    )
    op.create_index(
        "uq_ticket_active_per_queue",
        "tickets",
        ["queue_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('CALLED', 'IN_SERVICE')"),
        postgresql_where=sa.text("status IN ('CALLED', 'IN_SERVICE')"),
    )

    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_announcements_event_id", "announcements", ["event_id"])


def downgrade():
    op.drop_index("ix_announcements_event_id", table_name="announcements")
    op.drop_table("announcements")
    op.drop_index("uq_ticket_active_per_queue", table_name="tickets")
    op.drop_index("ix_ticket_queue_order", table_name="tickets")
    op.drop_index("ix_ticket_queue_status", table_name="tickets")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_index("ix_tickets_queue_id", table_name="tickets")
    op.drop_index("ix_tickets_public_id", table_name="tickets")
    op.drop_index("ix_tickets_event_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_index("ix_queues_event_id", table_name="queues")
    op.drop_index("ix_queues_active", table_name="queues")
    op.drop_table("queues")
    op.drop_index("ix_events_active", table_name="events")
    op.drop_table("events")
