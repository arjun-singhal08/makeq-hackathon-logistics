import importlib
import sys
from datetime import date, time
from pathlib import Path

import pytest
from flask_migrate import upgrade

from food_service import create_food_ticket
from identity_service import (
    create_participant,
    create_team,
    set_participant_active,
    set_participant_team,
    set_team_active,
)
from models import (
    Event,
    MealOption,
    MealService,
    MealServiceStatus,
    PickupWindow,
    Queue,
    TeamMealPreference,
    TeamReservation,
    Ticket,
    db,
)
from team_preference_service import (
    TeamPreferenceError,
    create_team_reservation,
    get_reservable_team_members,
    preference_breakdown,
)


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def add_option(setup, *, name):
    option = MealOption(
        meal_service_id=setup.meal_service_id,
        queue_id=setup.queue_id,
        name=name,
        description=f"{name} food",
        planned_quantity=10,
        received_quantity=10,
        available_quantity=10,
        active=True,
    )
    db.session.add(option)
    db.session.commit()
    return option


def make_team(setup, *names):
    team = create_team(event_id=setup.event_id, name="Team Alpha")
    members = []
    for name in names:
        participant = create_participant(event_id=setup.event_id, name=name)
        set_participant_team(event_id=setup.event_id, participant_id=participant.id, team_id=team.id)
        members.append(participant)
    return team, members


def submit(setup, team, preferences):
    return create_team_reservation(
        event_id=setup.event_id,
        team_id=team.id,
        meal_service_id=setup.meal_service_id,
        pickup_window_id=setup.pickup_window_id,
        member_preferences=preferences,
    )


def test_team_can_load_its_active_registered_members(app, food_setup):
    setup = food_setup()
    team, members = make_team(setup, "Arjun", "Sarah")

    loaded_team, loaded_members = get_reservable_team_members(
        event_id=setup.event_id, team_id=team.id
    )
    assert loaded_team.id == team.id
    assert [member.id for member in loaded_members] == [member.id for member in members]


def test_team_member_preferences_are_relational_and_break_down_correctly(app, food_setup):
    setup = food_setup()
    normal = add_option(setup, name="Normal")
    halal = add_option(setup, name="Halal")
    team, members = make_team(setup, "Arjun", "Sarah", "John")

    reservation = submit(
        setup,
        team,
        [
            {"participant_id": members[0].id, "meal_option_id": normal.id},
            {"participant_id": members[1].id, "meal_option_id": setup.meal_option_id},
            {"participant_id": members[2].id, "meal_option_id": halal.id},
        ],
    )

    stored = db.session.get(TeamReservation, reservation.id)
    assert stored.team_id == team.id
    assert stored.quantity == 3
    assert db.session.scalar(
        db.select(db.func.count(TeamMealPreference.id)).where(
            TeamMealPreference.team_reservation_id == reservation.id
        )
    ) == 3
    assert preference_breakdown(stored) == {"Halal": 1, "Normal": 1, "Vegetarian": 1}
    assert db.session.get(MealOption, setup.meal_option_id).available_quantity == 20
    assert db.session.get(PickupWindow, setup.pickup_window_id).reserved_quantity == 0


def test_team_preference_breakdown_counts_repeated_options(app, food_setup):
    setup = food_setup()
    team, members = make_team(setup, "Arjun", "Sarah")
    reservation = submit(
        setup,
        team,
        [
            {"participant_id": members[0].id, "meal_option_id": setup.meal_option_id},
            {"participant_id": members[1].id, "meal_option_id": setup.meal_option_id},
        ],
    )
    assert preference_breakdown(reservation) == {"Vegetarian": 2}


@pytest.mark.parametrize("kind", ["non_member", "other_event", "inactive_member"])
def test_invalid_member_identities_are_rejected(app, food_setup, kind):
    setup = food_setup()
    team, members = make_team(setup, "Arjun")
    if kind == "non_member":
        invalid = create_participant(event_id=setup.event_id, name="Not On Team")
    elif kind == "other_event":
        other = food_setup()
        invalid = create_participant(event_id=other.event_id, name="Other Event Person")
    else:
        invalid = members[0]
        active_member = create_participant(event_id=setup.event_id, name="Still Active")
        set_participant_team(
            event_id=setup.event_id, participant_id=active_member.id, team_id=team.id
        )
        set_participant_active(event_id=setup.event_id, participant_id=invalid.id, active=False)

    with pytest.raises(TeamPreferenceError) as captured:
        submit(
            setup,
            team,
            [{"participant_id": invalid.id, "meal_option_id": setup.meal_option_id}],
        )
    assert captured.value.code == "INVALID_MEMBER_SET"


