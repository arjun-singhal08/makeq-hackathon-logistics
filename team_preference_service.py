"""Validation and persistence for coordinated team meal preferences.

This module deliberately does not allocate inventory or create tickets.  Those
operations remain in ``food_service`` until a later stage can extend them
without weakening existing accounting guarantees.
"""

from collections import Counter

from sqlalchemy.exc import IntegrityError, OperationalError

from models import (
    MealOption,
    MealService,
    MealServiceStatus,
    Participant,
    PickupWindow,
    Team,
    TeamMealPreference,
    TeamReservation,
    db,
    utc_now,
)


class TeamPreferenceError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def get_reservable_team_members(*, event_id, team_id):
    team = db.session.execute(
        db.select(Team).where(
            Team.id == team_id, Team.event_id == event_id, Team.active.is_(True)
        )
    ).scalar_one_or_none()
    if team is None:
        raise TeamPreferenceError("TEAM_INACTIVE", "Choose an active team for this event.")

    members = db.session.execute(
        db.select(Participant)
        .where(
            Participant.event_id == event_id,
            Participant.team_id == team.id,
            Participant.active.is_(True),
        )
        .order_by(Participant.name, Participant.id)
    ).scalars().all()
    if not members:
        raise TeamPreferenceError(
            "TEAM_EMPTY", "This team has no active registered members to choose meals for."
        )
    return team, members


def _parse_preferences(member_preferences):
    if not isinstance(member_preferences, (list, tuple)):
        raise TeamPreferenceError("INVALID_PREFERENCES", "Submit one meal choice for each team member.")
    parsed = []
    for item in member_preferences:
        if not isinstance(item, dict):
            raise TeamPreferenceError("INVALID_PREFERENCES", "Submit valid team member meal choices.")
        try:
            participant_id = int(item.get("participant_id"))
            meal_option_id = int(item.get("meal_option_id"))
        except (TypeError, ValueError) as error:
            raise TeamPreferenceError(
                "INVALID_PREFERENCES", "Submit valid team member meal choices."
            ) from error
        if participant_id <= 0 or meal_option_id <= 0:
            raise TeamPreferenceError(
                "INVALID_PREFERENCES", "Submit valid team member meal choices."
            )
        parsed.append((participant_id, meal_option_id))
    return parsed


def _load_open_meal_context(event_id, meal_service_id, pickup_window_id):
    meal = db.session.execute(
        db.select(MealService).where(
            MealService.id == meal_service_id,
            MealService.event_id == event_id,
            MealService.status == MealServiceStatus.OPEN,
        )
    ).scalar_one_or_none()
    if meal is None:
        raise TeamPreferenceError("MEAL_NOT_OPEN", "Choose an open meal service for this event.")
    window = db.session.execute(
        db.select(PickupWindow).where(
            PickupWindow.id == pickup_window_id,
            PickupWindow.meal_service_id == meal.id,
            PickupWindow.active.is_(True),
        )
    ).scalar_one_or_none()
    if window is None:
        raise TeamPreferenceError("INVALID_WINDOW", "Choose a valid active pickup window.")
    return meal, window


def create_team_reservation(
    *, event_id, team_id, meal_service_id, pickup_window_id, member_preferences
):
    """Persist a team preference set after validating all membership and meal links."""
    parsed_preferences = _parse_preferences(member_preferences)
    submitted_member_ids = [participant_id for participant_id, _ in parsed_preferences]
    if len(submitted_member_ids) != len(set(submitted_member_ids)):
        raise TeamPreferenceError("DUPLICATE_MEMBER", "A team member can only be selected once.")

    try:
        team, members = get_reservable_team_members(event_id=event_id, team_id=team_id)
        member_ids = {member.id for member in members}
        submitted_ids = set(submitted_member_ids)
        if submitted_ids != member_ids:
            raise TeamPreferenceError(
                "INVALID_MEMBER_SET",
                "Choose exactly the active registered members of this team.",
            )
        meal, window = _load_open_meal_context(event_id, meal_service_id, pickup_window_id)

        option_ids = {option_id for _, option_id in parsed_preferences}
        options = db.session.execute(
            db.select(MealOption).where(
                MealOption.id.in_(option_ids),
                MealOption.meal_service_id == meal.id,
                MealOption.active.is_(True),
            )
        ).scalars().all()
        options_by_id = {option.id: option for option in options}
        if len(options_by_id) != len(option_ids):
            raise TeamPreferenceError(
                "INVALID_MEAL_OPTION", "Choose valid active meal options for this meal service."
            )

        now = utc_now()
        reservation = TeamReservation(
            event_id=event_id,
            team_id=team.id,
            meal_service_id=meal.id,
            pickup_window_id=window.id,
            quantity=len(parsed_preferences),
            created_at=now,
            updated_at=now,
        )
        db.session.add(reservation)
        db.session.flush()
        for participant_id, option_id in parsed_preferences:
            db.session.add(
                TeamMealPreference(
                    team_reservation_id=reservation.id,
                    participant_id=participant_id,
                    meal_option_id=option_id,
                    quantity=1,
                    created_at=now,
                )
            )
        db.session.commit()
        return reservation
    except TeamPreferenceError:
        db.session.rollback()
        raise
    except (IntegrityError, OperationalError) as error:
        db.session.rollback()
        raise TeamPreferenceError(
            "PREFERENCE_CONFLICT", "The team preferences changed concurrently. Please try again."
        ) from error


def preference_breakdown(reservation):
    """Return a deterministic, later-staff-usable meal option quantity summary."""
    counts = Counter()
    for preference in reservation.preferences:
        counts[preference.meal_option.name] += preference.quantity
    return dict(sorted(counts.items()))
