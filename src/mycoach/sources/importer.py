"""Generic importer: canonical WorkoutImport → Activity + GymWorkoutDetail.

Source-agnostic replacement for the old Hevy-specific mapper. Every gym source
(Hevy CSV, offline logger, …) parses to ``WorkoutImport`` objects and calls
``import_workouts`` with its source name.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.sources.base import ImportResult
from mycoach.sources.workout_import import WorkoutImport


async def _workout_exists(
    session: AsyncSession, user_id: int, workout: WorkoutImport, source: str
) -> bool:
    """Check whether a workout is already imported.

    Dedup key: (user_id, data_source, external_id) when external_id is present,
    else fall back to (user_id, title, start_time). A previously-imported
    workout may since have been merged with Garmin data (data_source flipped to
    "merged" while keeping its external_id / title / start_time), so "merged" is
    always considered alongside the source.
    """
    sources = (source, "merged")
    if workout.external_id:
        stmt = select(Activity.id).where(
            Activity.user_id == user_id,
            Activity.external_id == workout.external_id,
            Activity.data_source.in_(sources),
        )
    else:
        stmt = select(Activity.id).where(
            Activity.user_id == user_id,
            Activity.title == workout.title,
            Activity.start_time == workout.start_time,
            Activity.data_source.in_(sources),
        )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


def _compute_duration(workout: WorkoutImport) -> int | None:
    if workout.start_time and workout.end_time:
        minutes = int((workout.end_time - workout.start_time).total_seconds() / 60)
        return minutes if minutes > 0 else None
    return None


async def import_workouts(
    session: AsyncSession,
    user_id: int,
    workouts: list[WorkoutImport],
    source: str,
) -> ImportResult:
    """Import canonical workouts into the database.

    Deduplicates (see ``_workout_exists``) and creates Activity +
    GymWorkoutDetail records tagged with ``source`` as their data_source.
    Does not commit — the caller commits (typically after auto-merge).

    Args:
        session: Active database session.
        user_id: The user to import data for.
        workouts: Parsed canonical workouts.
        source: data_source tag for created activities (e.g. "hevy", "logger").

    Returns:
        ImportResult with created/skipped counts.
    """
    result = ImportResult(source_type=f"{source}_import")

    for workout in workouts:
        if await _workout_exists(session, user_id, workout, source):
            result.activities_skipped += 1
            continue

        activity = Activity(
            user_id=user_id,
            sport=workout.sport,
            title=workout.title,
            start_time=workout.start_time,
            end_time=workout.end_time,
            duration_minutes=_compute_duration(workout),
            data_source=source,
            external_id=workout.external_id,
            notes=workout.notes,
            created_at=datetime.utcnow(),
        )
        session.add(activity)
        await session.flush()  # get activity.id

        for s in workout.sets:
            session.add(
                GymWorkoutDetail(
                    activity_id=activity.id,
                    exercise_title=s.exercise_title,
                    superset_id=s.superset_id,
                    exercise_notes=s.exercise_notes,
                    set_index=s.set_index,
                    set_type=s.set_type,
                    weight_kg=s.weight_kg,
                    reps=s.reps,
                    distance_meters=s.distance_meters,
                    duration_seconds=s.duration_seconds,
                    rpe=s.rpe,
                )
            )

        result.activities_created += 1

    return result
