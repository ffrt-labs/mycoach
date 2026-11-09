"""Coaching runs recorded as Langfuse traces, captured with an in-memory exporter.

Trace IDs are independent fixtures (``printf '<seed>' | sha256sum | cut -c1-32``).
"""

import logging
import threading
from collections.abc import Sequence
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from langfuse._client.resource_manager import LangfuseResourceManager
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import LLMAuthError, LLMQuotaExceeded
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.activity import Activity
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.routine import RoutineDay, RoutineExercise, WorkoutRoutine
from mycoach.models.user import User
from mycoach.observability.tracing import set_tracer
from tests.conftest import test_session as new_session
from tests.test_coaching.test_engine import _add_recovery_snapshot
from tests.test_coaching.test_engine import _create_user as _create_briefing_user
from tests.test_coaching.test_engine import _mock_llm_client as _briefing_llm
from tests.test_observability.conftest import (
    Captured,
    attr,
    json_attr,
    parent_hex,
    span_hex,
    trace_hex,
    tracer_with,
)
from tests.test_plans.test_engine import VALID_CARDIO_RESPONSE, VALID_GYM_ADJUSTMENT
from tests.test_post_workout.test_engine import _mock_llm_client as _post_workout_llm
from tests.test_weekly_recap.test_engine import _mock_llm_client as _recap_llm

MONDAY = date(2024, 6, 10)


class TestDailyBriefingTrace:
    async def test_one_trace_seeded_by_user_and_day(self, captured: Captured) -> None:
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            assert user_id == 1
            await _add_recovery_snapshot(session, user_id, MONDAY)

            engine = CoachingEngine(llm_client=_briefing_llm(), tracer=captured.tracer)
            await engine.generate_daily_briefing(session, user_id, MONDAY)

        [root] = captured.roots()
        assert trace_hex(root) == "ef2156ef5a01aa2158bfb9e1b4a8fb29"
        assert attr(root, "langfuse.trace.name") == "daily-briefing"
        assert attr(root, "session.id") == "mycoach:week:1:2024-06-10"
        assert attr(root, "user.id") == "1"

        [gen] = captured.generations()
        assert trace_hex(gen) == trace_hex(root)
        assert parent_hex(gen) == span_hex(root)
        assert gen.name == "daily_briefing"
        assert attr(gen, "langfuse.observation.model.name") == "claude-sonnet-4-5-20250929"
        assert json_attr(gen, "langfuse.observation.usage_details") == {
            "input": 500,
            "output": 200,
        }
        assert json_attr(gen, "langfuse.observation.metadata.attempt") == 1
        assert json_attr(gen, "langfuse.observation.metadata.latency_ms") == 1200
        assert "langfuse.observation.level" not in (gen.attributes or {})
        # Input is the prompt's variable map, not the compiled prompt.
        variables = json_attr(gen, "langfuse.observation.input")
        assert set(variables) == {
            "health_data",
            "health_trends",
            "recent_activities",
            "planned_workout",
        }

    async def test_forced_regeneration_gets_a_regen_trace_that_supersedes(
        self, captured: Captured
    ) -> None:
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(
                llm_client=_briefing_llm(),
                tracer=captured.tracer,
                clock=lambda: datetime(2024, 6, 10, 7, 15, tzinfo=UTC),
            )
            await engine.generate_daily_briefing(session, user_id, MONDAY)
            await engine.generate_daily_briefing(session, user_id, MONDAY, force=True)

        first, regen = sorted(captured.roots(), key=lambda s: s.start_time or 0)
        assert trace_hex(first) == "ef2156ef5a01aa2158bfb9e1b4a8fb29"
        assert trace_hex(regen) == "d323de259f04b6ce4355d2c46d2e0aa3"
        assert (
            json_attr(regen, "langfuse.trace.metadata.supersedes")
            == "ef2156ef5a01aa2158bfb9e1b4a8fb29"
        )
        assert attr(first, "langfuse.trace.metadata.supersedes") is None
        assert attr(regen, "session.id") == "mycoach:week:1:2024-06-10"


