"""Wire schemas for the offline logger.

Only the *wire* gets a schema — ``PlannedSession.details`` stays an internal
blob (see the resolution on #48). Cardio ``details`` is LLM-shaped and passed
through unvalidated.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel


class PrescribedExercise(BaseModel):
    """One exercise in a prescribed session, weights filled or null."""

    exercise_id: str | None = None  # catalogue join key; null for a custom exercise
    name: str
    sets: int | None = None
    rep_range: str | None = None
    target_weight_kg: float | None = None
    target_rpe: float | None = None
    rest_seconds: int | None = None
    superset_group: int | None = None
    notes: str | None = None


class PrescribedSession(BaseModel):
    """One session on the phone. One shape for every sport.

    Gym sessions carry ``exercises`` (weights filled from the coach's plan, or
    null when built from the routine). Non-gym sessions carry the LLM-shaped
    ``details`` blob, passed through untouched, and are display-only.
    """

    id: int | None  # PlannedSession.id; null for a routine-built fallback session
    title: str
    sport: str
    track: str  # "gym" or "cardio"
    duration_minutes: int | None = None
    notes: str | None = None
    loggable: bool  # the logger can log it (track == "gym") vs display only
    done: bool  # fallback flag, not an answer — stale offline; unioned with local state
    exercises: list[PrescribedExercise] = []
    details: Any | None = None  # cardio's LLM-shaped blob, unvalidated; null for gym


class TrainingWeek(BaseModel):
    """The current Monday–Sunday, all sports, merged server-side."""

    week_start: date
    sessions: list[PrescribedSession]
