"""Tests for weekly recap context functions."""

from datetime import date, datetime

from mycoach.coaching.context import (
    get_activities_for_week,
    get_gym_details_for_week,
    get_gym_performance_history,
    get_plan_adherence_for_week,
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


WEEK = date(2024, 6, 10)


async def _plan(session: object) -> tuple[int, int]:
    user_id = await _create_user(session)
    plan = WeeklyPlan(
        user_id=user_id, week_start=WEEK, status="active", summary="p", prompt_version="v1"
    )
    session.add(plan)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    return user_id, plan.id


async def _gym(session: object, user_id: int, sets: int, day: int = 10) -> Activity:
    activity = Activity(
        user_id=user_id,
        title="Gym",
        sport="gym",
        start_time=datetime(2024, 6, day, 9, 0),
        data_source="hevy",
    )
    session.add(activity)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    for i in range(sets):
        session.add(  # type: ignore[union-attr]
            GymWorkoutDetail(activity_id=activity.id, exercise_title="Bench", set_index=i + 1)
        )
    await session.flush()  # type: ignore[union-attr]
    return activity


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

    async def test_auto_detects_completion_from_activities(self) -> None:
        """Sessions not marked completed should be detected via matching activities."""
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

            # Both sessions have completed=False
            s1 = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Push", completed=False
            )
            s2 = PlannedSession(
                plan_id=plan.id, day_of_week=2, sport="swimming", title="Swim", completed=False
            )
            session.add_all([s1, s2])

            # Activity on Monday (day_of_week=0) for gym — should auto-detect as done
            a1 = Activity(
                user_id=user_id,
                title="Gym Session",
                sport="gym",
                start_time=datetime(2024, 6, 10, 9, 0),
                data_source="hevy",
            )
            session.add(a1)
            await session.flush()
            session.add(GymWorkoutDetail(activity_id=a1.id, exercise_title="Bench", set_index=1))
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, week_start)
            assert result is not None
            assert result["total_sessions"] == 2
            assert result["completed_sessions"] == 1
            assert result["adherence_pct"] == 50.0

            # Check per-session: gym=DONE, swimming=MISSED
            sessions_by_sport = {s["sport"]: s for s in result["sessions"]}
            assert sessions_by_sport["gym"]["completed"] is True
            assert sessions_by_sport["swimming"]["completed"] is False

    async def test_shifted_day_still_counts(self) -> None:
        """A gym session planned Tuesday but lifted Wednesday must still count as done."""
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

            # Planned for Tuesday (day_of_week=1)
            s1 = PlannedSession(
                plan_id=plan.id, day_of_week=1, sport="gym", title="Push", completed=False
            )
            session.add(s1)

            # Actually lifted Wednesday (day_of_week=2)
            a1 = Activity(
                user_id=user_id,
                title="Gym Session",
                sport="gym",
                start_time=datetime(2024, 6, 12, 9, 0),
                data_source="hevy",
            )
            session.add(a1)
            await session.flush()
            session.add(GymWorkoutDetail(activity_id=a1.id, exercise_title="Bench", set_index=1))
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, week_start)
            assert result is not None
            assert result["completed_sessions"] == 1
            assert result["adherence_pct"] == 100.0

    async def test_greedy_one_activity_per_session(self) -> None:
        """One activity consumes exactly one planned session of that sport."""
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

            # Two planned gym sessions...
            s1 = PlannedSession(
                plan_id=plan.id, day_of_week=1, sport="gym", title="Push", completed=False
            )
            s2 = PlannedSession(
                plan_id=plan.id, day_of_week=3, sport="gym", title="Pull", completed=False
            )
            session.add_all([s1, s2])

            # ...but only one gym activity happened
            a1 = Activity(
                user_id=user_id,
                title="Gym Session",
                sport="gym",
                start_time=datetime(2024, 6, 12, 9, 0),
                data_source="hevy",
            )
            session.add(a1)
            await session.flush()
            session.add(GymWorkoutDetail(activity_id=a1.id, exercise_title="Bench", set_index=1))
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, week_start)
            assert result is not None
            assert result["total_sessions"] == 2
            assert result["completed_sessions"] == 1
            assert result["adherence_pct"] == 50.0

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

    async def test_empty_gym_activity_does_not_complete_session(self) -> None:
        async with test_session() as session:
            user_id, plan_id = await _plan(session)
            session.add(PlannedSession(plan_id=plan_id, day_of_week=0, sport="gym", title="Push"))
            await _gym(session, user_id, sets=0)
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, WEEK)
            assert result is not None
            assert result["completed_sessions"] == 0

    async def test_empty_linked_activity_does_not_read_done(self) -> None:
        async with test_session() as session:
            user_id, plan_id = await _plan(session)
            empty = await _gym(session, user_id, sets=0)
            session.add(
                PlannedSession(
                    plan_id=plan_id,
                    day_of_week=0,
                    sport="gym",
                    title="Push",
                    completed=True,
                    activity_id=empty.id,
                )
            )
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, WEEK)
            assert result is not None
            assert result["completed_sessions"] == 0

    async def test_linked_activity_is_consumed_before_pool(self) -> None:
        """Session 2's link must not lose its activity to session 1 (earlier day)."""
        async with test_session() as session:
            user_id, plan_id = await _plan(session)
            first = await _gym(session, user_id, sets=1, day=10)
            second = await _gym(session, user_id, sets=1, day=11)
            session.add_all(
                [
                    PlannedSession(plan_id=plan_id, day_of_week=0, sport="gym", title="A"),
                    PlannedSession(
                        plan_id=plan_id,
                        day_of_week=1,
                        sport="gym",
                        title="B",
                        completed=True,
                        activity_id=second.id,
                    ),
                ]
            )
            await session.commit()
            assert first.id != second.id

            result = await get_plan_adherence_for_week(session, user_id, WEEK)
            assert result is not None
            assert [s["completed"] for s in result["sessions"]] == [True, True]

    async def test_one_activity_linked_twice_credits_one_session(self) -> None:
        async with test_session() as session:
            user_id, plan_id = await _plan(session)
            act = await _gym(session, user_id, sets=1)
            session.add_all(
                [
                    PlannedSession(
                        plan_id=plan_id, day_of_week=0, sport="gym", title="A", activity_id=act.id
                    ),
                    PlannedSession(
                        plan_id=plan_id, day_of_week=2, sport="gym", title="B", activity_id=act.id
                    ),
                ]
            )
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, WEEK)
            assert result is not None
            assert result["completed_sessions"] == 1

    async def test_unlinked_activity_returns_to_pool(self) -> None:
        async with test_session() as session:
            user_id, plan_id = await _plan(session)
            await _gym(session, user_id, sets=1)
            session.add(PlannedSession(plan_id=plan_id, day_of_week=3, sport="gym", title="Pull"))
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, WEEK)
            assert result is not None
            assert result["completed_sessions"] == 1

    async def test_manual_complete_consumes_nothing(self) -> None:
        async with test_session() as session:
            user_id, plan_id = await _plan(session)
            await _gym(session, user_id, sets=1)
            session.add_all(
                [
                    PlannedSession(
                        plan_id=plan_id, day_of_week=0, sport="gym", title="A", completed=True
                    ),
                    PlannedSession(plan_id=plan_id, day_of_week=2, sport="gym", title="B"),
                ]
            )
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, WEEK)
            assert result is not None
            assert result["completed_sessions"] == 2

    async def test_non_gym_activity_still_counts_without_sets(self) -> None:
        async with test_session() as session:
            user_id, plan_id = await _plan(session)
            session.add(
                PlannedSession(plan_id=plan_id, day_of_week=0, sport="running", title="Run")
            )
            session.add(
                Activity(
                    user_id=user_id,
                    title="Run",
                    sport="running",
                    start_time=datetime(2024, 6, 11, 7, 0),
                    data_source="garmin",
                )
            )
            await session.commit()

            result = await get_plan_adherence_for_week(session, user_id, WEEK)
            assert result is not None
            assert result["completed_sessions"] == 1


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


