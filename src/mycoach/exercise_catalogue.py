"""Pinned exercise catalogue and MyCoach-owned additions."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class CatalogueExercise:
    id: str
    name: str


@lru_cache(maxsize=1)
def load_exercise_catalogue() -> tuple[CatalogueExercise, ...]:
    """Return the pinned upstream catalogue with the local overlay applied."""
    upstream = json.loads((DATA_DIR / "free_exercise_db.json").read_text())
    local = json.loads((DATA_DIR / "local_exercises.json").read_text())["exercises"]
    records = [*upstream, *local]

    exercises = tuple(
        sorted(
            (CatalogueExercise(id=record["id"], name=record["name"]) for record in records),
            key=lambda exercise: (exercise.name.casefold(), exercise.id),
        )
    )
    ids = [exercise.id for exercise in exercises]
    if len(ids) != len(set(ids)):
        raise ValueError("Exercise catalogue contains duplicate ids")
    return exercises


@lru_cache(maxsize=1)
def _known_ids() -> frozenset[str]:
    return frozenset(exercise.id for exercise in load_exercise_catalogue())


def is_known_exercise_id(exercise_id: str) -> bool:
    """Whether an id belongs to the pinned catalogue or local overlay."""
    return exercise_id in _known_ids()


def exercise_id_for_name(name: str) -> str | None:
    """Resolve an exact catalogue display name or reviewed legacy title."""
    by_name = {exercise.name.casefold(): exercise.id for exercise in load_exercise_catalogue()}
    return by_name.get(name.casefold()) or legacy_exercise_ids().get(name)


@lru_cache(maxsize=1)
def legacy_exercise_ids() -> dict[str, str | None]:
    """Map pre-ID display titles onto stable catalogue ids."""
    data = json.loads((DATA_DIR / "exercise_crosswalk.json").read_text())
    return {entry["exercise_title"]: entry["exercise_id"] for entry in data["entries"]}


async def sync_exercises(session: "AsyncSession") -> int:
    """Mirror the catalogue into ``health.exercises``; return how many rows changed.

    The JSON catalogue stays the source of truth (design #109): this inserts what is
    missing and repairs a retitled row, so ``gym_workout_details.exercise_id`` always has
    a row to point at. Idempotent. Does not commit.
    """
    from sqlalchemy import select

    from mycoach.models.exercise import Exercise

    existing = {row.id: row for row in (await session.execute(select(Exercise))).scalars()}
    changed = 0
    for exercise in load_exercise_catalogue():
        row = existing.get(exercise.id)
        if row is None:
            session.add(Exercise(id=exercise.id, title=exercise.name))
            changed += 1
        elif row.title != exercise.name:
            row.title = exercise.name
            changed += 1
    await session.flush()
    return changed
