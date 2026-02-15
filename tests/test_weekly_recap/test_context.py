"""Tests for weekly recap context functions."""

from datetime import date, datetime

from mycoach.coaching.context import get_activities_for_week, get_plan_adherence_for_week
from mycoach.models.activity import Activity
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: object) -> int:
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]
    return user.id


class TestGetPlanAdherenceForWeek:
    async def test_returns_adherence_data(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)  # Monday

            plan = WeeklyPlan(
                user_id=user_id,
                week_start=week_start,
                status="active",
                summary="Test plan",
                prompt_version="v1",
            )
            session.add(plan)
            await session.flush()

            s1 = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Upper Body", completed=True
            )
            s2 = PlannedSession(
                plan_id=plan.id, day_of_week=2, sport="swimming", title="Swim", completed=False
            )
            session.add_all([s1, s2])
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, week_start)
            assert result is not None
            assert result["total_sessions"] == 2
            assert result["completed_sessions"] == 1
            assert result["adherence_pct"] == 50.0
            assert len(result["sessions"]) == 2

    async def test_no_plan_returns_none(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            result = await get_plan_adherence_for_week(session, user_id, date(2024, 6, 10))
            assert result is None

    async def test_all_completed(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)

            plan = WeeklyPlan(
                user_id=user_id,
                week_start=week_start,
                status="active",
                summary="Full week",
                prompt_version="v1",
            )
            session.add(plan)
            await session.flush()

            s1 = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Push", completed=True
            )
            s2 = PlannedSession(
                plan_id=plan.id, day_of_week=2, sport="gym", title="Pull", completed=True
            )
            session.add_all([s1, s2])
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, week_start)
            assert result is not None
            assert result["adherence_pct"] == 100.0


class TestGetActivitiesForWeek:
    async def test_returns_activities_in_range(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)

            # Activity within the week
            a1 = Activity(
                user_id=user_id,
                title="Gym Session",
                sport="gym",
                start_time=datetime(2024, 6, 11, 8, 0),
                data_source="hevy",
            )
            # Activity outside the week
            a2 = Activity(
                user_id=user_id,
                title="Old Session",
                sport="gym",
                start_time=datetime(2024, 6, 9, 8, 0),
                data_source="hevy",
            )
            session.add_all([a1, a2])
            await session.commit()

            result = await get_activities_for_week(session, user_id, week_start)
            assert len(result) == 1
            assert result[0]["title"] == "Gym Session"

    async def test_empty_week(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            result = await get_activities_for_week(session, user_id, date(2024, 6, 10))
            assert result == []
