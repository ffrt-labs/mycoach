"""Parse Hevy CSV workout exports into structured data."""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

LBS_TO_KG = 0.45359237
MILES_TO_METERS = 1609.344

REQUIRED_COLUMNS = {
    "title",
    "start_time",
    "end_time",
    "exercise_title",
    "set_index",
    "set_type",
}

EXPECTED_COLUMNS = REQUIRED_COLUMNS | {
    "superset_id",
    "exercise_notes",
    "weight_lbs",
    "reps",
    "distance_miles",
    "duration_seconds",
    "rpe",
}


@dataclass
class HevySet:
    """A single set parsed from a Hevy CSV row."""

    exercise_title: str
    set_index: int
    set_type: str
    superset_id: int | None = None
    exercise_notes: str | None = None
    weight_kg: float | None = None
    reps: int | None = None
    distance_meters: float | None = None
    duration_seconds: int | None = None
    rpe: float | None = None


@dataclass
class HevyWorkout:
    """A workout parsed from a Hevy CSV export (group of rows with same title+start_time)."""

    title: str
    start_time: datetime
    end_time: datetime | None
    sets: list[HevySet] = field(default_factory=list)


@dataclass
class HevyParseResult:
    """Result of parsing a Hevy CSV file."""

    workouts: list[HevyWorkout] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rows_parsed: int = 0
    rows_skipped: int = 0


def _parse_optional_float(value: str) -> float | None:
    if not value or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_optional_int(value: str) -> int | None:
    if not value or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        f = _parse_optional_float(value)
        return int(f) if f is not None else None


def _parse_datetime(value: str) -> datetime | None:
    if not value or not value.strip():
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_hevy_csv(content: str) -> HevyParseResult:
    """Parse a Hevy CSV export string into structured workout data.

    Args:
        content: Raw CSV text from Hevy export.

    Returns:
        HevyParseResult with parsed workouts and any errors.
    """
    result = HevyParseResult()
    # Strip BOM if present (common in Windows-exported CSVs)
    content = content.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(content))

    if reader.fieldnames is None:
        result.errors.append("CSV file is empty or has no header row")
        return result

    actual_columns = set(reader.fieldnames)
    missing = REQUIRED_COLUMNS - actual_columns
    if missing:
        result.errors.append(f"Missing required columns: {', '.join(sorted(missing))}")
        return result

    # Group rows into workouts by (title, start_time)
    workouts_map: dict[tuple[str, str], HevyWorkout] = {}

    for row_num, row in enumerate(reader, start=2):  # row 1 is header
        title = row.get("title", "").strip()
        start_time_str = row.get("start_time", "").strip()
        exercise_title = row.get("exercise_title", "").strip()

        if not title or not start_time_str or not exercise_title:
            result.errors.append(f"Row {row_num}: missing title, start_time, or exercise_title")
            result.rows_skipped += 1
            continue

        start_time = _parse_datetime(start_time_str)
        if start_time is None:
            result.errors.append(f"Row {row_num}: invalid start_time '{start_time_str}'")
            result.rows_skipped += 1
            continue

        set_index = _parse_optional_int(row.get("set_index", ""))
        if set_index is None:
            result.errors.append(f"Row {row_num}: invalid or missing set_index")
            result.rows_skipped += 1
            continue

        set_type = row.get("set_type", "normal").strip().lower()
        if set_type not in ("normal", "warmup", "dropset", "failure"):
            set_type = "normal"

        # Convert units: lbs → kg, miles → meters
        weight_lbs = _parse_optional_float(row.get("weight_lbs", ""))
        weight_kg = round(weight_lbs * LBS_TO_KG, 2) if weight_lbs is not None else None

        distance_miles = _parse_optional_float(row.get("distance_miles", ""))
        distance_meters = (
            round(distance_miles * MILES_TO_METERS, 1) if distance_miles is not None else None
        )

        rpe = _parse_optional_float(row.get("rpe", ""))
        if rpe is not None and not (1 <= rpe <= 10):
            result.errors.append(f"Row {row_num}: RPE {rpe} out of range 1-10, ignoring")
            rpe = None

        hevy_set = HevySet(
            exercise_title=exercise_title,
            set_index=set_index,
            set_type=set_type,
            superset_id=_parse_optional_int(row.get("superset_id", "")),
            exercise_notes=row.get("exercise_notes", "").strip() or None,
            weight_kg=weight_kg,
            reps=_parse_optional_int(row.get("reps", "")),
            distance_meters=distance_meters,
            duration_seconds=_parse_optional_int(row.get("duration_seconds", "")),
            rpe=rpe,
        )

        workout_key = (title, start_time_str)
        if workout_key not in workouts_map:
            end_time = _parse_datetime(row.get("end_time", ""))
            workouts_map[workout_key] = HevyWorkout(
                title=title,
                start_time=start_time,
                end_time=end_time,
            )

        workouts_map[workout_key].sets.append(hevy_set)
        result.rows_parsed += 1

    result.workouts = list(workouts_map.values())
    return result
