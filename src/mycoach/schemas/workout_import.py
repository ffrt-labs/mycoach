"""Pydantic request models for the universal workout push endpoint.

Mirrors the canonical ``sources.workout_import`` dataclasses on the wire, with
validation, and converts to them via ``to_dataclass()`` for the importer.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from mycoach.sources.workout_import import WorkoutImport, WorkoutSetImport

SetType = Literal["normal", "warmup", "dropset", "failure"]


class WorkoutSetIn(BaseModel):
    exercise_title: str = Field(min_length=1, max_length=200)
    set_index: int = Field(ge=0)
    set_type: SetType = "normal"
    superset_id: int | None = None
    exercise_notes: str | None = None
    weight_kg: float | None = Field(default=None, ge=0)
    reps: int | None = Field(default=None, ge=0)
    distance_meters: float | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    rpe: float | None = Field(default=None, ge=1, le=10)

    def to_dataclass(self) -> WorkoutSetImport:
        return WorkoutSetImport(
            exercise_title=self.exercise_title.strip(),
            set_index=self.set_index,
            set_type=self.set_type,
            superset_id=self.superset_id,
            exercise_notes=self.exercise_notes,
            weight_kg=self.weight_kg,
            reps=self.reps,
            distance_meters=self.distance_meters,
            duration_seconds=self.duration_seconds,
            rpe=self.rpe,
        )


class WorkoutIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_time: datetime
    external_id: str | None = Field(default=None, max_length=100)
    sport: str = Field(default="gym", max_length=50)
    end_time: datetime | None = None
    notes: str | None = None
    sets: list[WorkoutSetIn] = Field(default_factory=list)

    def to_dataclass(self) -> WorkoutImport:
        return WorkoutImport(
            title=self.title.strip(),
            start_time=self.start_time,
            external_id=self.external_id,
            sport=self.sport,
            end_time=self.end_time,
            notes=self.notes,
            sets=[s.to_dataclass() for s in self.sets],
        )


class WorkoutImportBatch(BaseModel):
    """A batch of workouts pushed by an external source (logger, script, …)."""

    source: str = Field(default="logger", max_length=20)
    workouts: list[WorkoutIn] = Field(default_factory=list)

    def to_dataclasses(self) -> list[WorkoutImport]:
        return [w.to_dataclass() for w in self.workouts]
