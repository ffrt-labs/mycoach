"""Tests for post-workout analysis in coaching engine."""

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.prompt_log import PromptLog
from mycoach.models.user import User
from tests.conftest import test_session

VALID_POST_WORKOUT_RESPONSE = """{
  "performance_summary": "Solid upper body session with good volume and progressive overload.",
  "planned_vs_actual": "Completed all planned exercises. Bench press matched target weight.",
  "performance_trends": "Bench press up 2.5kg from last session, showing steady progression.",
  "hr_analysis": "Average HR 130bpm, stayed in zone 2-3 as expected for strength training.",
  "training_effect_assessment": "Aerobic effect 3.2 indicates good cardiovascular stimulus.",
  "key_highlights": ["Bench press PR at 82.5kg", "Good form on all sets"],
  "areas_for_improvement": ["Rest times could be more consistent"],
  "next_session_recommendations": "Increase bench press to 85kg for 3x6.",
  "recovery_notes": "Moderate session, aim for 7+ hours sleep tonight."
}"""


def _mock_llm_client(content: str = VALID_POST_WORKOUT_RESPONSE) -> MagicMock:
    client = MagicMock()
    client.call.return_value = LLMResponse(
        content=content,
        model="claude-sonnet-4-5-20250929",
        input_tokens=800,
        output_tokens=300,
        latency_ms=1500,
        estimated_cost_usd=0.007,
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


async def _create_activity(
    session: object, user_id: int, sport: str = "gym", title: str = "Upper Body"
) -> int:
    activity = Activity(
        user_id=user_id,
        sport=sport,
        title=title,
        start_time=datetime(2024, 6, 10, 9, 0),
        duration_minutes=60,
        avg_hr=130,
        max_hr=165,
        data_source="hevy",
    )
    session.add(activity)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]

    if sport == "gym":
        session.add(  # type: ignore[union-attr]
            GymWorkoutDetail(
                activity_id=activity.id,
                exercise_title="Bench Press",
                set_index=1,
                set_type="normal",
                weight_kg=82.5,
                reps=8,
                rpe=8.0,
            )
        )

    await session.commit()  # type: ignore[union-attr]
    return activity.id


class TestGeneratePostWorkoutAnalysis:
    async def test_success(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_post_workout_analysis(session, user_id, activity_id)

            assert insight.insight_type == "post_workout"
            assert insight.activity_id == activity_id
            assert insight.user_id == user_id
            assert "performance_summary" in insight.content
            mock_llm.call.assert_called_once()

    async def test_logs_prompt(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_post_workout_analysis(session, user_id, activity_id)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.prompt_type == "post_workout"
            assert log.success is True
            assert log.input_tokens == 800

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_post_workout_analysis(session, user_id, activity_id)

            with pytest.raises(ValueError, match="already exists"):
                await engine.generate_post_workout_analysis(session, user_id, activity_id)

    async def test_activity_not_found_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            with pytest.raises(ValueError, match="not found"):
                await engine.generate_post_workout_analysis(session, user_id, 999)

    async def test_llm_failure_raises_and_logs(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_post_workout_analysis(session, user_id, activity_id)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.success is False

    async def test_links_to_planned_session(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)

            # Create activity on Wednesday June 12 (week starting Monday June 10)
            activity = Activity(
                user_id=user_id,
                sport="gym",
                title="Upper Body",
                start_time=datetime(2024, 6, 12, 9, 0),
                duration_minutes=60,
                data_source="hevy",
            )
            session.add(activity)
            await session.flush()

            # Create a plan for that week
            plan = WeeklyPlan(
                user_id=user_id,
                week_start=date(2024, 6, 10),
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

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_post_workout_analysis(session, user_id, activity.id)

            # Verify planned session is now linked
            await session.refresh(planned)
            assert planned.completed is True
            assert planned.activity_id == activity.id

    async def test_swimming_activity(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(
                session, user_id, sport="swimming", title="Pool Swim"
            )

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_post_workout_analysis(session, user_id, activity_id)

            assert insight.insight_type == "post_workout"
            assert insight.activity_id == activity_id
