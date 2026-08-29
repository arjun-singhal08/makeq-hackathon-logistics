"""Registered participant and team operations for MakeQ events."""

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from models import Event, Participant, Team, db, utc_now


class IdentityServiceError(Exception):
    """Expected registration-domain failure that can be shown safely in the UI."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _clean_text(value, *, field_name, maximum=120, required=True):
    text = "" if value is None else str(value).strip()
    if not text and required:
        raise IdentityServiceError("INVALID_INPUT", f"{field_name} is required.")
    if len(text) > maximum:
        raise IdentityServiceError(
            "INVALID_INPUT", f"{field_name} must be {maximum} characters or fewer."
        )
    return text or None


def _event(event_id):
    event = db.session.get(Event, event_id)
    if event is None:
        raise IdentityServiceError("EVENT_NOT_FOUND", "No active event is available.")
    return event


def _team_for_event(event_id, team_id, *, active_only=False):
    team = db.session.execute(
        db.select(Team).where(Team.id == team_id, Team.event_id == event_id)
    ).scalar_one_or_none()
    if team is None:
        raise IdentityServiceError("TEAM_NOT_FOUND", "Choose a valid team for this event.")
    if active_only and not team.active:
        raise IdentityServiceError("TEAM_INACTIVE", "That team is inactive.")
    return team


def _participant_for_event(event_id, participant_id, *, active_only=False):
    participant = db.session.execute(
        db.select(Participant).where(
            Participant.id == participant_id, Participant.event_id == event_id
        )
    ).scalar_one_or_none()
    if participant is None:
        raise IdentityServiceError(
            "PARTICIPANT_NOT_FOUND", "Choose a valid participant for this event."
        )
    if active_only and not participant.active:
        raise IdentityServiceError("PARTICIPANT_INACTIVE", "That participant is inactive.")
    return participant


def _duplicate_participant(event_id, name, organizer_identifier):
    conditions = [func.lower(Participant.name) == name.casefold()]
    if organizer_identifier:
        conditions.append(
            func.lower(Participant.organizer_identifier) == organizer_identifier.casefold()
        )
    return db.session.execute(
        db.select(Participant.id).where(Participant.event_id == event_id, or_(*conditions))
    ).scalar_one_or_none()


def create_team(*, event_id, name):
    _event(event_id)
    name = _clean_text(name, field_name="Team name")
    duplicate = db.session.execute(
        db.select(Team.id).where(
            Team.event_id == event_id, func.lower(Team.name) == name.casefold()
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise IdentityServiceError("DUPLICATE_TEAM", "That team name is already registered.")

    try:
        team = Team(event_id=event_id, name=name, active=True)
        db.session.add(team)
        db.session.commit()
        return team
    except IntegrityError as error:
        db.session.rollback()
        raise IdentityServiceError("DUPLICATE_TEAM", "That team name is already registered.") from error


def create_participant(*, event_id, name, organizer_identifier=None, team_id=None):
    _event(event_id)
    name = _clean_text(name, field_name="Participant name")
    organizer_identifier = _clean_text(
        organizer_identifier, field_name="Organizer identifier", required=False
    )
    if _duplicate_participant(event_id, name, organizer_identifier) is not None:
        raise IdentityServiceError(
            "DUPLICATE_PARTICIPANT", "That participant name or identifier is already registered."
        )
    team = None
    if team_id is not None:
        team = _team_for_event(event_id, team_id)

    try:
        participant = Participant(
            event_id=event_id,
            team_id=team.id if team else None,
            name=name,
            organizer_identifier=organizer_identifier,
            active=True,
        )
        db.session.add(participant)
        db.session.commit()
        return participant
    except IntegrityError as error:
        db.session.rollback()
        raise IdentityServiceError(
            "DUPLICATE_PARTICIPANT", "That participant name or identifier is already registered."
        ) from error


def set_participant_team(*, event_id, participant_id, team_id=None):
    participant = _participant_for_event(event_id, participant_id)
    if not participant.active:
        raise IdentityServiceError("PARTICIPANT_INACTIVE", "Inactive participants cannot change teams.")
    team = None
    if team_id is not None:
        team = _team_for_event(event_id, team_id)
        if not team.active:
            raise IdentityServiceError("TEAM_INACTIVE", "Inactive teams cannot receive members.")

    participant.team_id = team.id if team else None
    participant.updated_at = utc_now()
    db.session.commit()
    return participant


def set_participant_active(*, event_id, participant_id, active):
    participant = _participant_for_event(event_id, participant_id)
    participant.active = bool(active)
    participant.updated_at = utc_now()
    db.session.commit()
    return participant


def set_team_active(*, event_id, team_id, active):
    team = _team_for_event(event_id, team_id)
    team.active = bool(active)
    team.updated_at = utc_now()
    db.session.commit()
    return team


def event_has_registered_identities(event_id):
    return (
        db.session.scalar(
            db.select(db.func.count(Participant.id)).where(Participant.event_id == event_id)
        )
        > 0
        or db.session.scalar(
            db.select(db.func.count(Team.id)).where(Team.event_id == event_id)
        )
        > 0
    )


def resolve_reservation_identity(*, event_id, participant_id=None, team_id=None):
    """Return canonical selected identity for a new ticket, never request text."""
    if participant_id is not None and team_id is not None:
        raise IdentityServiceError(
            "INVALID_IDENTITY", "Choose either one participant or one team."
        )
    if participant_id is None and team_id is None:
        return None, None, None
    if participant_id is not None:
        participant = _participant_for_event(event_id, participant_id, active_only=True)
        return participant, None, participant.name
    team = _team_for_event(event_id, team_id, active_only=True)
    return None, team, team.name