def _gym_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="claude-sonnet-4-5-20250929",
        input_tokens=1500,
        output_tokens=400,
        latency_ms=2000,
        estimated_cost_usd=0.0,
    )


async def _setup_two_gym_one_cardio(session: AsyncSession) -> int:
    user = User(email="t@example.com", name="T", fitness_level="intermediate")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    for day, sport in [(0, "gym"), (2, "gym"), (5, "running")]:
        session.add(
            WeeklyAvailability(user_id=user.id, week_start=MONDAY, day_of_week=day, sport=sport)
        )
    routine = WorkoutRoutine(user_id=user.id, name="PPL Split")
    push = RoutineDay(name="Push Day", order_index=0)
    push.exercises.append(
        RoutineExercise(
            exercise_id="Barbell_Bench_Press_-_Medium_Grip",
            exercise_name="Bench Press",
            sets=4,
            rep_range="6-8",
            order_index=0,
        )
    )
    routine.days.append(push)
    session.add(routine)
    await session.commit()
    return user.id


class TestWeeklyPlanTrace:
    async def test_one_trace_with_a_generation_per_gym_slot_plus_cardio(
        self, captured: Captured
    ) -> None:
        llm = MagicMock()
        llm.daily_model = "claude-sonnet-4-5-20250929"
        llm.weekly_model = "claude-opus-4-6"
        llm.call.side_effect = [
            _gym_response(VALID_GYM_ADJUSTMENT),
            # The second slot's first answer does not parse, so it is retried.
            _gym_response("not json at all"),
            _gym_response(VALID_GYM_ADJUSTMENT),
            _gym_response(VALID_CARDIO_RESPONSE),
        ]
        async with new_session() as session:
            user_id = await _setup_two_gym_one_cardio(session)
            engine = CoachingEngine(llm_client=llm, tracer=captured.tracer)
            plan = await engine.generate_weekly_plan(session, user_id, MONDAY)
            assert plan.id == 1

        [root] = captured.roots()
        assert trace_hex(root) == "136588f0a3f98a22572454bc98de0296"
        assert attr(root, "langfuse.trace.name") == "weekly-plan"
        assert attr(root, "session.id") == "mycoach:week:1:2024-06-10"

        gens = captured.generations()
        assert [g.name for g in gens] == [
            "gym_adjustment",
            "gym_adjustment",
            "gym_adjustment",
            "cardio_plan",
        ]
        # Every attempt, the retry included, is a sibling directly under the root.
        assert {trace_hex(g) for g in gens} == {trace_hex(root)}
        assert {parent_hex(g) for g in gens} == {span_hex(root)}

        # Each gym attempt, the retry included, names its slot as the
        # qualifier its code-check and adherence scores reference.
        assert [json_attr(g, "langfuse.observation.metadata.qualifier") for g in gens] == [
            "gym_adjustment:0",
            "gym_adjustment:1",
            "gym_adjustment:1",
            "cardio_plan",
        ]
        assert [json_attr(g, "langfuse.observation.metadata.day_of_week") for g in gens[:3]] == [
            0,
            2,
            2,
        ]

        failed, retry = gens[1], gens[2]
        assert json_attr(failed, "langfuse.observation.metadata.attempt") == 1
        assert attr(failed, "langfuse.observation.level") == "WARNING"
        parse_error = attr(failed, "langfuse.observation.status_message")
        assert parse_error
        assert json_attr(retry, "langfuse.observation.metadata.attempt") == 2
        assert json_attr(retry, "langfuse.observation.metadata.previous_error") == parse_error
        assert attr(retry, "langfuse.observation.level") is None


