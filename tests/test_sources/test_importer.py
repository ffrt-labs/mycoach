"""Tests for the generic canonical-workout importer."""

from datetime import datetime

import pytest
from sqlalchemy import select

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.user import User
from mycoach.sources.importer import import_workouts
from mycoach.sources.workout_import import WorkoutImport, WorkoutSetImport


@pytest.fixture
async def user(setup_db: None) -> User:
    from tests.conftest import test_session

    async with test_session() as session:
        u = User(id=1, name="Test User", email="test@example.com")
        session.add(u)
        await session.commit()
        return u


def _workout(external_id: str | None = None, title: str = "Push Day") -> WorkoutImport:
    return WorkoutImport(
        title=title,
        start_time=datetime(2024, 6, 10, 9, 0),
        end_time=datetime(2024, 6, 10, 10, 0),
        external_id=external_id,
        sets=[
            WorkoutSetImport(
                exercise_title="Bench Press", set_index=1, weight_kg=80, reps=8, rpe=7
            ),
            WorkoutSetImport(exercise_title="Bench Press", set_index=2, weight_kg=80, reps=7),
        ],
    )


class TestImportWorkouts:
    @pytest.mark.asyncio
    async def test_creates_activity_and_sets(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            result = await import_workouts(session, user.id, [_workout("abc")], source="logger")
            await session.commit()

        assert result.activities_created == 1

        async with test_session() as session:
            act = (await session.execute(select(Activity))).scalar_one()
            assert act.data_source == "logger"
            assert act.external_id == "abc"
            assert act.duration_minutes == 60
            details = (await session.execute(select(GymWorkoutDetail))).scalars().all()
            assert len(details) == 2

    @pytest.mark.asyncio
    async def test_dedup_by_external_id(self, user: User) -> None:
        """Same external_id is deduplicated even if title/time differ."""
        from tests.conftest import test_session

        async with test_session() as session:
            await import_workouts(session, user.id, [_workout("uuid-1")], source="logger")
            await session.commit()

        async with test_session() as session:
            result = await import_workouts(
                session, user.id, [_workout("uuid-1", title="Renamed")], source="logger"
            )
            await session.commit()

        assert result.activities_created == 0
        assert result.activities_skipped == 1

        async with test_session() as session:
            count = len((await session.execute(select(Activity))).scalars().all())
            assert count == 1

    @pytest.mark.asyncio
    async def test_dedup_fallback_title_start_time(self, user: User) -> None:
        """With no external_id, dedup falls back to (title, start_time)."""
        from tests.conftest import test_session

        async with test_session() as session:
            await import_workouts(session, user.id, [_workout()], source="hevy")
            await session.commit()

        async with test_session() as session:
            result = await import_workouts(session, user.id, [_workout()], source="hevy")
            await session.commit()

        assert result.activities_skipped == 1

    @pytest.mark.asyncio
    async def test_distinct_external_ids_both_imported(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            result = await import_workouts(
                session, user.id, [_workout("a"), _workout("b", title="Pull Day")], source="logger"
            )
            await session.commit()

        assert result.activities_created == 2

    @pytest.mark.asyncio
    async def test_dedup_against_merged_source(self, user: User) -> None:
        """A workout already merged with Garmin (data_source='merged') is still
        recognised as a duplicate on re-import by its external_id."""
        from tests.conftest import test_session

        async with test_session() as session:
            await import_workouts(session, user.id, [_workout("uuid-9")], source="logger")
            await session.commit()

        # Simulate the Garmin merge flipping the source to "merged".
        async with test_session() as session:
            act = (await session.execute(select(Activity))).scalar_one()
            act.data_source = "merged"
            await session.commit()

        async with test_session() as session:
            result = await import_workouts(session, user.id, [_workout("uuid-9")], source="logger")
            await session.commit()

        assert result.activities_created == 0
        assert result.activities_skipped == 1
