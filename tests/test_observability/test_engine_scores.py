"""Code-check Langfuse scores emitted by coaching runs, captured with a fake sink.

Trace IDs are independent fixtures (``printf '<seed>' | sha256sum | cut -c1-32``),
the same ones ``test_engine_tracing.py`` uses.
"""

import logging
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import LLMQuotaExceeded
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.activity import Activity
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from mycoach.observability.scores import NoopScoreSink, Score, ScoreSink
from tests.conftest import test_session as new_session
from tests.test_coaching.test_engine import _add_recovery_snapshot
from tests.test_coaching.test_engine import _create_user as _create_briefing_user
from tests.test_coaching.test_engine import _mock_llm_client as _briefing_llm
from tests.test_observability.conftest import Captured, RecordingScoreSink, trace_hex
from tests.test_observability.test_engine_tracing import (
    _gym_response,
    _setup_two_gym_one_cardio,
)
from tests.test_plans.test_engine import VALID_CARDIO_RESPONSE, VALID_GYM_ADJUSTMENT
from tests.test_post_workout.test_engine import _mock_llm_client as _post_workout_llm
from tests.test_weekly_recap.test_engine import _mock_llm_client as _recap_llm

MONDAY = date(2024, 6, 10)
BRIEFING = "ef2156ef5a01aa2158bfb9e1b4a8fb29"
PLAN = "136588f0a3f98a22572454bc98de0296"
POST_WORKOUT = "cc0219ed55252b07dbd1fe039bc88333"
RECAP = "0770cfbac563ba742e33b5e00d781155"


def _at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _truncated() -> LLMResponse:
    return LLMResponse(
        content='{"sleep_assessment": "Good',
        model="sonnet",
        input_tokens=500,
        output_tokens=4096,
        latency_ms=900,
        estimated_cost_usd=0.0,
        stop_reason="max_tokens",
    )


async def _briefing(llm: MagicMock, sink: ScoreSink, clock: datetime | None = None) -> None:
    async with new_session() as session:
        user_id = await _create_briefing_user(session)
        await _add_recovery_snapshot(session, user_id, MONDAY)
        engine = CoachingEngine(
            llm_client=llm,
            scores=sink,
            clock=lambda: clock or _at(2024, 6, 10, 7, 15),
        )
        await engine.generate_daily_briefing(session, user_id, MONDAY)


class TestDailyBriefingCodeChecks:
    async def test_clean_run_scores_parse_ok_without_retry_or_truncation(self) -> None:
        sink = RecordingScoreSink()
        await _briefing(_briefing_llm(), sink)

        assert sink.values() == {
            f"{BRIEFING}:parse_ok": 1.0,
            f"{BRIEFING}:needed_retry": 0.0,
            f"{BRIEFING}:truncated": 0.0,
        }
        for score in sink.scores:
            assert score.trace_id == BRIEFING
            assert score.data_type == "BOOLEAN"
            assert score.metadata == {"operation": "daily_briefing"}
            # Stable: the day the run is about, not the time it was sent.
            assert score.timestamp == _at(2024, 6, 10)

    async def test_truncated_first_answer_rescued_by_the_retry(self) -> None:
        llm = _briefing_llm()
        llm.call.side_effect = [_truncated(), llm.call.return_value]
        sink = RecordingScoreSink()
        await _briefing(llm, sink)

        assert sink.values() == {
            f"{BRIEFING}:parse_ok": 1.0,
            f"{BRIEFING}:needed_retry": 1.0,
            f"{BRIEFING}:truncated": 1.0,
        }

    async def test_failed_retry_scores_parse_ok_false(self) -> None:
        sink = RecordingScoreSink()
        with pytest.raises(RuntimeError, match="Failed to generate daily briefing"):
            await _briefing(_briefing_llm("not json at all"), sink)

        assert sink.values() == {
            f"{BRIEFING}:parse_ok": 0.0,
            f"{BRIEFING}:needed_retry": 1.0,
            f"{BRIEFING}:truncated": 0.0,
        }

    async def test_provider_error_has_no_answer_to_check(self) -> None:
        llm = _briefing_llm()
        llm.call.side_effect = LLMQuotaExceeded("Claude Code: usage limit reached")
        sink = RecordingScoreSink()
        with pytest.raises(RuntimeError):
            await _briefing(llm, sink)

        assert sink.scores == []

    async def test_a_retried_run_on_a_later_day_overwrites_its_scores(self) -> None:
        """Same trace, same IDs and the same timestamp day, so Langfuse overwrites."""
        failed, retried = RecordingScoreSink(), RecordingScoreSink()
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            with pytest.raises(RuntimeError):
                await CoachingEngine(
                    llm_client=_briefing_llm("not json at all"),
                    scores=failed,
                    clock=lambda: _at(2024, 6, 10, 7, 15),
                ).generate_daily_briefing(session, user_id, MONDAY)
            await CoachingEngine(
                llm_client=_briefing_llm(),
                scores=retried,
                clock=lambda: _at(2024, 6, 12, 9, 0),
            ).generate_daily_briefing(session, user_id, MONDAY)

        assert list(failed.by_id()) == list(retried.by_id())
        assert {s.timestamp for s in failed.scores + retried.scores} == {_at(2024, 6, 10)}
        assert retried.values()[f"{BRIEFING}:parse_ok"] == 1.0


