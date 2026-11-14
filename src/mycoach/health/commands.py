"""Commands on the Observed athlete record (map #85, ticket #186, designs #111 and #112).

Each command runs in its own health transaction and commits it, then returns what it raised
as plain-data events. The call site dispatches them to coaching *after* this commit, so a
coaching handler can never roll a health write back. Health never imports coaching.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from mycoach.config import get_settings
from mycoach.exercise_catalogue import load_exercise_catalogue
from mycoach.health.events import CanonicalActivitySettled, HealthEvent
from mycoach.health.garmin_weight import retry_garmin_weight_pushes
from mycoach.health.runtime import health_session
from mycoach.models.activity import Activity
from mycoach.models.athlete import Athlete
from mycoach.models.body_measurement import BodyMeasurement
from mycoach.models.data_source import DataSourceConfig
from mycoach.models.exercise import Exercise
from mycoach.sources.base import DataSource, ImportResult
from mycoach.sources.garmin.mappers import import_activities
from mycoach.sources.garmin.source import GarminSource
from mycoach.sources.importer import import_workouts
from mycoach.sources.reconcile import LinkResult, link_garmin_to_gym
from mycoach.sources.workout_import import WorkoutImport

logger = logging.getLogger(__name__)


@dataclass
class RecordedWorkout:
    """What ``record_workout`` did: the activity's external id, never an internal id."""

    activity_external_id: str
    created: bool
    events: list[HealthEvent] = field(default_factory=list)


@dataclass
class RecordedWorkouts:
    result: ImportResult
    events: list[HealthEvent]


async def record_workouts(
    athlete_id: int, source: str, workouts: list[WorkoutImport]
) -> RecordedWorkouts:
    """Record a batch of workouts and their sets in one transaction. Idempotent per workout.

    Identity is ``(athlete, source, external_id)``; a replay creates nothing and raises no
    event.
    """
    async with health_session() as session:
        result = await import_workouts(session, athlete_id, workouts, source)
        await session.commit()
    return RecordedWorkouts(result, list(result.events))


async def record_workout(athlete_id: int, source: str, workout: WorkoutImport) -> RecordedWorkout:
    """Record one workout and all its sets in one idempotent call (decision on #111)."""
    batch = await record_workouts(athlete_id, source, [workout])
    return RecordedWorkout(
        activity_external_id=batch.result.external_ids[0],
        created=batch.result.activities_created == 1,
        events=batch.events,
    )


async def record_garmin_activities(
    athlete_id: int, raw_activities: list[dict[str, Any]]
) -> RecordedWorkouts:
    """Record Garmin's raw activities (each a source record + a canonical activity)."""
    async with health_session() as session:
        result = await import_activities(session, athlete_id, raw_activities)
        await session.commit()
    return RecordedWorkouts(result, list(result.events))


@dataclass
class LinkOutcome:
    linked: int
    errors: list[str]
    events: list[HealthEvent]


async def link_garmin(athlete_id: int) -> LinkOutcome:
    """Link overlapping Garmin activities onto their gym activities (reconciliation)."""
    async with health_session() as session:
        result: LinkResult = await link_garmin_to_gym(session, athlete_id)
        await session.commit()
    return LinkOutcome(result.linked, list(result.errors or []), list(result.events))


