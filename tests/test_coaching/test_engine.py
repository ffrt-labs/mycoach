"""Tests for coaching engine with mocked LLM client."""

from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.prompt_log import PromptLog
from mycoach.models.user import User
from tests.conftest import test_session

VALID_LLM_RESPONSE = """{
  "sleep_assessment": "Good sleep, 7.5h duration with adequate deep sleep.",
  "recovery_status": "Well recovered. Body Battery at 80, HRV above baseline.",
  "readiness_verdict": "go_hard",
  "readiness_explanation": "HRV 45ms above 7-day avg, Body Battery 80, training readiness 75.",
  "workout_adjustments": "No adjustments needed.",
  "sleep_recommendation": "Target 10:30 PM bedtime for 7.5h sleep.",
  "key_metrics": {
    "body_battery": 80,
    "hrv_status": 45.0,
    "sleep_score": 82,
    "training_readiness": 75,
    "resting_hr": 55
  }
}"""


def _mock_llm_client(content: str = VALID_LLM_RESPONSE) -> MagicMock:
    client = MagicMock()
    client.call.return_value = LLMResponse(
        content=content,
        model="claude-sonnet-4-5-20250929",
        input_tokens=500,
        output_tokens=200,
        latency_ms=1200,
        estimated_cost_usd=0.0045,
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


class TestGenerateDailyBriefing:
    async def test_success(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)

            # Add health data
            session.add(
                DailyHealthSnapshot(
                    user_id=user_id,
                    snapshot_date=today,
                    resting_hr=55,
                    sleep_score=82,
                    body_battery_high=80,
                )
            )
            await session.commit()

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_daily_briefing(session, user_id, today)

            assert insight.insight_type == "daily_briefing"
            assert insight.insight_date == today
            assert insight.user_id == user_id
            assert "go_hard" in insight.content
            mock_llm.call.assert_called_once()

    async def test_logs_prompt(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_daily_briefing(session, user_id, today)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.prompt_type == "daily_briefing"
            assert log.model == "claude-sonnet-4-5-20250929"
            assert log.input_tokens == 500
            assert log.output_tokens == 200
            assert log.success is True

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_daily_briefing(session, user_id, today)

            with pytest.raises(ValueError, match="already exists"):
                await engine.generate_daily_briefing(session, user_id, today)

    async def test_llm_failure_raises_and_logs(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_daily_briefing(session, user_id, today)

            # Should still log the failure
            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.success is False
            assert "API timeout" in (log.error or "")

    async def test_invalid_llm_json_retries(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            # First call returns bad JSON, second call returns valid
            mock_llm.call.side_effect = [
                LLMResponse(
                    content="not valid json",
                    model="claude-sonnet-4-5-20250929",
                    input_tokens=500,
                    output_tokens=50,
                    latency_ms=800,
                    estimated_cost_usd=0.002,
                ),
                LLMResponse(
                    content=VALID_LLM_RESPONSE,
                    model="claude-sonnet-4-5-20250929",
                    input_tokens=550,
                    output_tokens=200,
                    latency_ms=1200,
                    estimated_cost_usd=0.0045,
                ),
            ]
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_daily_briefing(session, user_id, today)

            assert "go_hard" in insight.content
            assert mock_llm.call.call_count == 2
