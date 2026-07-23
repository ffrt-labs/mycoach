"""Tests for weekly plan generation in the coaching engine."""

import json
from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import PipelineSkip
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.plan import PlannedSession
from mycoach.models.prompt_log import PromptLog
from mycoach.models.routine import RoutineDay, RoutineExercise, WorkoutRoutine
from mycoach.models.user import User
from tests.conftest import test_session

# Cardio plan response (used when no routine — all slots go to cardio track)
VALID_CARDIO_RESPONSE = json.dumps(
    {
        "sessions": [
            {
                "day_of_week": 0,
                "sport": "running",
                "title": "Easy Recovery Run",
                "duration_minutes": 60,
                "details": {"type": "easy", "description": "Zone 2 easy run"},
                "notes": "Keep HR under 140.",
            },
            {
                "day_of_week": 3,
                "sport": "swimming",
                "title": "Endurance Swim",
                "duration_minutes": 45,
                "details": {"type": "endurance", "description": "Freestyle steady"},
                "notes": "Keep heart rate in zone 2.",
            },
        ],
        "goal_assessment": "On track for weekly targets.",
        "weekly_summary": "Balanced cardio week: easy run + endurance swim.",
    }
)

# Gym adjustment response (used when routine exists)
VALID_GYM_ADJUSTMENT = json.dumps(
    {
        "exercises": [
            {
                "exercise_name": "Bench Press",
                "target_weight_kg": 80.0,
                "target_rpe": 7,
                "rest_seconds": 120,
                "adjustment_rationale": "Progressing 2.5kg from last week",
                "notes": "Control the eccentric",
            }
        ],
        "session_notes": "Focus on compound movements.",
        "estimated_duration_minutes": 60,
    }
)


def _mock_llm_client(cardio_content: str = VALID_CARDIO_RESPONSE) -> MagicMock:
    """Mock LLM client that returns cardio response (for all-cardio flow)."""
    client = MagicMock()
    client.call.return_value = LLMResponse(
        content=cardio_content,
        model="claude-opus-4-6",
        input_tokens=2000,
        output_tokens=800,
        latency_ms=5000,
        estimated_cost_usd=0.09,
    )
    client.daily_model = "claude-sonnet-4-5-20250929"
    client.weekly_model = "claude-opus-4-6"
    return client


def _mock_llm_two_track() -> MagicMock:
    """Mock LLM client: gym adjustment → cardio plan (2 calls, no schedule distribution)."""
    client = MagicMock()
    cardio_for_saturday = json.dumps(
        {
            "sessions": [
                {
                    "day_of_week": 5,
                    "sport": "running",
                    "title": "Easy Recovery Run",
                    "duration_minutes": 45,
                    "details": {"type": "easy", "description": "Zone 2 easy run"},
                    "notes": "Keep HR under 140.",
                },
            ],
            "goal_assessment": "On track for weekly targets.",
            "weekly_summary": "Light cardio week with a Saturday run.",
        }
    )
    client.call.side_effect = [
        # 1st call: gym adjustment (daily model)
        LLMResponse(
            content=VALID_GYM_ADJUSTMENT,
            model="claude-sonnet-4-5-20250929",
            input_tokens=1500,
            output_tokens=400,
            latency_ms=2000,
            estimated_cost_usd=0.02,
        ),
        # 2nd call: cardio plan (weekly model)
        LLMResponse(
            content=cardio_for_saturday,
            model="claude-opus-4-6",
            input_tokens=2000,
            output_tokens=800,
            latency_ms=5000,
            estimated_cost_usd=0.09,
        ),
    ]
    client.daily_model = "claude-sonnet-4-5-20250929"
    client.weekly_model = "claude-opus-4-6"
    return client


async def _setup_user_and_availability(session: object) -> tuple[int, date]:
    """Create user + availability slots (cardio sports), return (user_id, week_start)."""
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]

    week_start = date(2024, 6, 10)  # a Monday
    slots = [
        WeeklyAvailability(
            user_id=user.id,
            week_start=week_start,
            day_of_week=0,
            sport="running",
        ),
        WeeklyAvailability(
            user_id=user.id,
            week_start=week_start,
            day_of_week=3,
            sport="swimming",
        ),
    ]
    for s in slots:
        session.add(s)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    return user.id, week_start


async def _setup_with_routine(session: object) -> tuple[int, date]:
    """Create user + availability (gym Mon, running Sat) + routine, return (user_id, week_start)."""
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]

    week_start = date(2024, 6, 10)  # a Monday
    slots = [
        WeeklyAvailability(
            user_id=user.id,
            week_start=week_start,
            day_of_week=0,
            sport="gym",
        ),
        WeeklyAvailability(
            user_id=user.id,
            week_start=week_start,
            day_of_week=5,
            sport="running",
        ),
    ]
    for s in slots:
        session.add(s)  # type: ignore[union-attr]

    # Create routine with one day
    routine = WorkoutRoutine(user_id=user.id, name="PPL Split")
    day = RoutineDay(name="Push Day", order_index=0)
    day.exercises.append(
        RoutineExercise(exercise_name="Bench Press", sets=4, rep_range="6-8", order_index=0)
    )
    routine.days.append(day)
    session.add(routine)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]

    return user.id, week_start


