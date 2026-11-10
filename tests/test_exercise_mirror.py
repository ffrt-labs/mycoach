"""``health.exercises`` mirrors the pinned catalogue at load time (ticket #185, design #109)."""

import pytest
from sqlalchemy import func, select

from mycoach import health
from mycoach.exercise_catalogue import load_exercise_catalogue
from mycoach.models.exercise import Exercise


@pytest.mark.asyncio
async def test_sync_mirrors_the_whole_catalogue_and_is_idempotent() -> None:
    from tests.conftest import test_session

    assert await health.sync_exercise_catalogue() == len(load_exercise_catalogue())
    assert await health.sync_exercise_catalogue() == 0  # nothing new, nothing changed
    async with test_session() as session:
        total = (await session.execute(select(func.count()).select_from(Exercise))).scalar_one()
        assert total == len(load_exercise_catalogue())


@pytest.mark.asyncio
async def test_sync_repairs_a_retitled_exercise() -> None:
    from tests.conftest import test_session

    first = load_exercise_catalogue()[0]
    await health.sync_exercise_catalogue()
    async with test_session() as session:
        row = await session.get(Exercise, first.id)
        assert row is not None
        row.title = "stale"
        await session.commit()
    assert await health.sync_exercise_catalogue() == 1
    async with test_session() as session:
        row = await session.get(Exercise, first.id)
        assert row is not None and row.title == first.name
