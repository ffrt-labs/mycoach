"""Source-agnostic canonical workout ingestion schema.

Any workout source (Hevy CSV, the offline companion logger, scripts, iOS
Shortcuts, future apps) maps its data to these dataclasses, and
``sources.importer.import_workouts`` turns them into ``Activity`` +
``GymWorkoutDetail`` rows. This decouples the domain model from any single
source's wire format.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WorkoutSetImport:
    """A single set within an imported workout."""

    exercise_title: str
    set_index: int
    set_type: str = "normal"  # normal, warmup, dropset, failure
    superset_id: int | None = None
    exercise_notes: str | None = None
    weight_kg: float | None = None
    reps: int | None = None
    distance_meters: float | None = None
    duration_seconds: int | None = None
    rpe: float | None = None


@dataclass
class WorkoutImport:
    """A single workout to import, independent of its originating source.

    ``external_id`` is a source-native stable id (e.g. the logger's client UUID)
    used for robust deduplication. When absent, dedup falls back to
    ``(title, start_time)``.
    """

    title: str
    start_time: datetime
    external_id: str | None = None
    sport: str = "gym"
    end_time: datetime | None = None
    notes: str | None = None
    sets: list[WorkoutSetImport] = field(default_factory=list)
