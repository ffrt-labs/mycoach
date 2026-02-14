"""Tests for post-workout context queries."""

from datetime import date, datetime

import pytest

from mycoach.coaching.context import (
    find_matching_planned_session,
    get_activity_with_details,
    get_similar_activities,
    link_activity_to_planned_session,
)
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: object) -> int:
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]
    return user.id


class TestGetActivityWithDetails:
    async def test_gym_activity_with_details(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity = Activity(
                user_id=user_id,
                sport="gym",
                title="Upper Body",
                start_time=datetime(2024, 6, 10, 9, 0),
                duration_minutes=60,
                avg_hr=130,
                max_hr=165,
                data_source="hevy",
            )
            session.add(activity)
            await session.flush()

            session.add(
                GymWorkoutDetail(
                    activity_id=activity.id,
                    exercise_title="Bench Press",
                    set_index=1,
                    set_type="normal",
                    weight_kg=80.0,
                    reps=8,
                    rpe=7.5,
                )
            )
            await session.commit()

            act_dict, gym_details = await get_activity_with_details(session, activity.id, user_id)
            assert act_dict["sport"] == "gym"
            assert act_dict["title"] == "Upper Body"
            assert act_dict["id"] == activity.id
            assert len(gym_details) == 1
            assert gym_details[0]["exercise_title"] == "Bench Press"
            assert gym_details[0]["weight_kg"] == 80.0

    async def test_non_gym_activity_no_details(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity = Activity(
                user_id=user_id,
                sport="swimming",
                title="Pool Swim",
                start_time=datetime(2024, 6, 10, 7, 0),
                duration_minutes=45,
                data_source="garmin",
            )
            session.add(activity)
            await session.commit()

            act_dict, gym_details = await get_activity_with_details(session, activity.id, user_id)
            assert act_dict["sport"] == "swimming"
            assert gym_details == []

    async def test_not_found_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            with pytest.raises(ValueError, match="not found"):
                await get_activity_with_details(session, 999, user_id)


class TestFindMatchingPlannedSession:
    async def test_finds_matching_session(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            # Monday
            week_start = date(2024, 6, 10)
            plan = WeeklyPlan(
                user_id=user_id,
                week_start=week_start,
                status="active",
                summary="Test plan",
            )
            session.add(plan)
            await session.flush()

            planned = PlannedSession(
                plan_id=plan.id,
                day_of_week=2,  # Wednesday
                sport="gym",
                title="Upper Body",
                duration_minutes=60,
            )
            session.add(planned)
            await session.commit()

            # Activity on Wednesday of that week
            activity_dict = {
                "sport": "gym",
                "start_time": "2024-06-12 09:00:00",
            }
            result = await find_matching_planned_session(session, activity_dict, user_id)
            assert result is not None
            assert result["title"] == "Upper Body"
            assert result["sport"] == "gym"

    async def test_no_matching_plan(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_dict = {
                "sport": "gym",
                "start_time": "2024-06-12 09:00:00",
            }
            result = await find_matching_planned_session(session, activity_dict, user_id)
            assert result is None

    async def test_no_matching_sport(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)
            plan = WeeklyPlan(
                user_id=user_id,
                week_start=week_start,
                status="active",
                summary="Test plan",
            )
            session.add(plan)
            await session.flush()
            session.add(
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=2,
                    sport="swimming",
                    title="Pool Swim",
                    duration_minutes=45,
                )
            )
            await session.commit()

            activity_dict = {
                "sport": "gym",
                "start_time": "2024-06-12 09:00:00",
            }
            result = await find_matching_planned_session(session, activity_dict, user_id)
            assert result is None


class TestGetSimilarActivities:
    async def test_returns_same_sport(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            a1 = Activity(
                user_id=user_id,
                sport="gym",
                title="Workout 1",
                start_time=datetime(2024, 6, 8, 9, 0),
                data_source="hevy",
            )
            a2 = Activity(
                user_id=user_id,
                sport="gym",
                title="Workout 2",
                start_time=datetime(2024, 6, 9, 9, 0),
                data_source="hevy",
            )
            a3 = Activity(
                user_id=user_id,
                sport="swimming",
                title="Swim",
                start_time=datetime(2024, 6, 9, 7, 0),
                data_source="garmin",
            )
            session.add_all([a1, a2, a3])
            await session.commit()

            # Exclude a2, should only get a1 (swimming excluded by sport filter)
            similar = await get_similar_activities(session, user_id, "gym", a2.id)
            assert len(similar) == 1
            assert similar[0]["title"] == "Workout 1"

    async def test_excludes_current(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            a1 = Activity(
                user_id=user_id,
                sport="gym",
                title="Only Workout",
                start_time=datetime(2024, 6, 10, 9, 0),
                data_source="hevy",
            )
            session.add(a1)
            await session.commit()

            similar = await get_similar_activities(session, user_id, "gym", a1.id)
            assert len(similar) == 0


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

            await link_activity_to_planned_session(session, 42, planned.id)

            assert planned.completed is True
            assert planned.activity_id == 42
