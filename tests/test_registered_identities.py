import importlib
import sys
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import Migrate, upgrade
from sqlalchemy import text

from food_service import FoodServiceError, create_food_ticket
from identity_service import (
    IdentityServiceError,
    create_participant,
    create_team,
    set_participant_active,
    set_participant_team,
    set_team_active,
)
from models import Participant, Team, Ticket, db


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def reserve_registered(setup, *, participant_id=None, team_id=None):
    return create_food_ticket(
        event_id=setup.event_id,
        meal_service_id=setup.meal_service_id,
        meal_option_id=setup.meal_option_id,
        pickup_window_id=setup.pickup_window_id,
        participant_name="Untrusted request text",
        ticket_type="TEAM" if team_id else "INDIVIDUAL",
        quantity=1,
        participant_id=participant_id,
        team_id=team_id,
    )


def test_create_participant(app, food_setup):
    setup = food_setup()
    participant = create_participant(
        event_id=setup.event_id, name="Alex Tan", organizer_identifier="HACK-001"
    )

    stored = db.session.get(Participant, participant.id)
    assert stored.name == "Alex Tan"
    assert stored.organizer_identifier == "HACK-001"
    assert stored.active is True
    assert stored.team_id is None


def test_create_team(app, food_setup):
    setup = food_setup()
    team = create_team(event_id=setup.event_id, name="Byte Builders")

    stored = db.session.get(Team, team.id)
    assert stored.name == "Byte Builders"
    assert stored.active is True


def test_add_and_remove_participant_from_team(app, food_setup):
    setup = food_setup()
    participant = create_participant(event_id=setup.event_id, name="Mina")
    team = create_team(event_id=setup.event_id, name="Night Owls")

    set_participant_team(event_id=setup.event_id, participant_id=participant.id, team_id=team.id)
    assert db.session.get(Participant, participant.id).team_id == team.id

    set_participant_team(event_id=setup.event_id, participant_id=participant.id)
    assert db.session.get(Participant, participant.id).team_id is None


def test_participant_cannot_belong_to_two_teams(app, food_setup):
    setup = food_setup()
    participant = create_participant(event_id=setup.event_id, name="Sam")
    first = create_team(event_id=setup.event_id, name="First Team")
    second = create_team(event_id=setup.event_id, name="Second Team")

    set_participant_team(event_id=setup.event_id, participant_id=participant.id, team_id=first.id)
    set_participant_team(event_id=setup.event_id, participant_id=participant.id, team_id=second.id)

    stored = db.session.get(Participant, participant.id)
    assert stored.team_id == second.id
    assert [member.id for member in db.session.get(Team, first.id).members] == []
    assert [member.id for member in db.session.get(Team, second.id).members] == [participant.id]


def test_duplicate_participant_identity_is_rejected(app, food_setup):
    setup = food_setup()
    create_participant(event_id=setup.event_id, name="Alex Tan", organizer_identifier="HACK-001")

    with pytest.raises(IdentityServiceError) as by_name:
        create_participant(event_id=setup.event_id, name="alex tan")
    with pytest.raises(IdentityServiceError) as by_identifier:
        create_participant(event_id=setup.event_id, name="Alex Two", organizer_identifier="hack-001")

    assert by_name.value.code == "DUPLICATE_PARTICIPANT"
    assert by_identifier.value.code == "DUPLICATE_PARTICIPANT"


def test_duplicate_team_name_is_rejected(app, food_setup):
    setup = food_setup()
    create_team(event_id=setup.event_id, name="Byte Builders")

    with pytest.raises(IdentityServiceError) as captured:
        create_team(event_id=setup.event_id, name="byte builders")
    assert captured.value.code == "DUPLICATE_TEAM"


def test_inactive_participant_cannot_be_newly_registered_for_food(app, food_setup):
    setup = food_setup()
    participant = create_participant(event_id=setup.event_id, name="Inactive Person")
    set_participant_active(event_id=setup.event_id, participant_id=participant.id, active=False)

    with pytest.raises(FoodServiceError) as captured:
        reserve_registered(setup, participant_id=participant.id)
    assert captured.value.code == "PARTICIPANT_INACTIVE"
    assert db.session.scalar(db.select(db.func.count(Ticket.id))) == 0


def test_inactive_team_cannot_be_newly_used_for_food(app, food_setup):
    setup = food_setup()
    team = create_team(event_id=setup.event_id, name="Inactive Team")
    set_team_active(event_id=setup.event_id, team_id=team.id, active=False)

    with pytest.raises(FoodServiceError) as captured:
        reserve_registered(setup, team_id=team.id)
    assert captured.value.code == "TEAM_INACTIVE"
    assert db.session.scalar(db.select(db.func.count(Ticket.id))) == 0


def test_different_events_can_reuse_participant_and_team_names(app, food_setup):
    first = food_setup()
    second = food_setup()

    first_participant = create_participant(event_id=first.event_id, name="Alex", organizer_identifier="1")
    second_participant = create_participant(event_id=second.event_id, name="Alex", organizer_identifier="1")
    first_team = create_team(event_id=first.event_id, name="Team Alpha")
    second_team = create_team(event_id=second.event_id, name="Team Alpha")

    assert first_participant.id != second_participant.id
    assert first_team.id != second_team.id