class TestWeeklyPlanCodeChecks:
    async def test_each_generation_is_scored_and_a_gym_fallback_is_flagged(self) -> None:
        llm = MagicMock()
        llm.daily_model = "claude-sonnet-4-5-20250929"
        llm.weekly_model = "claude-opus-4-6"
        llm.call.side_effect = [
            _gym_response(VALID_GYM_ADJUSTMENT),
            # The second gym slot never parses, so it falls back to the routine.
            _gym_response("not json at all"),
            _gym_response("still not json"),
            _gym_response(VALID_CARDIO_RESPONSE),
        ]
        sink = RecordingScoreSink()
        async with new_session() as session:
            user_id = await _setup_two_gym_one_cardio(session)
            engine = CoachingEngine(
                llm_client=llm, scores=sink, clock=lambda: _at(2024, 6, 9, 18, 0)
            )
            plan = await engine.generate_weekly_plan(session, user_id, MONDAY)
            assert plan.id == 1

        assert sink.values() == {
            f"{PLAN}:parse_ok:gym_adjustment:0": 1.0,
            f"{PLAN}:needed_retry:gym_adjustment:0": 0.0,
            f"{PLAN}:truncated:gym_adjustment:0": 0.0,
            f"{PLAN}:fallback_used:gym_adjustment:0": 0.0,
            f"{PLAN}:parse_ok:gym_adjustment:1": 0.0,
            f"{PLAN}:needed_retry:gym_adjustment:1": 1.0,
            f"{PLAN}:truncated:gym_adjustment:1": 0.0,
            f"{PLAN}:fallback_used:gym_adjustment:1": 1.0,
            f"{PLAN}:parse_ok:cardio_plan": 1.0,
            f"{PLAN}:needed_retry:cardio_plan": 0.0,
            f"{PLAN}:truncated:cardio_plan": 0.0,
        }
        fallback = sink.by_id()[f"{PLAN}:fallback_used:gym_adjustment:1"]
        assert fallback.metadata == {"operation": "gym_adjustment", "day_of_week": 2}
        # The plan's week is still ahead, so the run's start is the stable time.
        assert {s.timestamp for s in sink.scores} == {_at(2024, 6, 9, 18, 0)}

    async def test_provider_error_still_flags_the_fallback(self) -> None:
        llm = MagicMock()
        llm.daily_model = "claude-sonnet-4-5-20250929"
        llm.weekly_model = "claude-opus-4-6"
        llm.call.side_effect = LLMQuotaExceeded("usage limit reached")
        sink = RecordingScoreSink()
        async with new_session() as session:
            user_id = await _setup_two_gym_one_cardio(session)
            await CoachingEngine(llm_client=llm, scores=sink).generate_weekly_plan(
                session, user_id, MONDAY
            )

        assert sink.values() == {
            f"{PLAN}:fallback_used:gym_adjustment:0": 1.0,
            f"{PLAN}:fallback_used:gym_adjustment:1": 1.0,
        }


class TestOtherRuns:
    async def test_post_workout_run_is_scored_on_its_trace(self) -> None:
        sink = RecordingScoreSink()
        async with new_session() as session:
            user = User(email="t@example.com", name="T", fitness_level="intermediate")
            session.add(user)
            await session.flush()
            activity = Activity(
                user_id=user.id,
                sport="running",
                title="Easy run",
                start_time=datetime(2024, 6, 6, 9, 0),
                duration_minutes=40,
                data_source="garmin",
            )
            session.add(activity)
            await session.commit()
            engine = CoachingEngine(llm_client=_post_workout_llm(), scores=sink)
            await engine.generate_post_workout_analysis(session, user.id, activity.id)

        assert sink.values() == {
            f"{POST_WORKOUT}:parse_ok": 1.0,
            f"{POST_WORKOUT}:needed_retry": 0.0,
            f"{POST_WORKOUT}:truncated": 0.0,
        }
        assert {s.timestamp for s in sink.scores} == {_at(2024, 6, 6)}

    async def test_weekly_recap_run_is_scored_on_its_trace(self) -> None:
        sink = RecordingScoreSink()
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            engine = CoachingEngine(llm_client=_recap_llm(), scores=sink)
            await engine.generate_weekly_recap(session, user_id, MONDAY)

        assert set(sink.values()) == {
            f"{RECAP}:parse_ok",
            f"{RECAP}:needed_retry",
            f"{RECAP}:truncated",
        }


RATIO = f"{PLAN}:plan_sessions_completed_ratio"