class TestGenerateWeeklyPlan:
    async def test_all_cardio_no_routine(self) -> None:
        """Without a routine, all slots go to cardio track."""
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            plan = await engine.generate_weekly_plan(session, user_id, week_start)

            assert plan.status == "active"
            assert plan.week_start == week_start
            assert plan.prompt_version == "v2"

            result = await session.execute(
                select(PlannedSession)
                .where(PlannedSession.plan_id == plan.id)
                .order_by(PlannedSession.day_of_week)
            )
            sessions = list(result.scalars().all())
            assert len(sessions) == 2
            # All sessions should be cardio track
            assert all(s.track == "cardio" for s in sessions)

            # Weekly model used for cardio
            mock_llm.call.assert_called_once()
            call_kwargs = mock_llm.call.call_args[1]
            assert call_kwargs["model"] == "claude-opus-4-6"

    async def test_two_track_with_routine(self) -> None:
        """With a routine, gym slots go to gym track, rest to cardio."""
        async with test_session() as session:
            user_id, week_start = await _setup_with_routine(session)

            mock_llm = _mock_llm_two_track()
            engine = CoachingEngine(llm_client=mock_llm)
            plan = await engine.generate_weekly_plan(session, user_id, week_start)

            assert plan.status == "active"

            result = await session.execute(
                select(PlannedSession)
                .where(PlannedSession.plan_id == plan.id)
                .order_by(PlannedSession.day_of_week)
            )
            sessions = list(result.scalars().all())
            assert len(sessions) == 2

            # Monday = gym track (sport="gym")
            gym_session = next(s for s in sessions if s.day_of_week == 0)
            assert gym_session.track == "gym"
            assert gym_session.sport == "gym"
            assert gym_session.title == "Push Day"
            # Verify details include weight targets
            details = json.loads(gym_session.details)
            assert details["exercises"][0]["target_weight_kg"] == 80.0

            # Saturday = cardio track (sport="running")
            cardio_session = next(s for s in sessions if s.day_of_week == 5)
            assert cardio_session.track == "cardio"

            # Two LLM calls: gym adjustment + cardio plan (no schedule distribution)
            assert mock_llm.call.call_count == 2

    async def test_logs_prompts(self) -> None:
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_weekly_plan(session, user_id, week_start)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.prompt_type == "cardio_plan"
            assert log.success is True

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_weekly_plan(session, user_id, week_start)

            with pytest.raises(PipelineSkip, match="already exists"):
                await engine.generate_weekly_plan(session, user_id, week_start)

    async def test_no_availability_raises(self) -> None:
        async with test_session() as session:
            user = User(email="test@example.com", name="Test", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(PipelineSkip, match="No availability"):
                await engine.generate_weekly_plan(session, user.id, date(2024, 6, 10))

    async def test_non_monday_raises(self) -> None:
        async with test_session() as session:
            user = User(email="test@example.com", name="Test", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            engine = CoachingEngine(llm_client=_mock_llm_client())
            with pytest.raises(ValueError, match="Monday"):
                await engine.generate_weekly_plan(
                    session,
                    user.id,
                    date(2024, 6, 12),  # Wednesday
                )

    async def test_llm_failure_creates_fallback_sessions(self) -> None:
        """When LLM fails, fallback placeholder sessions are created."""
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            plan = await engine.generate_weekly_plan(session, user_id, week_start)

            result = await session.execute(
                select(PlannedSession).where(PlannedSession.plan_id == plan.id)
            )
            sessions = list(result.scalars().all())
            # Fallback sessions should be created
            assert len(sessions) == 2
            assert all("failed" in (s.notes or "").lower() for s in sessions)

            # Prompt log should record failure
            log_result = await session.execute(select(PromptLog))
            log = log_result.scalar_one()
            assert log.success is False

    async def test_gym_session_details_stored_as_json(self) -> None:
        async with test_session() as session:
            user_id, week_start = await _setup_with_routine(session)

            mock_llm = _mock_llm_two_track()
            engine = CoachingEngine(llm_client=mock_llm)
            plan = await engine.generate_weekly_plan(session, user_id, week_start)

            result = await session.execute(
                select(PlannedSession).where(
                    PlannedSession.plan_id == plan.id,
                    PlannedSession.track == "gym",
                )
            )
            gym_session = result.scalars().first()
            assert gym_session is not None
            details = json.loads(gym_session.details)
            assert details["exercises"][0]["name"] == "Bench Press"
            assert details["exercises"][0]["target_weight_kg"] == 80.0
            rationale = details["exercises"][0]["adjustment_rationale"]
            assert rationale == "Progressing 2.5kg from last week"

    async def test_all_gym_slots_no_cardio(self) -> None:
        """When all slots are gym, only gym adjustment calls are made."""
        async with test_session() as session:
            user = User(
                email="test@example.com", name="Test User", fitness_level="intermediate"
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            week_start = date(2024, 6, 10)
            slot = WeeklyAvailability(
                user_id=user.id,
                week_start=week_start,
                day_of_week=0,
                sport="gym",
            )
            session.add(slot)

            routine = WorkoutRoutine(user_id=user.id, name="Full Body")
            day = RoutineDay(name="Full Body Day", order_index=0)
            day.exercises.append(
                RoutineExercise(
                    exercise_name="Squat", sets=4, rep_range="6-8", order_index=0
                )
            )
            routine.days.append(day)
            session.add(routine)
            await session.commit()

            gym_resp = json.dumps(
                {
                    "exercises": [
                        {
                            "exercise_name": "Squat",
                            "target_weight_kg": 100.0,
                            "target_rpe": 8,
                            "rest_seconds": 180,
                            "adjustment_rationale": "Progressive overload",
                        }
                    ],
                    "session_notes": "Heavy day.",
                    "estimated_duration_minutes": 60,
                }
            )
            client = MagicMock()
            client.call.return_value = LLMResponse(
                content=gym_resp,
                model="claude-sonnet-4-5-20250929",
                input_tokens=1500,
                output_tokens=400,
                latency_ms=2000,
                estimated_cost_usd=0.02,
            )
            client.daily_model = "claude-sonnet-4-5-20250929"
            client.weekly_model = "claude-opus-4-6"

            engine = CoachingEngine(llm_client=client)
            plan = await engine.generate_weekly_plan(session, user.id, week_start)

            # Only 1 call (gym adjustment), no cardio plan
            assert client.call.call_count == 1
            result = await session.execute(
                select(PlannedSession).where(PlannedSession.plan_id == plan.id)
            )
            sessions = list(result.scalars().all())
            assert len(sessions) == 1
            assert sessions[0].track == "gym"

    async def test_sport_grouping_direct(self) -> None:
        """Slots are grouped by sport without LLM schedule distribution."""
        async with test_session() as session:
            user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            week_start = date(2024, 6, 10)
            # 3 slots: gym, swimming, running
            for dow, sport in [(0, "gym"), (2, "swimming"), (4, "running")]:
                session.add(
                    WeeklyAvailability(
                        user_id=user.id,
                        week_start=week_start,
                        day_of_week=dow,
                        sport=sport,
                    )
                )

            routine = WorkoutRoutine(user_id=user.id, name="PPL")
            day = RoutineDay(name="Push Day", order_index=0)
            day.exercises.append(
                RoutineExercise(
                    exercise_name="Bench Press", sets=3, rep_range="8-10", order_index=0
                )
            )
            routine.days.append(day)
            session.add(routine)
            await session.commit()

            cardio_resp = json.dumps(
                {
                    "sessions": [
                        {
                            "day_of_week": 2,
                            "sport": "swimming",
                            "title": "Swim",
                            "duration_minutes": 60,
                            "details": {},
                            "notes": None,
                        },
                        {
                            "day_of_week": 4,
                            "sport": "running",
                            "title": "Easy Run",
                            "duration_minutes": 60,
                            "details": {},
                            "notes": None,
                        },
                    ],
                    "goal_assessment": "Good.",
                    "weekly_summary": "Mixed cardio.",
                }
            )
            client = MagicMock()
            client.call.side_effect = [
                LLMResponse(
                    content=VALID_GYM_ADJUSTMENT,
                    model="claude-sonnet-4-5-20250929",
                    input_tokens=1500,
                    output_tokens=400,
                    latency_ms=2000,
                    estimated_cost_usd=0.02,
                ),
                LLMResponse(
                    content=cardio_resp,
                    model="claude-opus-4-6",
                    input_tokens=2000,
                    output_tokens=800,
                    latency_ms=5000,
                    estimated_cost_usd=0.09,
                ),
            ]
            client.daily_model = "claude-sonnet-4-5-20250929"
            client.weekly_model = "claude-opus-4-6"

            engine = CoachingEngine(llm_client=client)
            plan = await engine.generate_weekly_plan(session, user.id, week_start)

            result = await session.execute(
                select(PlannedSession)
                .where(PlannedSession.plan_id == plan.id)
                .order_by(PlannedSession.day_of_week)
            )
            sessions = list(result.scalars().all())
            assert len(sessions) == 3

            # Exactly 2 LLM calls: gym_adjustment + cardio_plan
            assert client.call.call_count == 2

            gym_s = next(s for s in sessions if s.day_of_week == 0)
            assert gym_s.track == "gym"
            assert gym_s.sport == "gym"

            swim_s = next(s for s in sessions if s.day_of_week == 2)
            assert swim_s.track == "cardio"

            run_s = next(s for s in sessions if s.day_of_week == 4)
            assert run_s.track == "cardio"
