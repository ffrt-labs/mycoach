"""``health.exercises`` mirrors the pinned catalogue at load time (ticket #185, design #109)."""

import pytest
from sqlalchemy import func, select

from mycoach.exercise_catalogue import load_exercise_catalogue, sync_exercises
from mycoach.models.exercise import Exercise


@pytest.mark.asyncio
async def test_sync_mirrors_the_whole_catalogue_and_is_idempotent() -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        assert await sync_exercises(session) == len(load_exercise_catalogue())
        await session.commit()
    async with test_session() as session:
        assert await sync_exercises(session) == 0  # nothing new, nothing changed
        await session.commit()
        total = (await session.execute(select(func.count()).select_from(Exercise))).scalar_one()
        assert total == len(load_exercise_catalogue())


@pytest.mark.asyncio
async def test_sync_repairs_a_retitled_exercise() -> None:
    from tests.conftest import test_session

    first = load_exercise_catalogue()[0]
    async with test_session() as session:
        await sync_exercises(session)
        row = await session.get(Exercise, first.id)
        assert row is not None
        row.title = "stale"
        await session.commit()
    async with test_session() as session:
        assert await sync_exercises(session) == 1
        await session.commit()
        row = await session.get(Exercise, first.id)
        assert row is not None and row.title == first.name
