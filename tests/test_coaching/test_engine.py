"""Tests for coaching engine with mocked LLM client."""

import json
import threading
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import InsufficientHealthData, PipelineSkip
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session

VALID_LLM_RESPONSE = """{
  "readiness": "go_hard",
  "headline": {
    "tone": "good",
    "summary": "Nothing planned for today and you're fresh: a good day for Squat Day."
  },
  "options": [
    {
      "open_session_id": 1,
      "title": null,
      "steps": ["Last working weights, RPE 7-8", "Log every load"],
      "why": "Fresh legs today, and Thursday keeps its swim.",
      "recommended": true
    }
  ],
  "guardrail": {"when": "HRV 7-day avg below range", "then": "Easy swim instead"},
  "sleep_note": "Best REM in 3 days.",
  "tonight": [{"tone": "neutral", "text": "Bed 22:00-22:30"}],
  "key_metrics": {
    "body_battery": 80,
    "hrv_status": 45.0,
    "sleep_score": 82,
    "training_readiness": 75,
    "resting_hr": 55
  },
  "analysis_md": "## Today. Well recovered."
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
    client.provider_name = "openrouter"
    client.daily_model = "claude-sonnet-4-5-20250929"
    client.weekly_model = "claude-opus-4-6"
    return client


async def _add_recovery_snapshot(session: object, user_id: int, day: date) -> None:
    """Give the day enough recovery data to clear the engine's empty-data guard.

    Every daily-briefing path now refuses a day with no sleep, HRV, or Body
    Battery, so a test about anything *else* still has to supply one.
    """
    session.add(  # type: ignore[union-attr]
        DailyHealthSnapshot(
            user_id=user_id,
            snapshot_date=day,
            resting_hr=55,
            sleep_score=82,
            sleep_duration_minutes=450,
        )
    )
    await session.commit()  # type: ignore[union-attr]


async def _create_user(session: object) -> int:
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]
    return user.id


class TestGenerateDailyBriefing:
    async def test_the_body_summary_reaches_the_prompt(self) -> None:
        from datetime import UTC, datetime

        from mycoach import health

        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)
        await health.record_body_measurement(
            user_id, measured_at=datetime(2024, 6, 8, 6, 30), weight_kg=82.4
        )
        mock_llm = _mock_llm_client()
        engine = CoachingEngine(
            llm_client=mock_llm, clock=lambda: datetime(2024, 6, 10, 9, 30, tzinfo=UTC)
        )

        async with test_session() as session:
            await engine.generate_daily_briefing(session, user_id, today)

        sent = " ".join(str(a) for a in [*mock_llm.call.call_args.args,
                                          *mock_llm.call.call_args.kwargs.values()])  # fmt: skip
        assert "Latest weight: 82.4 kg, measured Sat (2 days ago)." in sent

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

    async def test_prompt_lists_the_sessions_still_open_this_week(self) -> None:
        """#218: on Wednesday the coach knows Monday's squat is still open."""
        async with test_session() as session:
            user_id = await _create_user(session)
            wednesday = date(2024, 6, 12)
            await _add_recovery_snapshot(session, user_id, wednesday)
            plan = WeeklyPlan(user_id=user_id, week_start=date(2024, 6, 10), status="active")
            session.add(plan)
            await session.flush()
            squat = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Squat Day", track="gym"
            )
            session.add(squat)
            await session.commit()

            mock_llm = _mock_llm_client()
            await CoachingEngine(llm_client=mock_llm).generate_daily_briefing(
                session, user_id, wednesday
            )

            user_message = mock_llm.call.call_args.kwargs["user_message"]
            assert (
                "## Open This Week\n1 session open, 5 days left including today:\n"
                f"- id {squat.id} · Monday: Squat Day [gym], planned day passed\n"
            ) in user_message

    async def test_prompt_carries_the_steps_up_to_yesterday(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)
            for day, steps in [(date(2024, 6, 9), 12431), (date(2024, 6, 3), 6000)]:
                session.add(DailyHealthSnapshot(user_id=user_id, snapshot_date=day, steps=steps))
            await session.commit()

            mock_llm = _mock_llm_client()
            await CoachingEngine(llm_client=mock_llm).generate_daily_briefing(
                session, user_id, today
            )

            user_message = mock_llm.call.call_args.kwargs["user_message"]
            assert "Daily target: 10,000 steps\nYesterday: 12,431 steps\n" in user_message
            assert "7-day avg: 9,216/day (1/7 days at or above target)" in user_message

    async def test_without_a_date_today_is_the_local_day_in_the_settings_timezone(
        self,
    ) -> None:
        # 23:30 UTC on 1 July is 00:30 on 2 July in London (the default timezone).
        clock = lambda: datetime(2026, 7, 1, 23, 30, tzinfo=UTC)  # noqa: E731
        async with test_session() as session:
            user_id = await _create_user(session)
            await _add_recovery_snapshot(session, user_id, date(2026, 7, 2))
            for day, steps in [(date(2026, 7, 1), 7777), (date(2026, 6, 30), 5555)]:
                session.add(DailyHealthSnapshot(user_id=user_id, snapshot_date=day, steps=steps))
            await session.commit()

            mock_llm = _mock_llm_client()
            insight = await CoachingEngine(
                llm_client=mock_llm, clock=clock
            ).generate_daily_briefing(session, user_id)

            assert insight.insight_date == date(2026, 7, 2)
            assert "Yesterday: 7,777 steps" in mock_llm.call.call_args.kwargs["user_message"]

    async def test_llm_call_runs_off_the_event_loop_thread(self) -> None:
        """A slow provider (the Claude Code CLI takes 30-90 s) must not freeze the
        web server, so the engine hands the synchronous call to a worker thread."""
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)

            loop_thread = threading.get_ident()
            call_threads: list[int] = []
            mock_llm = _mock_llm_client()
            response = mock_llm.call.return_value

            def record_thread(**_kwargs: object) -> LLMResponse:
                call_threads.append(threading.get_ident())
                return response

            mock_llm.call.side_effect = record_thread
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_daily_briefing(session, user_id, today)

            assert call_threads
            assert loop_thread not in call_threads

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_daily_briefing(session, user_id, today)

            with pytest.raises(PipelineSkip, match="already exists"):
                await engine.generate_daily_briefing(session, user_id, today)

    async def test_refuses_a_day_with_no_health_snapshot_at_all(self) -> None:
        """The guard lives here so cron, retry loop and button cannot drift.

        For six days the dashboard invited the user to press a button that
        bypassed this rule entirely and would have briefed on nothing.
        """
        async with test_session() as session:
            user_id = await _create_user(session)
            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(InsufficientHealthData, match="no Garmin health snapshot"):
                await engine.generate_daily_briefing(session, user_id, date(2024, 6, 10))

            mock_llm.call.assert_not_called()

    async def test_refuses_a_day_carrying_no_recovery_data(self) -> None:
        """Resting HR and steps are content, but they are not recovery.

        Garmin answers a day it has nothing for with a well-formed document of
        nulls; a snapshot can survive that and still hold nothing worth
        coaching from.
        """
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            session.add(
                DailyHealthSnapshot(
                    user_id=user_id, snapshot_date=today, resting_hr=58, steps=10234
                )
            )
            await session.commit()

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(InsufficientHealthData, match="no sleep, HRV, or Body Battery"):
                await engine.generate_daily_briefing(session, user_id, today)

            mock_llm.call.assert_not_called()

    async def test_the_guard_is_a_skip_not_a_failure(self) -> None:
        """Withholding a briefing from an empty day is correct, not a fault.

        The retry loop reads this straight off ``job_runs``: a skip retries
        freely, a failure burns the budget.
        """
        assert issubclass(InsufficientHealthData, PipelineSkip)

    async def test_force_overrides_the_guard(self) -> None:
        """The one documented escape hatch, unchanged."""
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            session.add(DailyHealthSnapshot(user_id=user_id, snapshot_date=today, resting_hr=58))
            await session.commit()

            engine = CoachingEngine(llm_client=_mock_llm_client())
            insight = await engine.generate_daily_briefing(session, user_id, today, force=True)

        assert insight.insight_type == "daily_briefing"

    async def test_any_one_recovery_field_is_enough(self) -> None:
        """Body Battery alone is a thin day, but it is a day worth speaking to."""
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            session.add(
                DailyHealthSnapshot(user_id=user_id, snapshot_date=today, body_battery_morning=64)
            )
            await session.commit()

            engine = CoachingEngine(llm_client=_mock_llm_client())
            insight = await engine.generate_daily_briefing(session, user_id, today)

        assert insight.insight_type == "daily_briefing"

    async def test_llm_failure_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_daily_briefing(session, user_id, today)

    async def test_invalid_llm_json_retries(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)

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

    async def test_truncated_response_retries_with_doubled_budget_and_the_parse_error(
        self,
    ) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = [
                LLMResponse(
                    content='{"readiness": "go_h',
                    model="claude-sonnet-4-5-20250929",
                    input_tokens=500,
                    output_tokens=4096,
                    latency_ms=800,
                    estimated_cost_usd=0.002,
                    stop_reason="max_tokens",
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
            await engine.generate_daily_briefing(session, user_id, today)

            first, retry = mock_llm.call.call_args_list
            assert first.kwargs["max_tokens"] == 4096
            assert retry.kwargs["max_tokens"] == 8192
            assert "Your previous response had this error:" in retry.kwargs["user_message"]

    async def test_openrouter_truncation_retries_with_doubled_answer_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``finish_reason: length`` from OpenRouter triggers the same retry, and
        each attempt keeps its reasoning headroom on top of the answer budget."""
        from tests.test_coaching.test_openrouter_provider import (
            FakeOpenRouter,
            client_for,
            models,
            ok,
        )

        monkeypatch.setenv("MYCOACH_OPENROUTER_API_KEY", "sk-or-test")
        fake = FakeOpenRouter(
            models(),  # the briefing asks once whether the model takes a strict schema
            ok(content='{"readiness": "go_h', finish_reason="length"),
            ok(content=VALID_LLM_RESPONSE),
        )
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)

            engine = CoachingEngine(llm_client=client_for(fake))
            insight = await engine.generate_daily_briefing(session, user_id, today)

            assert "go_hard" in insight.content
            # 4096 of answer + 8192 to think, then 8192 of answer + 8192 to think:
            # the retry's answer budget doubles, its headroom never shrinks.
            bodies = [json.loads(r.content) for r in fake.requests[1:]]
            assert [body["max_tokens"] for body in bodies] == [12288, 16384]
            retry_message = bodies[1]["messages"][1]["content"]
            assert "Your previous response had this error:" in retry_message

    async def test_retry_that_also_fails_stores_the_briefing_without_coach_notes(self) -> None:
        """Prompt v5 (#241): the email still goes out, with its code blocks only."""
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)

            mock_llm = _mock_llm_client(content="not valid json")
            engine = CoachingEngine(llm_client=mock_llm)

            insight = await engine.generate_daily_briefing(session, user_id, today)

            assert insight.insight_type == "daily_briefing"
            assert json.loads(insight.content) is None
            assert mock_llm.call.call_count == 2
            retry = mock_llm.call.call_args_list[1]
            assert retry.kwargs["max_tokens"] == 4096

    async def test_the_call_carries_the_strict_schema_of_the_briefing_notes(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            await _add_recovery_snapshot(session, user_id, today)
            mock_llm = _mock_llm_client()

            await CoachingEngine(llm_client=mock_llm).generate_daily_briefing(
                session, user_id, today
            )

        schema = mock_llm.call.call_args.kwargs["response_schema"]
        assert schema.name == "daily_briefing"
        assert schema.schema["additionalProperties"] is False
        assert "options" in schema.schema["required"]
