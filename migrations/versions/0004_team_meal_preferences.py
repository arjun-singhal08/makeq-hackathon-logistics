"""Coordinated team reservation preference sets.

Revision ID: 0004_team_meal_preferences
Revises: 0003_registered_identities
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_team_meal_preferences"
down_revision = "0003_registered_identities"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "team_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("meal_service_id", sa.Integer(), nullable=False),
        sa.Column("pickup_window_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_team_reservation_quantity_positive"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["meal_service_id"], ["meal_services.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pickup_window_id"], ["pickup_windows.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_team_reservations_event_id", "team_reservations", ["event_id"])
    op.create_index("ix_team_reservations_team_id", "team_reservations", ["team_id"])
    op.create_index(
        "ix_team_reservations_meal_service_id", "team_reservations", ["meal_service_id"]
    )
    op.create_index(
        "ix_team_reservations_pickup_window_id", "team_reservations", ["pickup_window_id"]
    )

    op.create_table(
        "team_meal_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_reservation_id", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("meal_option_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_team_meal_preference_quantity_positive"),
        sa.ForeignKeyConstraint(
            ["team_reservation_id"], ["team_reservations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["meal_option_id"], ["meal_options.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "team_reservation_id", "participant_id", name="uq_team_reservation_participant"
        ),
    )
    op.create_index(
        "ix_team_meal_preferences_team_reservation_id",
        "team_meal_preferences",
        ["team_reservation_id"],
    )
    op.create_index(
        "ix_team_meal_preferences_participant_id", "team_meal_preferences", ["participant_id"]
    )
    op.create_index(
        "ix_team_meal_preferences_meal_option_id", "team_meal_preferences", ["meal_option_id"]
    )


def downgrade():
    op.drop_index("ix_team_meal_preferences_meal_option_id", table_name="team_meal_preferences")
    op.drop_index("ix_team_meal_preferences_participant_id", table_name="team_meal_preferences")
    op.drop_index(
        "ix_team_meal_preferences_team_reservation_id", table_name="team_meal_preferences"
    )
    op.drop_table("team_meal_preferences")
    op.drop_index("ix_team_reservations_pickup_window_id", table_name="team_reservations")
    op.drop_index("ix_team_reservations_meal_service_id", table_name="team_reservations")
    op.drop_index("ix_team_reservations_team_id", table_name="team_reservations")
    op.drop_index("ix_team_reservations_event_id", table_name="team_reservations")
    op.drop_table("team_reservations")
