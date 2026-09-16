"""Tests for the generic canonical-workout importer."""

from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
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
    async def test_persists_prescribed_alongside_performed(self, user: User) -> None:
        """Set import: sets carrying a prescription land on GymWorkoutDetail."""
        from tests.conftest import test_session

        workout = WorkoutImport(
            title="Push Day",
            start_time=datetime(2024, 6, 10, 9, 0),
            sets=[
                WorkoutSetImport(
                    exercise_title="Bench Press",
                    set_index=1,
                    weight_kg=80,
                    reps=8,
                    prescribed_weight_kg=82.5,
                    prescribed_reps=6,
                ),
                WorkoutSetImport(exercise_title="Bench Press", set_index=2, weight_kg=80, reps=7),
            ],
        )

        async with test_session() as session:
            await import_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        async with test_session() as session:
            details = (
                (await session.execute(select(GymWorkoutDetail).order_by(GymWorkoutDetail.set_index)))
                .scalars()
                .all()
            )
            assert details[0].prescribed_weight_kg == 82.5
            assert details[0].prescribed_reps == 6
            # An unprescribed set (manually logged, or no plan for the session) stays null.
            assert details[1].prescribed_weight_kg is None
            assert details[1].prescribed_reps is None

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


class TestPrescriptionLinking:
    """A logged session says which prescription it answers (#69).

    The link rides on ``PlannedSession.activity_id`` — there is no forward
    column on ``activities``. A prescription id that no longer resolves is
    expected rather than exceptional (the plan may have been regenerated or
    superseded while the session sat unsynced), so it never costs the log.
    """

    async def _plan(self, session: AsyncSession, user_id: int) -> WeeklyPlan:
        plan = WeeklyPlan(
            user_id=user_id, week_start=date(2024, 6, 10), status="active", summary="Test"
        )
        session.add(plan)
        await session.flush()
        return plan

    @pytest.mark.asyncio
    async def test_links_and_completes_the_prescription(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            plan = await self._plan(session, user.id)
            planned = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Push", track="gym"
            )
            session.add(planned)
            await session.flush()
            planned_id = planned.id

            workout = _workout("uuid-link")
            workout.planned_session_id = planned_id
            result = await import_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert not result.errors

        async with test_session() as session:
            act = (await session.execute(select(Activity))).scalar_one()
            refreshed = (
                await session.execute(select(PlannedSession).where(PlannedSession.id == planned_id))
            ).scalar_one()
            assert refreshed.completed is True
            assert refreshed.activity_id == act.id

    @pytest.mark.asyncio
    async def test_unknown_prescription_still_imports_the_log(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            workout = _workout("uuid-stale")
            workout.planned_session_id = 4242
            result = await import_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert result.errors is not None
        assert "4242" in result.errors[0]

        async with test_session() as session:
            details = (await session.execute(select(GymWorkoutDetail))).scalars().all()
            assert len(details) == 2

    @pytest.mark.asyncio
    async def test_another_users_prescription_is_not_linked(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            other = User(id=2, name="Other", email="other@example.com")
            session.add(other)
            await session.flush()
            plan = await self._plan(session, other.id)
            planned = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Push", track="gym"
            )
            session.add(planned)
            await session.flush()
            planned_id = planned.id

            workout = _workout("uuid-foreign")
            workout.planned_session_id = planned_id
            result = await import_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert result.errors

        async with test_session() as session:
            refreshed = (
                await session.execute(select(PlannedSession).where(PlannedSession.id == planned_id))
            ).scalar_one()
            assert refreshed.completed is False
            assert refreshed.activity_id is None

    @pytest.mark.asyncio
    async def test_a_claimed_prescription_is_left_alone(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            plan = await self._plan(session, user.id)
            planned = PlannedSession(
                plan_id=plan.id,
                day_of_week=0,
                sport="gym",
                title="Push",
                track="gym",
                activity_id=999,
                completed=True,
            )
            session.add(planned)
            await session.flush()
            planned_id = planned.id

            workout = _workout("uuid-claimed")
            workout.planned_session_id = planned_id
            result = await import_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert result.errors

        async with test_session() as session:
            refreshed = (
                await session.execute(select(PlannedSession).where(PlannedSession.id == planned_id))
            ).scalar_one()
            assert refreshed.activity_id == 999

    @pytest.mark.asyncio
    async def test_no_prescription_is_the_silent_case(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            result = await import_workouts(
                session, user.id, [_workout("uuid-none")], source="logger"
            )
            await session.commit()

        assert result.activities_created == 1
        assert not result.errors