def test_inactive_team_and_duplicate_member_are_rejected(app, food_setup):
    setup = food_setup()
    team, members = make_team(setup, "Arjun")
    set_team_active(event_id=setup.event_id, team_id=team.id, active=False)
    with pytest.raises(TeamPreferenceError) as inactive:
        submit(
            setup,
            team,
            [{"participant_id": members[0].id, "meal_option_id": setup.meal_option_id}],
        )
    assert inactive.value.code == "TEAM_INACTIVE"

    set_team_active(event_id=setup.event_id, team_id=team.id, active=True)
    with pytest.raises(TeamPreferenceError) as duplicate:
        submit(
            setup,
            team,
            [
                {"participant_id": members[0].id, "meal_option_id": setup.meal_option_id},
                {"participant_id": members[0].id, "meal_option_id": setup.meal_option_id},
            ],
        )
    assert duplicate.value.code == "DUPLICATE_MEMBER"


def test_empty_team_and_invalid_meal_option_are_rejected(app, food_setup):
    setup = food_setup()
    empty_team = create_team(event_id=setup.event_id, name="Empty Team")
    with pytest.raises(TeamPreferenceError) as empty:
        submit(setup, empty_team, [])
    assert empty.value.code == "TEAM_EMPTY"

    team, members = make_team(setup, "Arjun")
    other = food_setup()
    with pytest.raises(TeamPreferenceError) as invalid_option:
        submit(
            setup,
            team,
            [{"participant_id": members[0].id, "meal_option_id": other.meal_option_id}],
        )
    assert invalid_option.value.code == "INVALID_MEAL_OPTION"


def test_individual_food_reservation_remains_independent(app, food_setup):
    setup = food_setup(option_quantity=5, window_capacity=5)
    result = create_food_ticket(
        event_id=setup.event_id,
        meal_service_id=setup.meal_service_id,
        meal_option_id=setup.meal_option_id,
        pickup_window_id=setup.pickup_window_id,
        participant_name="Individual Alex",
        ticket_type="INDIVIDUAL",
        quantity=2,
    )
    ticket = db.session.get(Ticket, result.ticket_id)
    assert ticket.quantity == 2
    assert db.session.get(MealOption, setup.meal_option_id).allocated_quantity == 2
    assert db.session.scalar(db.select(db.func.count(TeamReservation.id))) == 0


def test_browser_submitted_unknown_member_is_rejected_by_register_route(database_uri, monkeypatch):
    monkeypatch.setenv("MAKEQ_DATABASE_URL", database_uri)
    monkeypatch.setenv("MAKEQ_SECRET_KEY", "team-preference-route-test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    app_module.app.config["TESTING"] = True
    try:
        with app_module.app.app_context():
            upgrade(directory=str(MIGRATIONS_DIR))
            event = Event(name="Team Preference Route Event", active=True)
            db.session.add(event)
            db.session.flush()
            queue = Queue(event_id=event.id, name="Normal", description="", active=True)
            meal = MealService(
                event_id=event.id,
                name="Dinner",
                service_date=date(2026, 8, 29),
                start_time=time(18, 30),
                end_time=time(19, 30),
                location="Hall",
                status=MealServiceStatus.OPEN,
            )
            db.session.add_all([queue, meal])
            db.session.flush()
            option = MealOption(
                meal_service_id=meal.id,
                queue_id=queue.id,
                name="Normal",
                description="",
                planned_quantity=5,
                received_quantity=5,
                available_quantity=5,
                active=True,
            )
            window = PickupWindow(
                meal_service_id=meal.id,
                start_time=time(18, 30),
                end_time=time(18, 45),
                capacity=5,
                active=True,
            )
            db.session.add_all([option, window])
            db.session.commit()
            team = create_team(event_id=event.id, name="Route Team")
            member = create_participant(event_id=event.id, name="Valid Member", team_id=team.id)
            individual = create_participant(event_id=event.id, name="Independent Person")
            meal_id, window_id, option_id = meal.id, window.id, option.id
            team_id, member_id = team.id, member.id
            individual_id = individual.id

        client = app_module.app.test_client()
        page = client.get("/")
        assert page.status_code == 200
        assert b"Valid Member" in page.data
        assert b"Team food preferences" in page.data
        response = client.post(
            "/register",
            data={
                "meal_service_id": str(meal_id),
                "pickup_window_id": str(window_id),
                "registered_identity_id": f"team:{team_id}",
                f"member_option_{team_id}_{member_id}": str(option_id),
                f"member_option_{team_id}_99999": str(option_id),
            },
        )
        assert response.status_code == 302
        with app_module.app.app_context():
            assert db.session.scalar(db.select(db.func.count(TeamReservation.id))) == 0
        individual_response = client.post(
            "/register",
            data={
                "meal_service_id": str(meal_id),
                "meal_option_id": str(option_id),
                "pickup_window_id": str(window_id),
                "registered_identity_id": f"participant:{individual_id}",
                "quantity": "1",
            },
        )
        assert individual_response.status_code == 302
        with app_module.app.app_context():
            ticket = db.session.execute(
                db.select(Ticket).where(Ticket.participant_id == individual_id)
            ).scalar_one()
            assert ticket.participant_name == "Independent Person"
    finally:
        with app_module.app.app_context():
            db.session.remove()
            db.engine.dispose()
        sys.modules.pop("app", None)
