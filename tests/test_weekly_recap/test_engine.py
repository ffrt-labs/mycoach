"""Tests for weekly recap engine method."""

import json
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import PipelineSkip
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.activity import Activity
from mycoach.models.coaching import CoachingInsight
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session

VALID_RECAP_RESPONSE = """{
  "headline": {
    "tone": "good",
    "summary": "Both lifts and the swim are in; the bench press reached 85kg."
  },
  "actions": [
    {"do": "Keep one intervals session in the week.", "why": "The only high-intensity work."},
    {"do": "Sleep earlier before the hard days.", "why": null}
  ],
  "guardrail": {"when": "Resting HR up 5 bpm", "then": "Swap intervals for easy Z2"},
  "session_notes": [{"session_id": 1, "text": "Solid squat day."}],
  "recovery_note": "Sleep dipped mid-week and recovered by Sunday.",
  "lift_notes": [{"exercise_id": "D04AC939", "tone": "good", "trend": "up 2.5 kg"}],
  "load_note": "Mostly low aerobic work.",
  "block_suggestion": null,
  "analysis_md": "## Week\\nStrong week."
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
            assert "85kg" in json.loads(insight.content)["headline"]["summary"]
            mock_llm.call.assert_called_once()

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_weekly_recap(session, user_id, week_start)

            with pytest.raises(PipelineSkip, match="already exists"):
                await engine.generate_weekly_recap(session, user_id, week_start)

    async def test_non_monday_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(ValueError, match="must be a Monday"):
                await engine.generate_weekly_recap(session, user_id, date(2024, 6, 12))

    async def test_llm_failure_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            week_start = date(2024, 6, 10)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_weekly_recap(session, user_id, week_start)

    async def test_a_forced_regenerate_that_fails_keeps_the_previous_recap(self) -> None:
        """#180: the failed run no longer commits, so the forced delete rolls back."""
        week_start = date(2024, 6, 10)
        mock_llm = _mock_llm_client()
        engine = CoachingEngine(llm_client=mock_llm)
        async with test_session() as session:
            user_id = await _create_user(session)
            previous = await engine.generate_weekly_recap(session, user_id, week_start)

            mock_llm.call.side_effect = Exception("API timeout")
            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_weekly_recap(session, user_id, week_start, force=True)

        async with test_session() as session:
            kept = (await session.execute(select(CoachingInsight))).scalars().all()
        assert [(i.id, i.content) for i in kept] == [(previous.id, previous.content)]

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


def _response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="claude-sonnet-4-5-20250929",
        input_tokens=800,
        output_tokens=300,
        latency_ms=1200,
        estimated_cost_usd=0.006,
    )


def _four_actions() -> str:
    """A v5 recap answer with one action more than the slot allows."""
    data = json.loads(VALID_RECAP_RESPONSE)
    data["actions"] = data["actions"] * 2
    return json.dumps(data)


class TestRecapCoachNotes:
    """Prompt v5 (#242): the recap's Coach notes in typed slots, one retry, then none."""

    async def test_the_call_carries_the_strict_schema_of_the_recap_notes(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            mock_llm = _mock_llm_client()
            await CoachingEngine(llm_client=mock_llm).generate_weekly_recap(
                session, user_id, date(2024, 6, 10)
            )

        schema = mock_llm.call.call_args.kwargs["response_schema"]
        assert schema.name == "weekly_recap"
        assert "session_notes" in schema.schema["required"]

    async def test_a_second_invalid_answer_stores_the_recap_without_coach_notes(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = [_response(_four_actions()), _response(_four_actions())]
            insight = await CoachingEngine(llm_client=mock_llm).generate_weekly_recap(
                session, user_id, date(2024, 6, 10)
            )

            assert insight.insight_type == "weekly_recap"
            assert json.loads(insight.content) is None
            retry = mock_llm.call.call_args_list[1]
            assert "at most 3 items" in retry.kwargs["user_message"]
