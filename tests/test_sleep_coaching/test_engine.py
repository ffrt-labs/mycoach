"""Tests for sleep coaching engine method."""

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

VALID_SLEEP_RESPONSE = """{
  "sleep_quality_summary": "Good overall sleep quality.",
  "consistency_analysis": "Consistent bedtime around 22:30.",
  "sleep_architecture": "Healthy deep/REM ratio.",
  "performance_correlation": "Better sleep correlates with better training readiness.",
  "recommended_bedtime": "22:30",
  "recommended_wake_time": "06:00",
  "sleep_debt_assessment": "No significant sleep debt.",
  "hygiene_tips": ["Avoid caffeine after 2PM", "Dim lights 1h before bed"],
  "key_concern": "None"
}"""


def _mock_llm_client(content: str = VALID_SLEEP_RESPONSE) -> MagicMock:
    client = MagicMock()
    client.call.return_value = LLMResponse(
        content=content,
        model="claude-sonnet-4-5-20250929",
        input_tokens=600,
        output_tokens=250,
        latency_ms=1100,
        estimated_cost_usd=0.005,
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


class TestGenerateSleepCoaching:
    async def test_success(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 14)

            # Add some sleep data
            for offset in range(3):
                session.add(
                    DailyHealthSnapshot(
                        user_id=user_id,
                        snapshot_date=date(2024, 6, 14 - offset),
                        sleep_score=80 + offset,
                        sleep_duration_minutes=420 + offset * 15,
                    )
                )
            await session.commit()

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_sleep_coaching(session, user_id, today)

            assert insight.insight_type == "sleep"
            assert insight.insight_date == today
            assert insight.user_id == user_id
            assert "22:30" in insight.content
            mock_llm.call.assert_called_once()

    async def test_logs_prompt(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 14)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_sleep_coaching(session, user_id, today)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.prompt_type == "sleep"
            assert log.model == "claude-sonnet-4-5-20250929"
            assert log.success is True

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 14)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_sleep_coaching(session, user_id, today)

            with pytest.raises(ValueError, match="already exists"):
                await engine.generate_sleep_coaching(session, user_id, today)

    async def test_llm_failure_raises_and_logs(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 14)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_sleep_coaching(session, user_id, today)

            result = await session.execute(select(PromptLog))
            log = result.scalar_one()
            assert log.success is False
            assert "API timeout" in (log.error or "")

    async def test_works_without_sleep_data(self) -> None:
        """Engine should still work even with no sleep data (empty context)."""
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 14)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_sleep_coaching(session, user_id, today)

            assert insight.insight_type == "sleep"
            mock_llm.call.assert_called_once()
