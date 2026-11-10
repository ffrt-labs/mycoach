"""Coaching's side of the health events (map #85, ticket #186, designs #111 and #112).

Health returns events as data; the call site dispatches them here after health's commit.
Each handler is isolated, idempotent by deterministic event id, and safe to re-invoke by hand.
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from mycoach import health
from mycoach.coaching.exceptions import PipelineSkip
from mycoach.coaching.health_events import (
    REVISION_FIELDS,
    claim_on_recorded,
    dispatch,
    post_workout_on_revised,
    post_workout_on_settled,
)
from mycoach.health.events import (
    CanonicalActivityRecorded,
    CanonicalActivityRevised,
    CanonicalActivitySettled,
)
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from mycoach.sources.workout_import import WorkoutImport, WorkoutSetImport
from tests.conftest import test_session

START = datetime(2026, 11, 2, 9, 0)
NOW = datetime(2026, 11, 2, 12, 0)


def _recorded(ext: str = "uuid-1") -> CanonicalActivityRecorded:
    return CanonicalActivityRecorded.build(1, ext, "gym", "logger", NOW)


def _settled(ext: str = "uuid-1") -> CanonicalActivitySettled:
    return CanonicalActivitySettled.build(1, ext, "gym", NOW, "reconciled")


def _revised(fields: list[str], at: datetime = NOW) -> CanonicalActivityRevised:
    return CanonicalActivityRevised.build(1, "uuid-1", "gym", at, fields)


class TestDispatch:
    async def test_a_failing_handler_does_not_block_its_siblings_or_raise(self):
        seen: list[str] = []

        async def boom(event) -> None:
            raise RuntimeError("coaching is down")

        async def ok(event) -> None:
            seen.append(event.event_id)

        result = await dispatch(
            [_recorded()], {CanonicalActivityRecorded: [boom, ok]}
        )
        assert seen == [_recorded().event_id]
        assert len(result.failures) == 1
        assert result.failures[0].event_id == _recorded().event_id
        assert "coaching is down" in result.failures[0].error

    async def test_events_without_handlers_are_ignored(self):
        result = await dispatch([_settled()], {CanonicalActivityRecorded: []})
        assert result.failures == [] and result.notes == []

    async def test_handler_notes_are_collected(self):
        async def declines(event) -> str:
            return "declined because"

        result = await dispatch([_recorded()], {CanonicalActivityRecorded: [declines]})
        assert result.notes == ["declined because"]


async def _plan_with_session() -> tuple[int, int]:
    async with test_session() as session:
        user = User(id=1, email="t@e.com", name="T", health_athlete_id=1)
        session.add(user)
        plan = WeeklyPlan(user_id=1, week_start=date(2026, 11, 2), status="active", summary="s")
        session.add(plan)
        await session.flush()
        planned = PlannedSession(
            plan_id=plan.id, day_of_week=0, sport="gym", title="Push", duration_minutes=60
        )
        session.add(planned)
        await session.commit()
        return 1, planned.id


def _workout() -> WorkoutImport:
    return WorkoutImport(
        title="Push",
        start_time=START,
        external_id="uuid-1",
        sets=[WorkoutSetImport(exercise_title="Bench", set_index=1, weight_kg=80, reps=8)],
    )


class TestClaimOnRecorded:
    async def test_links_the_planned_session_by_external_id(self):
        user_id, planned_id = await _plan_with_session()
        await health.record_workout(1, "logger", _workout())

        handler = claim_on_recorded(user_id, planned_id)
        assert await handler(_recorded()) is None

        async with test_session() as session:
            planned = (await session.execute(select(PlannedSession))).scalar_one()
        assert planned.activity_external_id == "uuid-1"
        assert planned.completed is True

    async def test_redelivery_of_the_same_event_is_a_no_op(self):
        user_id, planned_id = await _plan_with_session()
        await health.record_workout(1, "logger", _workout())
        handler = claim_on_recorded(user_id, planned_id)
        await handler(_recorded())
        assert await handler(_recorded()) is None

    async def test_a_declined_claim_is_a_note_not_an_exception(self):
        user_id, _ = await _plan_with_session()
        await health.record_workout(1, "logger", _workout())
        note = await claim_on_recorded(user_id, 9999)(_recorded())
        assert note is not None and "not found" in note


def _fake_engine(raises: Exception | None = None) -> MagicMock:
    engine = MagicMock()
    insight = MagicMock(content='{"performance_summary": "ok"}')
    engine.generate_post_workout_analysis = AsyncMock(side_effect=raises, return_value=insight)
    return engine


class TestPostWorkoutOnSettled:
    async def test_generates_the_analysis_for_the_external_id_and_notifies(self):
        engine = _fake_engine()
        notified: list[tuple[str, str]] = []
        handler = post_workout_on_settled(
            1,
            engine_factory=lambda: engine,
            notify=lambda insight, ext: notified.append((insight.content, ext)),
        )
        await handler(_settled())
        args = engine.generate_post_workout_analysis.call_args
        assert args.args[1:] == (1, "uuid-1")
        assert notified == [('{"performance_summary": "ok"}', "uuid-1")]

    async def test_an_existing_analysis_is_skipped_quietly(self):
        engine = _fake_engine(PipelineSkip("already"))
        handler = post_workout_on_settled(1, engine_factory=lambda: engine)
        assert await handler(_settled()) is None  # idempotent: safe to re-invoke by hand


class TestPostWorkoutOnRevised:
    async def test_a_prompt_field_change_asks_for_an_insight_revision(self):
        engine = _fake_engine()
        handler = post_workout_on_revised(1, engine_factory=lambda: engine)
        event = _revised(["avg_hr", "title"])
        await handler(event)
        kwargs = engine.generate_post_workout_analysis.call_args.kwargs
        assert kwargs["revision_event_id"] == event.event_id

    async def test_a_change_outside_the_prompt_fields_produces_no_revision(self):
        engine = _fake_engine()
        handler = post_workout_on_revised(1, engine_factory=lambda: engine)
        await handler(_revised(["title", "notes"]))
        engine.generate_post_workout_analysis.assert_not_called()

    def test_the_relevant_fields_are_the_post_workout_prompts(self):
        assert {
            "avg_hr",
            "max_hr",
            "calories",
            "training_effect_aerobic",
            "training_effect_anaerobic",
            "epoc",
            "recovery_time_minutes",
            "distance_meters",
            "duration_minutes",
        } == REVISION_FIELDS
