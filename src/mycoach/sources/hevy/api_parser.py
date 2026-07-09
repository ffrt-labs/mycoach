"""Parse Hevy API workout JSON into HevyWorkout/HevySet dataclasses (reuses CSV types)."""

from datetime import datetime

from mycoach.sources.hevy.csv_parser import HevyParseResult, HevySet, HevyWorkout


def _parse_api_datetime(value: int | str | None) -> datetime | None:
    """Parse a Unix timestamp (int) or ISO 8601 string into a naive UTC datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(int(value))
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def parse_api_workouts(workouts_json: list[dict]) -> HevyParseResult:  # type: ignore[type-arg]
    """Parse a list of Hevy API workout dicts into HevyWorkout/HevySet dataclasses.

    Reuses the same HevyParseResult / HevyWorkout / HevySet types as the CSV parser
    so that import_hevy_workouts() can ingest both sources without modification.

    Args:
        workouts_json: List of raw workout dicts from the Hevy API.

    Returns:
        HevyParseResult with parsed workouts and any per-workout errors.
    """
    result = HevyParseResult()

    for workout_data in workouts_json:
        title = (workout_data.get("name") or "").strip()
        start_ts = workout_data.get("start_time")
        end_ts = workout_data.get("end_time")

        start_time = _parse_api_datetime(start_ts)
        if start_time is None:
            result.errors.append(f"Workout '{title}': invalid start_time '{start_ts}' — skipped")
            result.rows_skipped += 1
            continue

        end_time = _parse_api_datetime(end_ts)
        workout = HevyWorkout(title=title, start_time=start_time, end_time=end_time)

        exercises: list[dict] = workout_data.get("exercises") or []  # type: ignore[assignment]
        for exercise in exercises:
            exercise_title = (exercise.get("title") or "").strip()
            sets: list[dict] = exercise.get("sets") or []  # type: ignore[assignment]
            for set_data in sets:
                rpe_raw = set_data.get("rpe")
                rpe: float | None = None
                if rpe_raw is not None:
                    try:
                        rpe_val = float(rpe_raw)
                        rpe = rpe_val if 1.0 <= rpe_val <= 10.0 else None
                    except (TypeError, ValueError):
                        rpe = None

                hevy_set = HevySet(
                    exercise_title=exercise_title,
                    set_index=set_data.get("index") or 0,
                    set_type="normal",
                    weight_kg=set_data.get("weight_kg"),
                    reps=set_data.get("reps"),
                    distance_meters=set_data.get("distance_meters"),
                    duration_seconds=set_data.get("duration_seconds"),
                    rpe=rpe,
                )
                workout.sets.append(hevy_set)
                result.rows_parsed += 1

        result.workouts.append(workout)

    return result
