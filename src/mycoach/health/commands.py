"""Commands on the Observed athlete record (map #85, ticket #186, designs #111 and #112).

Each command runs in its own health transaction and commits it, then returns what it raised
as plain-data events. The call site dispatches them to coaching *after* this commit, so a
coaching handler can never roll a health write back. Health never imports coaching.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from mycoach.config import get_settings
from mycoach.health.events import CanonicalActivitySettled, HealthEvent
from mycoach.health.runtime import health_session
from mycoach.models.activity import Activity
from mycoach.models.athlete import Athlete
from mycoach.models.data_source import DataSourceConfig
from mycoach.sources.base import DataSource, ImportResult
from mycoach.sources.garmin.mappers import import_activities
from mycoach.sources.importer import import_workouts
from mycoach.sources.reconcile import LinkResult, link_garmin_to_gym
from mycoach.sources.workout_import import WorkoutImport


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
    """Fetch and import Garmin data, link it onto gym activities, settle what is due.

    The settlement check piggybacks on the Garmin sync, the job whose new data is what
    reconciles an activity (no cron of its own, #112).
    """
    async with health_session() as session:
        result = await source.fetch_and_import(session, athlete_id, since=since)
    link = await link_garmin(athlete_id)
    deadline = await settle_due_activities(athlete_id)
    return SyncOutcome(result, link, [*result.events, *link.events, *deadline])


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
    "LinkOutcome",
    "RecordedWorkout",
    "RecordedWorkouts",
    "SyncOutcome",
    "delete_source_credentials",
    "has_source_credentials",
    "issue_athlete",
    "link_garmin",
    "record_garmin_activities",
    "record_workout",
    "record_workouts",
    "settle_due_activities",
    "store_source_credentials",
    "sync_garmin",
]