class TestPostWorkoutTrace:
    async def test_session_follows_the_activity_date_not_the_run_date(
        self, captured: Captured
    ) -> None:
        async with new_session() as session:
            user = User(email="t@example.com", name="T", fitness_level="intermediate")
            session.add(user)
            await session.flush()
            # A Thursday of the week before MONDAY, analysed later.
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
            assert activity.id == 1

            engine = CoachingEngine(llm_client=_post_workout_llm(), tracer=captured.tracer)
            await engine.generate_post_workout_analysis(session, user.id, activity.id)

        [root] = captured.roots()
        assert trace_hex(root) == "cc0219ed55252b07dbd1fe039bc88333"
        assert attr(root, "session.id") == "mycoach:week:1:2024-06-03"
        [gen] = captured.generations()
        assert gen.name == "post_workout"


class TestWeeklyRecapTrace:
    async def test_recap_goes_in_the_session_of_the_week_it_recaps(
        self, captured: Captured
    ) -> None:
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            engine = CoachingEngine(llm_client=_recap_llm(), tracer=captured.tracer)
            await engine.generate_weekly_recap(session, user_id, MONDAY)

        [root] = captured.roots()
        assert trace_hex(root) == "0770cfbac563ba742e33b5e00d781155"
        assert attr(root, "langfuse.trace.name") == "weekly-recap"
        assert attr(root, "session.id") == "mycoach:week:1:2024-06-10"


class TestErrorLevelGenerations:
    @pytest.mark.parametrize(
        "error",
        [LLMQuotaExceeded("Claude Code: usage limit reached"), LLMAuthError("bad token")],
    )
    async def test_typed_provider_error_is_an_error_generation(
        self, captured: Captured, error: Exception
    ) -> None:
        llm = _briefing_llm()
        llm.call.side_effect = error
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(llm_client=llm, tracer=captured.tracer)
            # The run's failure behaviour is unchanged.
            with pytest.raises(RuntimeError, match="Failed to generate daily briefing"):
                await engine.generate_daily_briefing(session, user_id, MONDAY)

        [gen] = captured.generations()
        assert attr(gen, "langfuse.observation.level") == "ERROR"
        assert gen.status.status_code is StatusCode.ERROR
        assert json_attr(gen, "langfuse.observation.metadata.error_type") == type(error).__name__
        assert attr(gen, "langfuse.observation.status_message") == str(error)
        [root] = captured.roots()
        assert attr(root, "langfuse.observation.level") == "ERROR"

    async def test_truncated_response_is_an_error_generation(self, captured: Captured) -> None:
        llm = _briefing_llm()
        truncated = LLMResponse(
            content='{"sleep_assessment": "Good',
            model="sonnet",
            input_tokens=500,
            output_tokens=4096,
            latency_ms=900,
            estimated_cost_usd=0.0,
            stop_reason="max_tokens",
        )
        llm.call.side_effect = [truncated, llm.call.return_value]
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(llm_client=llm, tracer=captured.tracer)
            await engine.generate_daily_briefing(session, user_id, MONDAY)

        first, retry = captured.generations()
        assert attr(first, "langfuse.observation.level") == "ERROR"
        assert json_attr(first, "langfuse.observation.metadata.truncated") is True
        assert json_attr(first, "langfuse.observation.metadata.stop_reason") == "max_tokens"
        assert json_attr(retry, "langfuse.observation.model.parameters") == {"max_tokens": 8192}


class TestThreadHop:
    async def test_trace_context_reaches_the_provider_worker_thread(
        self, captured: Captured
    ) -> None:
        llm = _briefing_llm()
        response: LLMResponse = llm.call.return_value
        seen: list[tuple[int, str]] = []

        def call(**_kwargs: object) -> LLMResponse:
            ctx = trace.get_current_span().get_span_context()
            seen.append((threading.get_ident(), format(ctx.trace_id, "032x")))
            return response

        llm.call.side_effect = call
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(llm_client=llm, tracer=captured.tracer)
            await engine.generate_daily_briefing(session, user_id, MONDAY)

        [(thread_id, trace_in_thread)] = seen
        assert thread_id != threading.get_ident()
        assert trace_in_thread == "ef2156ef5a01aa2158bfb9e1b4a8fb29"


