"""Tests for weekly plan context helpers."""

from datetime import date, datetime

from mycoach.coaching.context import (
    get_active_routine,
    get_availability_for_week,
    get_last_week_cardio_performance,
    get_recent_plan_summaries,
    get_sport_profiles,
    get_today_planned_sessions,
)
from mycoach.models.activity import Activity
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.routine import RoutineDay, RoutineExercise, WorkoutRoutine
from mycoach.models.sport_profile import SportProfile
from mycoach.models.user import User
from tests.conftest import test_session


class TestGetAvailabilityForWeek:
    async def test_returns_slots(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            week = date(2024, 6, 10)
            session.add(
                WeeklyAvailability(
                    user_id=user.id,
                    week_start=week,
                    day_of_week=0,
                    sport="gym",
                )
            )
            session.add(
                WeeklyAvailability(
                    user_id=user.id,
                    week_start=week,
                    day_of_week=3,
                    sport="swimming",
                )
            )
            await session.commit()

            result = await get_availability_for_week(session, user.id, week)
            assert len(result) == 2
            assert result[0]["day_name"] == "Monday"
            assert result[0]["sport"] == "gym"
            assert result[1]["day_name"] == "Thursday"
            assert result[1]["sport"] == "swimming"

    async def test_empty_when_no_slots(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            result = await get_availability_for_week(session, user.id, date(2024, 6, 10))
            assert result == []

    async def test_filters_by_week(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Add slots for two different weeks
            for week in [date(2024, 6, 10), date(2024, 6, 17)]:
                session.add(
                    WeeklyAvailability(
                        user_id=user.id,
                        week_start=week,
                        day_of_week=0,
                        sport="running",
                    )
                )
            await session.commit()

            result = await get_availability_for_week(session, user.id, date(2024, 6, 10))
            assert len(result) == 1


class TestGetActiveRoutine:
    async def test_includes_order_index(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            routine = WorkoutRoutine(user_id=user.id, name="PPL")
            day = RoutineDay(name="Push", order_index=0)
            day.exercises.append(
                RoutineExercise(
                    exercise_name="Bench Press", sets=4, rep_range="6-8", order_index=0
                )
            )
            routine.days.append(day)
            session.add(routine)
            await session.commit()

            result = await get_active_routine(session, user.id)
            assert result is not None
            assert result["days"][0]["order_index"] == 0
            assert result["days"][0]["day_of_week"] is None

    async def test_none_when_no_routine(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            result = await get_active_routine(session, user.id)
            assert result is None


class TestGetSportProfiles:
    async def test_returns_profiles(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            session.add(
                SportProfile(
                    user_id=user.id,
                    sport="gym",
                    skill_level="intermediate",
                    goals="Build muscle",
                )
            )
            session.add(
                SportProfile(
                    user_id=user.id,
                    sport="swimming",
                    skill_level="beginner",
                    goals="Improve endurance",
                )
            )
            await session.commit()

            result = await get_sport_profiles(session, user.id)
            assert len(result) == 2
            assert result[0]["sport"] == "gym"
            assert result[0]["skill_level"] == "intermediate"
            assert result[0]["goals"] == "Build muscle"
            assert result[1]["sport"] == "swimming"

    async def test_empty_when_no_profiles(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            result = await get_sport_profiles(session, user.id)
            assert result == []


class TestGetTodayPlannedSessions:
    async def test_returns_sessions_for_today(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Wednesday 2024-06-12, week_start = Monday 2024-06-10
            today = date(2024, 6, 12)
            plan = WeeklyPlan(
                user_id=user.id,
                week_start=date(2024, 6, 10),
                prompt_version="v2",
                status="active",
            )
            session.add(plan)
            await session.flush()

            session.add(
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=2,  # Wednesday
                    sport="running",
                    title="Easy Run",
                    duration_minutes=30,
                    track="cardio",
                )
            )
            session.add(
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=0,  # Monday — different day
                    sport="gym",
                    title="Push Day",
                    duration_minutes=60,
                    track="gym",
                )
            )
            # Matching availability for Wednesday
            session.add(
                WeeklyAvailability(
                    user_id=user.id,
                    week_start=date(2024, 6, 10),
                    day_of_week=2,
                    sport="running",
                )
            )
            await session.commit()

            result = await get_today_planned_sessions(session, user.id, today)
            assert len(result) == 1
            assert result[0]["title"] == "Easy Run"
            assert result[0]["sport"] == "running"

    async def test_empty_when_no_plan(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            result = await get_today_planned_sessions(session, user.id, date(2024, 6, 12))
            assert result == []

    async def test_filters_sessions_against_availability(self) -> None:
        """Sessions whose sport doesn't match current availability are filtered out."""
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Wednesday 2024-06-12, week_start = Monday 2024-06-10
            today = date(2024, 6, 12)
            week_start = date(2024, 6, 10)

            # Plan has swimming on Wednesday
            plan = WeeklyPlan(
                user_id=user.id,
                week_start=week_start,
                prompt_version="v2",
                status="active",
            )
            session.add(plan)
            await session.flush()

            session.add(
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=2,  # Wednesday
                    sport="swimming",
                    title="Pool Swim",
                    duration_minutes=45,
                    track="cardio",
                )
            )

            # But availability now says gym on Wednesday (user changed it)
            session.add(
                WeeklyAvailability(
                    user_id=user.id,
                    week_start=week_start,
                    day_of_week=2,
                    sport="gym",
                )
            )
            await session.commit()

            result = await get_today_planned_sessions(session, user.id, today)
            assert result == []

    async def test_returns_empty_when_no_availability(self) -> None:
        """If no availability slot exists for today, return empty list."""
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            today = date(2024, 6, 12)
            plan = WeeklyPlan(
                user_id=user.id,
                week_start=date(2024, 6, 10),
                prompt_version="v2",
                status="active",
            )
            session.add(plan)
            await session.flush()

            session.add(
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=2,
                    sport="running",
                    title="Easy Run",
                    duration_minutes=30,
                    track="cardio",
                )
            )
            await session.commit()

            # No availability set at all
            result = await get_today_planned_sessions(session, user.id, today)
            assert result == []


class TestGetRecentPlanSummaries:
    async def test_returns_summaries_with_adherence(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            plan = WeeklyPlan(
                user_id=user.id,
                week_start=date(2024, 6, 3),
                prompt_version="v2",
                status="active",
                summary="Week 1 plan",
            )
            session.add(plan)
            await session.flush()

            session.add(
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=0,
                    sport="gym",
                    title="Push",
                    duration_minutes=60,
                    completed=True,
                    track="gym",
                )
            )
            session.add(
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=3,
                    sport="running",
                    title="Run",
                    duration_minutes=30,
                    completed=False,
                    track="cardio",
                )
            )
            await session.commit()

            result = await get_recent_plan_summaries(
                session, user.id, weeks=4, before_date=date(2024, 6, 10)
            )
            assert len(result) == 1
            assert result[0]["summary"] == "Week 1 plan"
            assert result[0]["total_sessions"] == 2
            assert result[0]["completed_sessions"] == 1
            assert result[0]["adherence_pct"] == 50.0

    async def test_empty_when_no_plans(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            result = await get_recent_plan_summaries(
                session, user.id, before_date=date(2024, 6, 10)
            )
            assert result == []


class TestGetLastWeekCardioPerformance:
    async def test_includes_running_and_swimming(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Previous week activities (week_start = June 10, prev week = June 3-9)
            for sport, title, day in [
                ("running", "Easy Run", 4),
                ("swimming", "Pool Swim", 5),
                ("cardio", "Cycling", 6),
                ("gym", "Push Day", 3),
            ]:
                session.add(
                    Activity(
                        user_id=user.id,
                        sport=sport,
                        title=title,
                        start_time=datetime(2024, 6, day, 8, 0),
                        data_source="garmin",
                        distance_meters=5000.0 if sport != "gym" else None,
                    )
                )
            await session.commit()

            result = await get_last_week_cardio_performance(
                session, user.id, date(2024, 6, 10)
            )
            sports = [a["sport"] for a in result]
            assert "running" in sports
            assert "swimming" in sports
            assert "cardio" in sports
            assert "gym" not in sports
            assert len(result) == 3

    async def test_includes_distance_in_results(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            session.add(
                Activity(
                    user_id=user.id,
                    sport="running",
                    title="Morning Run",
                    start_time=datetime(2024, 6, 5, 7, 0),
                    duration_minutes=30,
                    distance_meters=5200.0,
                    avg_speed_mps=2.89,
                    data_source="garmin",
                )
            )
            await session.commit()

            result = await get_last_week_cardio_performance(
                session, user.id, date(2024, 6, 10)
            )
            assert len(result) == 1
            assert result[0]["distance_meters"] == 5200.0
            assert result[0]["avg_speed_mps"] == 2.89
