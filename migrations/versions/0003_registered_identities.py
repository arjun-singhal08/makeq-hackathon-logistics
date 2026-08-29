"""Registered participants, teams, and ticket identity links.

Revision ID: 0003_registered_identities
Revises: 0002_food_foundation
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_registered_identities"
down_revision = "0002_food_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_team_name_nonempty"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("event_id", "name", name="uq_team_event_name"),
    )
    op.create_index("ix_teams_event_id", "teams", ["event_id"])
    op.create_index("ix_teams_active", "teams", ["active"])

    op.create_table(
        "participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("organizer_identifier", sa.String(120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_participant_name_nonempty"),
        sa.CheckConstraint(
            "organizer_identifier IS NULL OR length(trim(organizer_identifier)) > 0",
            name="ck_participant_identifier_nonempty",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("event_id", "name", name="uq_participant_event_name"),
        sa.UniqueConstraint(
            "event_id", "organizer_identifier", name="uq_participant_event_identifier"
        ),
    )
    op.create_index("ix_participants_event_id", "participants", ["event_id"])
    op.create_index("ix_participants_team_id", "participants", ["team_id"])
    op.create_index("ix_participants_active", "participants", ["active"])

    with op.batch_alter_table("tickets") as batch_op:
        batch_op.add_column(sa.Column("participant_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_ticket_participant", "participants", ["participant_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_ticket_team", "teams", ["team_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_check_constraint(
            "ck_ticket_registered_identity_exclusive", "participant_id IS NULL OR team_id IS NULL"
        )
    op.create_index("ix_tickets_participant_id", "tickets", ["participant_id"])
    op.create_index("ix_tickets_team_id", "tickets", ["team_id"])


def downgrade():
    op.drop_index("ix_tickets_team_id", table_name="tickets")
    op.drop_index("ix_tickets_participant_id", table_name="tickets")
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.drop_constraint("ck_ticket_registered_identity_exclusive", type_="check")
        batch_op.drop_constraint("fk_ticket_team", type_="foreignkey")
        batch_op.drop_constraint("fk_ticket_participant", type_="foreignkey")
        batch_op.drop_column("team_id")
        batch_op.drop_column("participant_id")

    op.drop_index("ix_participants_active", table_name="participants")
    op.drop_index("ix_participants_team_id", table_name="participants")
    op.drop_index("ix_participants_event_id", table_name="participants")
    op.drop_table("participants")
    op.drop_index("ix_teams_active", table_name="teams")
    op.drop_index("ix_teams_event_id", table_name="teams")
    op.drop_table("teams")
