"""Tests for weekly recap engine method."""

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.activity import Activity
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.prompt_log import PromptLog
from mycoach.models.user import User
from tests.conftest import test_session

VALID_RECAP_RESPONSE = """{
  "week_summary": "Strong training week with 4/5 sessions completed.",
  "adherence_analysis": "Missed Friday padel due to weather.",
  "performance_highlights": ["New bench press PR at 85kg", "Improved 100m swim time"],
  "areas_of_concern": ["Elevated Resting Heart Rate mid-week"],
  "recovery_assessment": "Sleep quality dipped mid-week.",
  "training_load_analysis": "Good distribution across gym and swimming.",
  "gym_coaching": [{"day_label": "Day 1 (Push)", "exercises": ["Bench press progressing well", "Squat stagnating at 120kg for 3 weeks"]}],
  "exercise_substitutions": ["Squat → Bulgarian Split Squat because plateau at 120kg"],
  "cardio_coaching": [{"sport": "Swimming", "analysis": "Pace steady at 2:30/100m.", "recommendation": "Add one fartlek session next week."}],
  "coach_recommendations": ["Deload on squat", "Add fartlek swim", "Sleep earlier"],
  "next_week_recommendations": "Reduce gym volume by 10%.",
  "mesocycle_progress": "Week 3 of 4 in build phase."
}"""


def _mock_llm_client(content: str = VALID_RECAP_RESPONSE) -> MagicMock:
    client = MagicMock()
    client.call.return_value = LLMResponse(
        content=content,
        model="claude-sonnet-4-5-20250929",
        input_tokens=800,
        output_tokens=300,
        latency_ms=1200,
        estimated_cost_usd=0.006,
    )
    client.daily_model = "claude-sonnet-4-5-20250929"
    client.weekly_model = "claude-opus-4-6"
    return client


async def _create_user(session: object) -> int:
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]
    return user.id


class TestGenerateWeeklyRecap:
    async def test_success(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)  # Monday

            # Add an activity in the week
            session.add(
                Activity(
                    user_id=user_id,
                    title="Gym",
                    sport="gym",
                    start_time=datetime(2024, 6, 11, 8, 0),
                    data_source="hevy",
                )
            )
            await session.commit()

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_weekly_recap(session, user_id, week_start)

            assert insight.insight_type == "weekly_recap"
            assert insight.insight_date == week_start
            assert insight.user_id == user_id
            assert "85kg" in insight.content
            mock_llm.call.assert_called_once()

    async def test_logs_prompt(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_weekly_recap(session, user_id, week_start)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.prompt_type == "weekly_recap"
            assert log.model == "claude-sonnet-4-5-20250929"
            assert log.success is True

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_weekly_recap(session, user_id, week_start)

            with pytest.raises(ValueError, match="already exists"):
                await engine.generate_weekly_recap(session, user_id, week_start)

    async def test_non_monday_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(ValueError, match="must be a Monday"):
                await engine.generate_weekly_recap(session, user_id, date(2024, 6, 12))

    async def test_llm_failure_raises_and_logs(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_weekly_recap(session, user_id, week_start)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.success is False
            assert "API timeout" in (log.error or "")

    async def test_with_plan_adherence(self) -> None:
        """Engine should include plan adherence data in prompt when plan exists."""
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)

            plan = WeeklyPlan(
                user_id=user_id,
                week_start=week_start,
                status="active",
                summary="Test plan",
                prompt_version="v1",
            )
            session.add(plan)
            await session.flush()
            session.add(
                PlannedSession(
                    plan_id=plan.id, day_of_week=0, sport="gym", title="Push", completed=True
                )
            )
            await session.commit()

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_weekly_recap(session, user_id, week_start)

            assert insight.insight_type == "weekly_recap"
            # Verify the prompt included adherence data
            call_args = mock_llm.call.call_args
            msg = call_args.kwargs.get("user_message", call_args[1].get("user_message", ""))
            assert "1/1" in msg