def test_legacy_ticket_stays_valid_and_registered_ticket_keeps_snapshot(app, food_setup):
    setup = food_setup(option_quantity=10, window_capacity=10)
    legacy = create_food_ticket(
        event_id=setup.event_id,
        meal_service_id=setup.meal_service_id,
        meal_option_id=setup.meal_option_id,
        pickup_window_id=setup.pickup_window_id,
        participant_name="Legacy Free Text",
        ticket_type="INDIVIDUAL",
        quantity=2,
    )
    participant = create_participant(event_id=setup.event_id, name="Registered Alex")
    registered = reserve_registered(setup, participant_id=participant.id)

    legacy_ticket = db.session.get(Ticket, legacy.ticket_id)
    registered_ticket = db.session.get(Ticket, registered.ticket_id)
    assert legacy_ticket.participant_id is None and legacy_ticket.team_id is None
    assert legacy_ticket.participant_name == "Legacy Free Text"
    assert registered_ticket.participant_id == participant.id
    assert registered_ticket.participant_name == "Registered Alex"


def test_invalid_identity_input_is_handled_safely(app, food_setup):
    setup = food_setup()
    with pytest.raises(IdentityServiceError):
        create_team(event_id=setup.event_id, name="   ")
    with pytest.raises(IdentityServiceError):
        create_participant(event_id=setup.event_id, name="   ")
    other = food_setup()
    other_team = create_team(event_id=other.event_id, name="Other Event Team")
    with pytest.raises(IdentityServiceError) as captured:
        create_participant(event_id=setup.event_id, name="Wrong Team", team_id=other_team.id)
    assert captured.value.code == "TEAM_NOT_FOUND"


def test_migration_from_food_foundation_preserves_existing_ticket(tmp_path):
    database_path = tmp_path / "food-foundation.sqlite"
    migration_app = Flask("makeq-registered-identity-migration-test")
    migration_app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path.as_posix()}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(migration_app)
    Migrate(migration_app, db, directory=str(MIGRATIONS_DIR), render_as_batch=True)

    with migration_app.app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision="0002_food_foundation")
        db.session.execute(
            text(
                "INSERT INTO events (name, created_at, active) VALUES "
                "('Migration Event', '2026-08-29 00:00:00', 1)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO queues (event_id, name, description, active, paused, created_at) VALUES "
                "(1, 'Normal', '', 1, 0, '2026-08-29 00:00:00')"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO tickets (public_id, event_id, queue_id, participant_name, ticket_type, "
                "status, created_at, queued_at, updated_at, priority, notes, quantity, "
                "collected_quantity, reservation_active) VALUES "
                "('MQ-LEGACY', 1, 1, 'Legacy', 'INDIVIDUAL', 'WAITING', "
                "'2026-08-29 00:00:00', '2026-08-29 00:00:00', '2026-08-29 00:00:00', "
                "0, '', 1, 0, 0)"
            )
        )
        db.session.commit()

        upgrade(directory=str(MIGRATIONS_DIR))
        row = db.session.execute(
            text(
                "SELECT public_id, participant_id, team_id FROM tickets WHERE public_id = 'MQ-LEGACY'"
            )
        ).mappings().one()
        assert dict(row) == {"public_id": "MQ-LEGACY", "participant_id": None, "team_id": None}
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
        db.session.remove()
        db.engine.dispose()


def test_admin_routes_render_and_manage_roster(database_uri, monkeypatch):
    monkeypatch.setenv("MAKEQ_DATABASE_URL", database_uri)
    monkeypatch.setenv("MAKEQ_SECRET_KEY", "registered-identity-route-test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    app_module.app.config["TESTING"] = True
    try:
        with app_module.app.app_context():
            upgrade(directory=str(MIGRATIONS_DIR))
            from models import Event

            event = Event(name="Roster Route Event", active=True)
            db.session.add(event)
            db.session.commit()

        client = app_module.app.test_client()
        with client.session_transaction() as session:
            session["is_admin"] = True
        assert client.get("/").status_code == 200
        assert client.post("/admin/teams", data={"name": "Route Team"}).status_code == 302
        assert (
            client.post(
                "/admin/participants",
                data={"name": "Route Person", "organizer_identifier": "R-1", "team_id": "1"},
            ).status_code
            == 302
        )
        with app_module.app.app_context():
            participant = db.session.execute(
                db.select(Participant).where(Participant.name == "Route Person")
            ).scalar_one()
            assert participant.team.name == "Route Team"
            assert client.post(
                f"/admin/participants/{participant.id}/team", data={"team_id": ""}
            ).status_code == 302
            assert db.session.get(Participant, participant.id).team_id is None
    finally:
        with app_module.app.app_context():
            db.session.remove()
            db.engine.dispose()
        sys.modules.pop("app", None)