async def _plan_with_sessions(session: object, user_id: int, week_start: date, done: int) -> None:
    """A plan of three sessions, the first ``done`` of them marked done by hand."""
    plan = WeeklyPlan(user_id=user_id, week_start=week_start, status="active", summary="Plan")
    session.add(plan)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]
    for day in range(3):
        session.add(  # type: ignore[attr-defined]
            PlannedSession(
                plan_id=plan.id,
                day_of_week=day,
                sport="padel",
                title=f"Session {day}",
                completed=day < done,
            )
        )
    await session.commit()  # type: ignore[attr-defined]


class TestPlanCompletionScore:
    async def test_recap_scores_the_ratio_on_the_recapped_weeks_plan(self) -> None:
        sink = RecordingScoreSink()
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _plan_with_sessions(session, user_id, MONDAY, done=1)  # plan 1: week W
            await _plan_with_sessions(session, user_id, date(2024, 6, 17), done=3)  # plan 2
            engine = CoachingEngine(
                llm_client=_recap_llm(), scores=sink, clock=lambda: _at(2024, 6, 17, 6, 0)
            )
            await engine.generate_weekly_recap(session, user_id, MONDAY)

        ratio = sink.by_id()[RATIO]
        assert ratio.trace_id == PLAN
        assert ratio.data_type == "NUMERIC"
        assert ratio.value == pytest.approx(1 / 3)
        assert ratio.timestamp == _at(2024, 6, 10)
        assert ratio.metadata == {
            "operation": "weekly_recap",
            "completed_sessions": 1,
            "planned_sessions": 3,
        }
        assert [s.id for s in sink.scores if s.trace_id != RECAP] == [RATIO]

    async def test_a_week_without_a_plan_scores_no_ratio(self) -> None:
        sink = RecordingScoreSink()
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            engine = CoachingEngine(llm_client=_recap_llm(), scores=sink)
            await engine.generate_weekly_recap(session, user_id, MONDAY)

        assert all(s.name != "plan_sessions_completed_ratio" for s in sink.scores)

    async def test_a_regenerated_recap_overwrites_the_ratio(self) -> None:
        first, again = RecordingScoreSink(), RecordingScoreSink()
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _plan_with_sessions(session, user_id, MONDAY, done=2)
            await CoachingEngine(
                llm_client=_recap_llm(), scores=first, clock=lambda: _at(2024, 6, 17, 6, 0)
            ).generate_weekly_recap(session, user_id, MONDAY)
            await CoachingEngine(
                llm_client=_recap_llm(), scores=again, clock=lambda: _at(2024, 6, 19, 20, 0)
            ).generate_weekly_recap(session, user_id, MONDAY, force=True)

        assert first.by_id()[RATIO].timestamp == again.by_id()[RATIO].timestamp
        assert again.values()[RATIO] == pytest.approx(2 / 3)

    async def test_the_ratio_is_scored_even_when_the_recap_fails(self) -> None:
        sink = RecordingScoreSink()
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _plan_with_sessions(session, user_id, MONDAY, done=0)
            with pytest.raises(RuntimeError):
                await CoachingEngine(
                    llm_client=_recap_llm("not json at all"), scores=sink
                ).generate_weekly_recap(session, user_id, MONDAY)

        assert sink.values()[RATIO] == 0.0


class _FailingSink:
    def __init__(self) -> None:
        self.calls = 0

    def emit(self, score: Score) -> None:
        self.calls += 1
        raise ConnectionError("langfuse-web:3000 unreachable")


class TestSinkFailure:
    async def test_a_failing_sink_never_affects_the_run(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        failing = _FailingSink()
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(llm_client=_briefing_llm(), scores=failing)
            with caplog.at_level(logging.WARNING, logger="mycoach.coaching.engine"):
                insight = await engine.generate_daily_briefing(session, user_id, MONDAY)

        # The sink was really called (so the run did try to score) ...
        assert failing.calls == 3
        # ... and the run produced its insight regardless.
        assert insight.id is not None
        assert "go_hard" in insight.content
        assert any("score" in r.getMessage() for r in caplog.records)


class TestLangfuseOn:
    async def test_scores_land_on_the_runs_own_trace(self, captured: Captured) -> None:
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(llm_client=_briefing_llm(), tracer=captured.tracer)
            await engine.generate_daily_briefing(session, user_id, MONDAY)

        [root] = captured.roots()
        calls = captured.tracer.client.score_calls  # type: ignore[attr-defined]
        assert {c["trace_id"] for c in calls} == {trace_hex(root)}
        assert sorted(c["score_id"] for c in calls) == [
            f"{BRIEFING}:needed_retry",
            f"{BRIEFING}:parse_ok",
            f"{BRIEFING}:truncated",
        ]


class TestDisabled:
    def test_engine_defaults_to_a_noop_sink_when_langfuse_is_off(self) -> None:
        # The root conftest forces the no-op tracer.
        engine = CoachingEngine(llm_client=_briefing_llm())
        assert isinstance(engine._scores, NoopScoreSink)
