"""Tests for weekly plan generation in the coaching engine."""

import json
from datetime import date, time
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.plan import PlannedSession
from mycoach.models.prompt_log import PromptLog
from mycoach.models.user import User
from tests.conftest import test_session

VALID_PLAN_RESPONSE = json.dumps(
    {
        "summary": "Balanced week: upper/lower split gym + endurance swim.",
        "sessions": [
            {
                "day_of_week": 0,
                "sport": "gym",
                "title": "Upper Body Strength",
                "duration_minutes": 60,
                "details": {
                    "exercises": [
                        {
                            "name": "Bench Press",
                            "sets": 4,
                            "reps": "6-8",
                            "rpe": 8,
                            "rest_seconds": 120,
                            "notes": "Progressive overload from last week",
                        }
                    ]
                },
                "notes": "Focus on controlled eccentrics.",
            },
            {
                "day_of_week": 3,
                "sport": "swimming",
                "title": "Endurance Swim",
                "duration_minutes": 45,
                "details": {
                    "exercises": [
                        {
                            "name": "Freestyle intervals",
                            "sets": 8,
                            "reps": "100m",
                            "rest_seconds": 30,
                        }
                    ]
                },
                "notes": "Keep heart rate in zone 2.",
            },
        ],
    }
)


def _mock_llm_client(content: str = VALID_PLAN_RESPONSE) -> MagicMock:
    client = MagicMock()
    client.call.return_value = LLMResponse(
        content=content,
        model="claude-opus-4-6",
        input_tokens=2000,
        output_tokens=800,
        latency_ms=5000,
        estimated_cost_usd=0.09,
    )
    client.daily_model = "claude-sonnet-4-5-20250929"
    client.weekly_model = "claude-opus-4-6"
    return client


async def _setup_user_and_availability(session: object) -> tuple[int, date]:
    """Create user + availability slots, return (user_id, week_start)."""
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
            start_time=time(7, 0),
            duration_minutes=60,
            preferred_sport="gym",
        ),
        WeeklyAvailability(
            user_id=user.id,
            week_start=week_start,
            day_of_week=3,
            start_time=time(18, 0),
            duration_minutes=45,
            preferred_sport="swimming",
        ),
    ]
    for s in slots:
        session.add(s)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    return user.id, week_start


class TestGenerateWeeklyPlan:
    async def test_success(self) -> None:
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            plan = await engine.generate_weekly_plan(session, user_id, week_start)

            assert plan.status == "active"
            assert plan.week_start == week_start
            assert plan.user_id == user_id
            assert plan.summary == "Balanced week: upper/lower split gym + endurance swim."
            assert plan.prompt_version == "v1"

            # Verify sessions were created
            result = await session.execute(
                select(PlannedSession).where(PlannedSession.plan_id == plan.id)
            )
            sessions = list(result.scalars().all())
            assert len(sessions) == 2
            assert sessions[0].sport == "gym"
            assert sessions[1].sport == "swimming"

            # Verify weekly model was used
            mock_llm.call.assert_called_once()
            call_kwargs = mock_llm.call.call_args[1]
            assert call_kwargs["model"] == "claude-opus-4-6"
            assert call_kwargs["max_tokens"] == 8192

    async def test_logs_prompt(self) -> None:
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_weekly_plan(session, user_id, week_start)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.prompt_type == "weekly_plan"
            assert log.model == "claude-opus-4-6"
            assert log.success is True

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_weekly_plan(session, user_id, week_start)

            with pytest.raises(ValueError, match="already exists"):
                await engine.generate_weekly_plan(session, user_id, week_start)

    async def test_no_availability_raises(self) -> None:
        async with test_session() as session:
            user = User(email="test@example.com", name="Test", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(ValueError, match="No availability"):
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

    async def test_llm_failure_raises_and_logs(self) -> None:
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_weekly_plan(session, user_id, week_start)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.success is False
            assert log.prompt_type == "weekly_plan"

    async def test_session_details_stored_as_json(self) -> None:
        async with test_session() as session:
            user_id, week_start = await _setup_user_and_availability(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            plan = await engine.generate_weekly_plan(session, user_id, week_start)

            result = await session.execute(
                select(PlannedSession)
                .where(PlannedSession.plan_id == plan.id)
                .order_by(PlannedSession.day_of_week)
            )
            gym_session = result.scalars().first()
            assert gym_session is not None
            details = json.loads(gym_session.details)
            assert details["exercises"][0]["name"] == "Bench Press"
