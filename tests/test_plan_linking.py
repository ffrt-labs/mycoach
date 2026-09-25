"""Tests for linking an activity to the prescription it answers."""

from datetime import date, datetime

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from mycoach.plan_linking import link_activity_to_planned_session
from tests.conftest import test_session


async def _create_user(session: object) -> int:
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]
    return user.id


class TestLinkActivityToPlannedSession:
    async def test_links_and_marks_completed(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            plan = WeeklyPlan(
                user_id=user_id,
                week_start=date(2024, 6, 10),
                status="active",
                summary="Test",
            )
            session.add(plan)
            await session.flush()
            planned = PlannedSession(
                plan_id=plan.id,
                day_of_week=0,
                sport="gym",
                title="Test",
                duration_minutes=60,
            )
            session.add(planned)
            await session.flush()

            assert planned.completed is False
            assert planned.activity_id is None

            activity = Activity(
                user_id=user_id,
                sport="gym",
                title="Push",
                start_time=datetime(2024, 6, 10, 9, 0),
                data_source="logger",
            )
            session.add(activity)
            await session.flush()
            session.add(
                GymWorkoutDetail(
                    activity_id=activity.id, exercise_title="Bench Press", set_index=1, reps=8
                )
            )
            await session.flush()

            await link_activity_to_planned_session(session, activity.id, planned.id)

            assert planned.completed is True
            assert planned.activity_id == activity.id

    async def test_activity_without_performed_sets_does_not_complete(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            plan = WeeklyPlan(
                user_id=user_id, week_start=date(2024, 6, 10), status="active", summary="Test"
            )
            session.add(plan)
            await session.flush()
            planned = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Test", duration_minutes=60
            )
            session.add(planned)
            activity = Activity(
                user_id=user_id,
                sport="gym",
                title="Empty",
                start_time=datetime(2024, 6, 10, 9, 0),
                data_source="logger",
            )
            session.add(activity)
            await session.flush()

            await link_activity_to_planned_session(session, activity.id, planned.id)

            assert planned.completed is False
            assert planned.activity_id is None
