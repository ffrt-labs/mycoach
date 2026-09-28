"""Tests for the one-off cleanup script (#131)."""

from datetime import datetime

from sqlalchemy import select

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.coaching import CoachingInsight
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from mycoach.tools.cleanup_activity_58 import ACTIVITY_ID, PLANNED_SESSION_ID, cleanup
from tests.conftest import test_session


async def _create_user(session: object) -> int:
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    return user.id


class TestCleanup:
    async def test_releases_session_and_deletes_activity(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            plan = WeeklyPlan(
                user_id=user_id, week_start=datetime(2026, 9, 21).date(), status="active"
            )
            session.add(plan)
            await session.flush()
            activity = Activity(
                id=ACTIVITY_ID,
                user_id=user_id,
                sport="gym",
                title="Morning Session",
                start_time=datetime(2026, 9, 21, 8, 9, 26),
                end_time=datetime(2026, 9, 21, 8, 9, 45),
                data_source="logger",
            )
            session.add(activity)
            planned = PlannedSession(
                id=PLANNED_SESSION_ID,
                plan_id=plan.id,
                day_of_week=0,
                sport="gym",
                title="Squat Day",
                completed=True,
                activity_id=ACTIVITY_ID,
            )
            session.add(planned)
            session.add(
                CoachingInsight(
                    user_id=user_id,
                    insight_date=datetime(2026, 9, 21).date(),
                    insight_type="post_workout",
                    content='{"performance_summary": "Incomplete session, logging error."}',
                    activity_id=ACTIVITY_ID,
                )
            )
            await session.flush()

            ok = await cleanup(session)
            await session.commit()

            assert ok is True
            refreshed = await session.get(PlannedSession, PLANNED_SESSION_ID)
            assert refreshed is not None
            assert refreshed.completed is False
            assert refreshed.activity_id is None
            assert await session.get(Activity, ACTIVITY_ID) is None
            remaining_insights = (
                await session.execute(
                    select(CoachingInsight).where(CoachingInsight.activity_id == ACTIVITY_ID)
                )
            ).scalars().all()
            assert remaining_insights == []

    async def test_refuses_when_planned_session_links_a_different_activity(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            plan = WeeklyPlan(
                user_id=user_id, week_start=datetime(2026, 9, 21).date(), status="active"
            )
            session.add(plan)
            await session.flush()
            other_activity = Activity(
                user_id=user_id,
                sport="gym",
                title="Real workout",
                start_time=datetime(2026, 9, 21, 8, 0),
                data_source="logger",
            )
            session.add(other_activity)
            await session.flush()
            planned = PlannedSession(
                id=PLANNED_SESSION_ID,
                plan_id=plan.id,
                day_of_week=0,
                sport="gym",
                title="Squat Day",
                completed=True,
                activity_id=other_activity.id,
            )
            session.add(planned)
            await session.flush()

            ok = await cleanup(session)

            assert ok is False
            assert planned.completed is True
            assert planned.activity_id == other_activity.id

    async def test_refuses_when_planned_session_does_not_exist(self) -> None:
        async with test_session() as session:
            ok = await cleanup(session)

            assert ok is False

    async def test_refuses_when_planned_session_belongs_to_a_different_user(self) -> None:
        async with test_session() as session:
            await _create_user(session)  # id 1 — not the owner below
            other_user = User(
                id=2, email="other@example.com", name="Other", fitness_level="intermediate"
            )
            session.add(other_user)
            await session.flush()
            plan = WeeklyPlan(
                user_id=other_user.id, week_start=datetime(2026, 9, 21).date(), status="active"
            )
            session.add(plan)
            await session.flush()
            activity = Activity(
                id=ACTIVITY_ID,
                user_id=other_user.id,
                sport="gym",
                title="Morning Session",
                start_time=datetime(2026, 9, 21, 8, 9, 26),
                data_source="logger",
            )
            session.add(activity)
            planned = PlannedSession(
                id=PLANNED_SESSION_ID,
                plan_id=plan.id,
                day_of_week=0,
                sport="gym",
                title="Squat Day",
                completed=True,
                activity_id=ACTIVITY_ID,
            )
            session.add(planned)
            await session.flush()

            ok = await cleanup(session)

            assert ok is False
            assert planned.completed is True
            assert await session.get(Activity, ACTIVITY_ID) is not None

    async def test_refuses_when_activity_has_performed_sets(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            plan = WeeklyPlan(
                user_id=user_id, week_start=datetime(2026, 9, 21).date(), status="active"
            )
            session.add(plan)
            await session.flush()
            activity = Activity(
                id=ACTIVITY_ID,
                user_id=user_id,
                sport="gym",
                title="Actually a workout",
                start_time=datetime(2026, 9, 21, 8, 9, 26),
                data_source="logger",
            )
            session.add(activity)
            planned = PlannedSession(
                id=PLANNED_SESSION_ID,
                plan_id=plan.id,
                day_of_week=0,
                sport="gym",
                title="Squat Day",
                completed=True,
                activity_id=ACTIVITY_ID,
            )
            session.add(planned)
            await session.flush()
            session.add(
                GymWorkoutDetail(
                    activity_id=activity.id, exercise_title="Squat", set_index=1, reps=5
                )
            )
            await session.flush()

            ok = await cleanup(session)

            assert ok is False
            assert planned.completed is True
            assert await session.get(Activity, ACTIVITY_ID) is not None
