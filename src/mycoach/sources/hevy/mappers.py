"""Map parsed Hevy CSV data to ORM models and import into the database."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.sources.base import ImportResult
from mycoach.sources.hevy.csv_parser import HevyParseResult, HevyWorkout


async def _workout_exists(session: AsyncSession, user_id: int, workout: HevyWorkout) -> bool:
    """Check if a workout with the same title and start_time already exists."""
    stmt = select(Activity.id).where(
        Activity.user_id == user_id,
        Activity.title == workout.title,
        Activity.start_time == workout.start_time,
        Activity.data_source.in_(("hevy", "merged")),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


def _compute_duration(workout: HevyWorkout) -> int | None:
    if workout.start_time and workout.end_time:
        delta = workout.end_time - workout.start_time
        minutes = int(delta.total_seconds() / 60)
        return minutes if minutes > 0 else None
    return None


async def import_hevy_workouts(
    session: AsyncSession, user_id: int, parse_result: HevyParseResult
) -> ImportResult:
    """Import parsed Hevy workouts into the database.

    Handles deduplication by checking for existing activities with the same
    title and start_time. Creates Activity + GymWorkoutDetail records.

    Args:
        session: Active database session.
        user_id: The user to import data for.
        parse_result: Output from parse_hevy_csv().

    Returns:
        ImportResult with counts and any errors.
    """
    result = ImportResult(
        source_type="hevy_csv",
        errors=list(parse_result.errors),
    )

    for workout in parse_result.workouts:
        if await _workout_exists(session, user_id, workout):
            result.activities_skipped += 1
            continue

        activity = Activity(
            user_id=user_id,
            sport="gym",
            title=workout.title,
            start_time=workout.start_time,
            end_time=workout.end_time,
            duration_minutes=_compute_duration(workout),
            data_source="hevy",
            created_at=datetime.utcnow(),
        )
        session.add(activity)
        await session.flush()  # get activity.id

        for hevy_set in workout.sets:
            detail = GymWorkoutDetail(
                activity_id=activity.id,
                exercise_title=hevy_set.exercise_title,
                superset_id=hevy_set.superset_id,
                exercise_notes=hevy_set.exercise_notes,
                set_index=hevy_set.set_index,
                set_type=hevy_set.set_type,
                weight_kg=hevy_set.weight_kg,
                reps=hevy_set.reps,
                distance_meters=hevy_set.distance_meters,
                duration_seconds=hevy_set.duration_seconds,
                rpe=hevy_set.rpe,
            )
            session.add(detail)

        result.activities_created += 1

    await session.commit()
    return result
