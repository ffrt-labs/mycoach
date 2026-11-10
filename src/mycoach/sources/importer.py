"""Generic importer: canonical WorkoutImport → Activity + GymWorkoutDetail.

Source-agnostic replacement for the old Hevy-specific mapper. Every gym source
(Hevy CSV, offline logger, …) parses to ``WorkoutImport`` objects and calls
``import_workouts`` with its source name.
"""

import dataclasses
import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.exercise_catalogue import exercise_id_for_name, is_known_exercise_id
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.sources.base import ImportResult
from mycoach.sources.intake import intake_source_record
from mycoach.sources.workout_import import WorkoutImport


def source_external_id(workout: WorkoutImport) -> str:
    """The identity a workout is recorded under: its own id, else one derived from it.

    A source with no native id (the Hevy CSV) still needs an identity for the intake's
    (athlete, source, external_id) idempotency. The derivation is the old fallback key,
    (title, start_time), made a single stable string.
    """
    if workout.external_id:
        return workout.external_id
    digest = hashlib.sha256(f"{workout.title}|{workout.start_time.isoformat()}".encode())
    return f"derived:{digest.hexdigest()[:32]}"


def _raw_payload(workout: WorkoutImport) -> dict[str, Any]:
    """What the source sent, JSON-safe: the workout and every set as received."""
    return json.loads(json.dumps(dataclasses.asdict(workout), default=_iso))


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _catalogue_id(exercise_id: str | None, title: str) -> str | None:
    """The catalogue id to store, or None (a custom exercise).

    ``gym_workout_details.exercise_id`` is a FK to ``health.exercises``, the catalogue
    mirror, so an id the catalogue does not know cannot be stored.
    """
    resolved = exercise_id or exercise_id_for_name(title)
    return resolved if resolved and is_known_exercise_id(resolved) else None


def _compute_duration(workout: WorkoutImport) -> int | None:
    if workout.start_time and workout.end_time:
        minutes = int((workout.end_time - workout.start_time).total_seconds() / 60)
        return minutes if minutes > 0 else None
    return None


async def import_workouts(
    session: AsyncSession,
    athlete_id: int,
    workouts: list[WorkoutImport],
    source: str,
) -> ImportResult:
    """Import canonical workouts into the health schema (the health side of the intake).

    Funnels each workout through the shared ``intake_source_record`` (idempotent on
    (athlete, source, external_id)), then attaches its sets to the canonical activity it
    created. Does not commit: ``health.record_workouts`` owns the transaction.

    A prescription (``prescribed_*`` on a set, ``planned_session_id`` on a workout) is
    coaching's, never stored here: the call site hands it to coaching after this commits
    (``coaching.workout_log``). Deduplicated workouts raise no events.

    Returns an ``ImportResult`` with created/skipped counts, the events raised and the
    external id each workout was recorded under.
    """
    result = ImportResult(source_type=f"{source}_import")

    for workout in workouts:
        external_id = source_external_id(workout)
        intake = await intake_source_record(
            session,
            athlete_id=athlete_id,
            source=source,
            external_id=external_id,
            raw_payload=_raw_payload(workout),
            canonical=Activity(
                user_id=athlete_id,
                sport=workout.sport,
                title=workout.title,
                start_time=workout.start_time,
                end_time=workout.end_time,
                duration_minutes=_compute_duration(workout),
                data_source=source,
                external_id=external_id,
                notes=workout.notes,
                created_at=datetime.utcnow(),
            ),
        )
        if not intake.created:
            result.activities_skipped += 1
            result.external_ids.append(external_id)
            continue

        for s in workout.sets:
            session.add(
                GymWorkoutDetail(
                    activity_id=intake.canonical_activity_id,
                    exercise_id=_catalogue_id(s.exercise_id, s.exercise_title),
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
        await session.flush()
        result.events.extend(intake.events)
        result.external_ids.append(external_id)
        result.activities_created += 1

    return result