class TestGetGymPerformanceHistory:
    async def test_aggregates_by_id_and_skips_custom_exercises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity = Activity(
                user_id=user_id,
                title="Legs",
                sport="gym",
                start_time=datetime(2024, 6, 10, 9, 0),
                data_source="logger",
            )
            session.add(activity)
            await session.flush()
            session.add_all(
                [
                    GymWorkoutDetail(
                        activity_id=activity.id,
                        exercise_id="Barbell_Squat",
                        exercise_title="Barbell Squat",
                        set_index=1,
                        weight_kg=100,
                        reps=5,
                    ),
                    GymWorkoutDetail(
                        activity_id=activity.id,
                        exercise_id="Barbell_Squat",
                        exercise_title="Back Squat",
                        set_index=2,
                        weight_kg=105,
                        reps=3,
                    ),
                    GymWorkoutDetail(
                        activity_id=activity.id,
                        exercise_id=None,
                        exercise_title="Unlisted Machine",
                        set_index=3,
                        weight_kg=50,
                        reps=10,
                    ),
                ]
            )
            await session.commit()

            result = await get_gym_performance_history(session, user_id, date(2024, 6, 17), weeks=1)

            assert result == [
                {
                    "week_start": "2024-06-10",
                    "exercise_id": "Barbell_Squat",
                    "exercise_title": "Barbell Squat",
                    "best_weight_kg": 105,
                    "best_reps": 3,
                    "total_sets": 2,
                    "avg_rpe": None,
                }
            ]

    async def test_raw_recap_details_keep_custom_exercise_title(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity = Activity(
                user_id=user_id,
                title="Accessories",
                sport="gym",
                start_time=datetime(2024, 6, 11, 9, 0),
                data_source="logger",
            )
            session.add(activity)
            await session.flush()
            session.add(
                GymWorkoutDetail(
                    activity_id=activity.id,
                    exercise_id=None,
                    exercise_title="Unlisted Machine",
                    set_index=1,
                )
            )
            await session.commit()

            result = await get_gym_details_for_week(session, user_id, date(2024, 6, 10))

            assert result[0]["exercise_id"] is None
            assert result[0]["exercise_title"] == "Unlisted Machine"
