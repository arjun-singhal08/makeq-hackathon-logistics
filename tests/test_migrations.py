from pathlib import Path

from flask import Flask
from flask_migrate import Migrate, upgrade
from sqlalchemy import text

from models import db


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def test_food_migration_preserves_a_populated_phase1_database(tmp_path):
    database_path = tmp_path / "legacy-phase1.sqlite"
    migration_app = Flask("makeq-legacy-migration-test")
    migration_app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path.as_posix()}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(migration_app)
    Migrate(migration_app, db, directory=str(MIGRATIONS_DIR), render_as_batch=True)

    with migration_app.app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision="0001_phase1_baseline")
        now = "2026-08-29 00:00:00"
        db.session.execute(
            text(
                "INSERT INTO events (name, created_at, active) "
                "VALUES ('Preserved Hackathon', :now, 1)"
            ),
            {"now": now},
        )
        db.session.execute(
            text(
                "INSERT INTO queues "
                "(event_id, name, description, active, paused, created_at) "
                "VALUES (1, 'Normal', 'Legacy queue', 1, 0, :now)"
            ),
            {"now": now},
        )
        db.session.execute(
            text(
                "INSERT INTO tickets "
                "(public_id, event_id, queue_id, participant_name, ticket_type, "
                "status, created_at, queued_at, called_at, completed_at, updated_at, "
                "priority, notes) VALUES "
                "('MQ-PRESERVED', 1, 1, 'Existing Participant', 'INDIVIDUAL', "
                "'WAITING', :now, :now, NULL, NULL, :now, 0, '')"
            ),
            {"now": now},
        )
        db.session.commit()

        upgrade(directory=str(MIGRATIONS_DIR))
        preserved = db.session.execute(
            text(
                "SELECT public_id, quantity, collected_quantity, reservation_active, "
                "meal_service_id, meal_option_id, pickup_window_id, claim_token_hash "
                "FROM tickets WHERE public_id = 'MQ-PRESERVED'"
            )
        ).mappings().one()
        assert dict(preserved) == {
            "public_id": "MQ-PRESERVED",
            "quantity": 1,
            "collected_quantity": 0,
            "reservation_active": 0,
            "meal_service_id": None,
            "meal_option_id": None,
            "pickup_window_id": None,
            "claim_token_hash": None,
        }
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
        db.session.remove()
        db.engine.dispose()