async def settle_due_activities(
    athlete_id: int, now: datetime | None = None
) -> list[HealthEvent]:
    """Settle gym-sourced activities whose deadline has passed without a Garmin match.

    Only gym-sourced ones (not ``garmin``): a Garmin-only activity expects no second source (#112).

    The deadline is ``Settings.activity_settlement_deadline_hours`` after the activity's start
    (#112). Recorded on the row, so each activity settles exactly once.
    """
    now = now or datetime.utcnow()
    cutoff = now - timedelta(hours=get_settings().activity_settlement_deadline_hours)
    events: list[HealthEvent] = []
    async with health_session() as session:
        due = (
            (
                await session.execute(
                    select(Activity).where(
                        Activity.user_id == athlete_id,
                        Activity.settled_at.is_(None),
                        Activity.data_source != "garmin",
                        Activity.start_time <= cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        for activity in due:
            if not activity.external_id:
                continue
            activity.settled_at = now
            activity.settlement_reason = "deadline"
            events.append(
                CanonicalActivitySettled.build(
                    athlete_id, activity.external_id, activity.sport, now, "deadline"
                )
            )
        await session.commit()
    return events


@dataclass
class SyncOutcome:
    result: ImportResult
    link: LinkOutcome
    events: list[HealthEvent]
    """Everything the sync raised: new activities, reconciliations, deadline settlements."""


async def sync_garmin(
    athlete_id: int, source: DataSource, since: datetime | None = None
) -> SyncOutcome:
    """Fetch and import Garmin data, link it onto gym activities, settle what is due, and
    retry the weight readings not yet copied to Garmin.

    The settlement check piggybacks on the Garmin sync, the job whose new data is what
    reconciles an activity (no cron of its own, #112). So does the weight-push retry
    (#199): the sync already holds a connected client. A retry failure never fails it.
    """
    async with health_session() as session:
        result = await source.fetch_and_import(session, athlete_id, since=since)
    link = await link_garmin(athlete_id)
    deadline = await settle_due_activities(athlete_id)
    if isinstance(source, GarminSource):
        try:
            await retry_garmin_weight_pushes(athlete_id, source.client)
        except Exception:  # noqa: BLE001 - the weight copy must never cost a sync
            logger.exception("Garmin weight-push retry failed")
    return SyncOutcome(result, link, [*result.events, *link.events, *deadline])


async def sync_exercise_catalogue() -> int:
    """Mirror the pinned catalogue into ``health.exercises``; return how many rows changed.

    The JSON catalogue stays the source of truth (design #109): this inserts what is missing
    and repairs a retitled row, so ``gym_workout_details.exercise_id`` always has a row to
    point at. Idempotent; run at app startup.
    """
    changed = 0
    async with health_session() as session:
        existing = {row.id: row for row in (await session.execute(select(Exercise))).scalars()}
        for exercise in load_exercise_catalogue():
            row = existing.get(exercise.id)
            if row is None:
                session.add(Exercise(id=exercise.id, title=exercise.name))
                changed += 1
            elif row.title != exercise.name:
                row.title = exercise.name
                changed += 1
        await session.commit()
    return changed


async def issue_athlete(athlete_id: int | None = None) -> int:
    """Issue an Athlete ID (the health schema owns identity), or return the existing one.

    ``athlete_id`` pins the id for the first deployment, which serves one athlete.
    """
    async with health_session() as session:
        if athlete_id is not None:
            existing = await session.get(Athlete, athlete_id)
            if existing is not None:
                return existing.id
        athlete = Athlete(id=athlete_id) if athlete_id is not None else Athlete()
        session.add(athlete)
        await session.commit()
        return athlete.id


async def store_source_credentials(athlete_id: int, source_type: str, encrypted: str) -> None:
    """Keep an encrypted ingestion credential for a data source (health owns ingestion config)."""
    async with health_session() as session:
        config = (
            await session.execute(
                select(DataSourceConfig).where(
                    DataSourceConfig.user_id == athlete_id,
                    DataSourceConfig.source_type == source_type,
                )
            )
        ).scalar_one_or_none()
        if config is None:
            session.add(
                DataSourceConfig(
                    user_id=athlete_id, source_type=source_type, credentials_encrypted=encrypted
                )
            )
        else:
            config.credentials_encrypted = encrypted
        await session.commit()


async def delete_source_credentials(athlete_id: int, source_type: str) -> bool:
    """Remove a stored credential. False if the source has no config."""
    async with health_session() as session:
        config = (
            await session.execute(
                select(DataSourceConfig).where(
                    DataSourceConfig.user_id == athlete_id,
                    DataSourceConfig.source_type == source_type,
                )
            )
        ).scalar_one_or_none()
        if config is None:
            return False
        config.credentials_encrypted = None
        await session.commit()
        return True


@dataclass(frozen=True)
class BodyMeasurementRecord:
    """A body measurement as plain data: what was recorded, or what a delete removed.

    ``id`` is the measurement's own name across the boundary (the health side issues it;
    a body reading has no source id). The Garmin weight push (#199) works from this
    record after the command has committed, so a failed push never loses a reading.
    """

    id: int
    athlete_id: int
    measured_at: datetime
    """UTC, naive."""
    weight_kg: float | None
    waist_cm: float | None
    note: str | None
    source: str
    garmin_push_status: str | None = None
    """``pending``/``pushed``/``failed``; None for a waist-only reading (never pushed)."""
    garmin_weigh_in_id: str | None = None


def _body_record(row: BodyMeasurement) -> BodyMeasurementRecord:
    return BodyMeasurementRecord(
        id=row.id,
        athlete_id=row.athlete_id,
        measured_at=row.measured_at,
        weight_kg=row.weight_kg,
        waist_cm=row.waist_cm,
        note=row.note,
        source=row.source,
        garmin_push_status=row.garmin_push_status,
        garmin_weigh_in_id=row.garmin_weigh_in_id,
    )


async def record_body_measurement(
    athlete_id: int,
    *,
    measured_at: datetime,
    weight_kg: float | None = None,
    waist_cm: float | None = None,
    note: str | None = None,
    source: str = "manual",
) -> BodyMeasurementRecord:
    """Record a weight and/or waist reading (ticket #198). ``measured_at`` is naive UTC.

    Raises ``ValueError`` when neither weight nor waist is given (the table refuses it too).
    """
    if weight_kg is None and waist_cm is None:
        raise ValueError("A body measurement needs a weight or a waist")
    async with health_session() as session:
        row = BodyMeasurement(
            athlete_id=athlete_id,
            measured_at=measured_at,
            weight_kg=weight_kg,
            waist_cm=waist_cm,
            note=note or None,
            source=source,
            # Only a weight goes to Garmin (#199); the push itself runs after this commit.
            garmin_push_status="pending" if weight_kg is not None else None,
            garmin_push_attempts=0,
        )
        session.add(row)
        await session.commit()
        return _body_record(row)


async def delete_body_measurement(
    athlete_id: int, measurement_id: int
) -> BodyMeasurementRecord | None:
    """Delete one of the athlete's measurements; return what was removed, or None."""
    async with health_session() as session:
        row = await session.get(BodyMeasurement, measurement_id)
        if row is None or row.athlete_id != athlete_id:
            return None
        record = _body_record(row)
        await session.delete(row)
        await session.commit()
    return record


async def set_athlete_height(athlete_id: int, height_cm: float | None) -> None:
    """Store the athlete's height (entered once, used for waist-to-height)."""
    async with health_session() as session:
        athlete = await session.get(Athlete, athlete_id)
        if athlete is None:
            athlete = Athlete(id=athlete_id)
            session.add(athlete)
        athlete.height_cm = height_cm
        await session.commit()


async def has_source_credentials(athlete_id: int, source_type: str) -> bool:
    async with health_session() as session:
        config = (
            await session.execute(
                select(DataSourceConfig).where(
                    DataSourceConfig.user_id == athlete_id,
                    DataSourceConfig.source_type == source_type,
                )
            )
        ).scalar_one_or_none()
    return config is not None and config.credentials_encrypted is not None


__all__ = [
    "BodyMeasurementRecord",
    "LinkOutcome",
    "RecordedWorkout",
    "RecordedWorkouts",
    "SyncOutcome",
    "delete_body_measurement",
    "delete_source_credentials",
    "has_source_credentials",
    "issue_athlete",
    "link_garmin",
    "record_body_measurement",
    "record_garmin_activities",
    "record_workout",
    "record_workouts",
    "set_athlete_height",
    "settle_due_activities",
    "store_source_credentials",
    "sync_exercise_catalogue",
    "sync_garmin",
]