async def _briefing_generation(captured: Captured, response: LLMResponse) -> ReadableSpan:
    llm = _briefing_llm()
    llm.call.return_value = response
    async with new_session() as session:
        user_id = await _create_briefing_user(session)
        await _add_recovery_snapshot(session, user_id, MONDAY)
        engine = CoachingEngine(llm_client=llm, tracer=captured.tracer)
        await engine.generate_daily_briefing(session, user_id, MONDAY)
    [gen] = captured.generations()
    return gen


class TestBillingAndQuota:
    async def test_subscription_generation_sends_zero_cost_and_its_quota(
        self, captured: Captured
    ) -> None:
        response: LLMResponse = _briefing_llm().call.return_value
        response.billing = "subscription"
        response.quota_usage = {"unit": "usd_notional", "amount": 0.0421}
        response.estimated_cost_usd = 0.0421

        gen = await _briefing_generation(captured, response)

        assert json_attr(gen, "langfuse.observation.cost_details") == {"total": 0}
        assert json_attr(gen, "langfuse.observation.usage_details") == {
            "input": 500,
            "output": 200,
        }
        assert json_attr(gen, "langfuse.observation.metadata.billing") == "subscription"
        assert json_attr(gen, "langfuse.observation.metadata.quota_usage") == {
            "unit": "usd_notional",
            "amount": 0.0421,
        }

    async def test_paid_generation_sends_its_cost_unchanged(self, captured: Captured) -> None:
        response: LLMResponse = _briefing_llm().call.return_value
        response.billing = "paid"
        response.estimated_cost_usd = 0.0105

        gen = await _briefing_generation(captured, response)

        assert json_attr(gen, "langfuse.observation.cost_details") == {"total": 0.0105}
        assert json_attr(gen, "langfuse.observation.metadata.billing") == "paid"


class _FailingExporter(SpanExporter):
    def __init__(self) -> None:
        self.calls = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.calls += 1
        raise ConnectionError("langfuse-web:3000 unreachable")

    def shutdown(self) -> None:
        pass


class TestLangfuseFailures:
    async def test_export_failure_is_logged_at_warn_and_never_reaches_the_run(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        exporter = _FailingExporter()
        tracer = tracer_with(exporter)
        try:
            async with new_session() as session:
                user_id = await _create_briefing_user(session)
                await _add_recovery_snapshot(session, user_id, MONDAY)
                engine = CoachingEngine(llm_client=_briefing_llm(), tracer=tracer)
                insight = await engine.generate_daily_briefing(session, user_id, MONDAY)
            with caplog.at_level(logging.WARNING, logger="mycoach.observability.tracing"):
                tracer.flush()
        finally:
            LangfuseResourceManager.reset()

        assert "go_hard" in insight.content
        assert exporter.calls >= 1
        ours = [r for r in caplog.records if r.name == "mycoach.observability.tracing"]
        assert ours
        assert all(r.levelno == logging.WARNING for r in ours)

    async def test_client_failure_is_logged_at_warn_and_never_reaches_the_run(
        self,
        captured: Captured,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("SDK bug")

        client = captured.tracer._lf  # type: ignore[attr-defined]
        monkeypatch.setattr(client, "start_as_current_observation", boom)
        monkeypatch.setattr(client, "start_observation", boom)
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(llm_client=_briefing_llm(), tracer=captured.tracer)
            with caplog.at_level(logging.WARNING, logger="mycoach.observability.tracing"):
                insight = await engine.generate_daily_briefing(session, user_id, MONDAY)

        assert "go_hard" in insight.content
        messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("opening the daily-briefing trace failed" in m for m in messages)
        assert any("starting a daily_briefing generation failed" in m for m in messages)


class TestDisabled:
    async def test_engine_defaults_to_a_noop_tracer_when_langfuse_is_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            monkeypatch.delenv(var, raising=False)
        set_tracer(None)
        try:
            engine = CoachingEngine(llm_client=_briefing_llm())
            assert engine._tracer.enabled is False
        finally:
            set_tracer(None)
