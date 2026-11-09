"""Adherence scores at Prescription fulfillment: what is sent, when, and where."""

import json
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.engine import CoachingEngine
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from mycoach.observability.trace_ids import trace_id, weekly_plan_seed
from mycoach.plan_linking import claim_planned_session, link_activity_to_planned_session
from tests.conftest import test_session
from tests.test_observability.conftest import RecordingScoreSink
from tests.test_observability.test_engine_tracing import _gym_response, _setup_two_gym_one_cardio
from tests.test_plans.test_engine import VALID_CARDIO_RESPONSE, VALID_GYM_ADJUSTMENT
from tests.test_post_workout.test_engine import _mock_llm_client as _post_workout_llm

WEEK = date(2024, 6, 10)


async def _user(session: AsyncSession) -> int:
    user = User(email="t@example.com", name="T", fitness_level="intermediate")
    session.add(user)
    await session.flush()
    return user.id


async def _plan(session: AsyncSession, user_id: int) -> WeeklyPlan:
    plan = WeeklyPlan(user_id=user_id, week_start=WEEK, status="active", summary="Test")
    session.add(plan)
    await session.flush()
    return plan


async def _gym_session(session: AsyncSession, plan: WeeklyPlan, day: int) -> PlannedSession:
    details = {
        "exercises": [
            {
                "exercise_id": "bench",
                "name": "Bench",
                "sets": 2,
                "reps": "8",
                "target_weight_kg": 100,
                "rpe": 8,
            },
        ]
    }
    planned = PlannedSession(
        plan_id=plan.id,
        day_of_week=day,
        sport="gym",
        title="Push",
        details=json.dumps(details),
        track="gym",
    )
    session.add(planned)
    await session.flush()
    return planned


async def _activity(
    session: AsyncSession,
    user_id: int,
    *,
    sets: int = 2,
    minutes: int | None = None,
    sport: str = "gym",
    source: str = "logger",
) -> Activity:
    activity = Activity(
        user_id=user_id,
        sport=sport,
        title="Push",
        start_time=datetime(2024, 6, 12, 9),
        data_source=source,
        duration_minutes=minutes,
    )
    session.add(activity)
    await session.flush()
    for i in range(sets):
        session.add(
            GymWorkoutDetail(
                activity_id=activity.id,
                exercise_id="bench",
                exercise_title="Bench",
                set_index=i,
                weight_kg=110,
                reps=8,
                rpe=9,
            )
        )
    await session.flush()
    return activity


async def _gym_setup(session: AsyncSession) -> tuple[int, WeeklyPlan, PlannedSession, Activity]:
    user_id = await _user(session)
    plan = await _plan(session, user_id)
    await _gym_session(session, plan, day=0)
    planned = await _gym_session(session, plan, day=2)  # the plan's second gym slot
    activity = await _activity(session, user_id)
    return user_id, plan, planned, activity


def _id(plan: WeeklyPlan, name: str, planned: PlannedSession) -> str:
    return f"{trace_id(weekly_plan_seed(plan.id))}:{name}:planned_session:{planned.id}"


class TestBothFulfillmentPaths:
    async def test_post_workout_link_scores_the_weekly_plan_trace_after_commit(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            _, plan, planned, activity = await _gym_setup(session)

            await link_activity_to_planned_session(session, activity.id, planned.id, scores=sink)
            assert sink.scores == []  # nothing before the commit

            await session.commit()

        assert sink.values() == {
            _id(plan, "adherence_set_completion", planned): 1.0,
            _id(plan, "adherence_weight_delta_pct", planned): pytest.approx(10.0),
            _id(plan, "adherence_reps_delta", planned): 0.0,
            _id(plan, "adherence_rpe_delta", planned): 1.0,
        }

    async def test_import_claim_scores_the_weekly_plan_trace_after_commit(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            user_id, plan, planned, activity = await _gym_setup(session)

            declined = await claim_planned_session(
                session, user_id, activity.id, planned.id, scores=sink
            )
            assert declined is None
            assert sink.scores == []

            await session.commit()

        assert _id(plan, "adherence_set_completion", planned) in sink.by_id()


class TestScoreContent:
    async def test_gym_scores_carry_the_planned_session_and_the_slots_generation(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            _, plan, planned, activity = await _gym_setup(session)
            await link_activity_to_planned_session(session, activity.id, planned.id, scores=sink)
            await session.commit()

        for score in sink.scores:
            assert score.trace_id == trace_id(weekly_plan_seed(plan.id))
            assert score.metadata["planned_session_id"] == planned.id
            assert score.metadata["generation"] == "gym_adjustment:1"
            assert score.metadata["day_of_week"] == 2
            # A stable day, so a later re-link overwrites.
            assert score.timestamp == datetime(2024, 6, 10, tzinfo=UTC)

    async def test_the_timestamp_comes_from_the_injected_clock_before_the_plans_week(
        self,
    ) -> None:
        sink = RecordingScoreSink()
        before_the_week = datetime(2024, 6, 9, 18, tzinfo=UTC)
        async with test_session() as session:
            user_id, _, planned, activity = await _gym_setup(session)
            await claim_planned_session(
                session,
                user_id,
                activity.id,
                planned.id,
                scores=sink,
                clock=lambda: before_the_week,
            )
            await session.commit()

        assert {s.timestamp for s in sink.scores} == {before_the_week}

    async def test_cardio_scores_the_duration_ratio(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            user_id = await _user(session)
            plan = await _plan(session, user_id)
            planned = PlannedSession(
                plan_id=plan.id,
                day_of_week=1,
                sport="running",
                title="Easy run",
                duration_minutes=40,
                track="cardio",
            )
            session.add(planned)
            await session.flush()
            activity = await _activity(session, user_id, sets=1, minutes=30, sport="running")

            await link_activity_to_planned_session(session, activity.id, planned.id, scores=sink)
            await session.commit()

        assert sink.values() == {_id(plan, "adherence_duration_ratio", planned): 0.75}
        assert "generation" not in sink.scores[0].metadata
        assert sink.scores[0].metadata["planned_session_id"] == planned.id


class TestWhenNothingIsSent:
    async def test_a_rolled_back_link_sends_nothing(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            _, _, planned, activity = await _gym_setup(session)
            await session.commit()

            await link_activity_to_planned_session(session, activity.id, planned.id, scores=sink)
            await session.rollback()
            await session.commit()  # a later commit must not resurrect them

        assert sink.scores == []

    async def test_a_link_with_no_performed_sets_sends_nothing(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            user_id = await _user(session)
            plan = await _plan(session, user_id)
            planned = await _gym_session(session, plan, day=0)
            empty = await _activity(session, user_id, sets=0)

            await link_activity_to_planned_session(session, empty.id, planned.id, scores=sink)
            declined = await claim_planned_session(
                session, user_id, empty.id, planned.id, scores=sink
            )
            await session.commit()

        assert declined is not None
        assert sink.scores == []

    async def test_a_declined_claim_sends_nothing(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            user_id, _, planned, activity = await _gym_setup(session)
            other = await _activity(session, user_id)
            planned.activity_id = other.id
            await session.flush()

            declined = await claim_planned_session(
                session, user_id, activity.id, planned.id, scores=sink
            )
            await session.commit()

        assert declined is not None
        assert sink.scores == []


class TestIdempotency:
    async def test_relinking_the_same_planned_session_reuses_the_score_ids(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            user_id, _, planned, activity = await _gym_setup(session)
            await link_activity_to_planned_session(session, activity.id, planned.id, scores=sink)
            await session.commit()
            first = [s.id for s in sink.scores]

            sink.scores.clear()
            await claim_planned_session(session, user_id, activity.id, planned.id, scores=sink)
            await session.commit()

        assert [s.id for s in sink.scores] == first


class TestPostWorkoutRun:
    async def test_the_engines_sink_gets_the_adherence_of_the_linked_session(self) -> None:
        sink = RecordingScoreSink()
        async with test_session() as session:
            user_id = await _user(session)
            plan = await _plan(session, user_id)
            planned = PlannedSession(
                plan_id=plan.id,
                day_of_week=2,
                sport="running",
                title="Easy run",
                duration_minutes=50,
                track="cardio",
            )
            session.add(planned)
            activity = await _activity(
                session, user_id, sets=1, minutes=40, sport="running", source="garmin"
            )
            await session.commit()

            engine = CoachingEngine(llm_client=_post_workout_llm(), scores=sink)
            await engine.generate_post_workout_analysis(session, user_id, activity.id)

        assert sink.values()[_id(plan, "adherence_duration_ratio", planned)] == 0.8


class TestGymSlotContract:
    async def test_each_gym_sessions_adherence_names_the_generation_that_planned_it(
        self,
    ) -> None:
        """The ordering contract between the weekly plan and ``_gym_slot``.

        The engine adds one gym planned session per gym slot, in slot order,
        and fulfillment recovers the slot index from that order. If either
        side changes, an adherence score would name another slot's generation.
        """
        sink = RecordingScoreSink()
        llm = MagicMock()
        llm.daily_model = "claude-sonnet-4-5-20250929"
        llm.weekly_model = "claude-opus-4-6"
        llm.call.side_effect = [
            _gym_response(VALID_GYM_ADJUSTMENT),
            _gym_response(VALID_GYM_ADJUSTMENT),
            _gym_response(VALID_CARDIO_RESPONSE),
        ]
        async with test_session() as session:
            user_id = await _setup_two_gym_one_cardio(session)
            engine = CoachingEngine(llm_client=llm, scores=sink)
            plan = await engine.generate_weekly_plan(session, user_id, WEEK)
            slot_of_day = {
                s.metadata["day_of_week"]: s.qualifier
                for s in sink.scores
                if s.name == "parse_ok" and s.metadata["operation"] == "gym_adjustment"
            }
            assert slot_of_day == {0: "gym_adjustment:0", 2: "gym_adjustment:1"}

            stmt = select(PlannedSession).where(
                PlannedSession.plan_id == plan.id, PlannedSession.track == "gym"
            )
            gym_sessions = (await session.execute(stmt)).scalars().all()
            sink.scores.clear()
            for planned in gym_sessions:
                activity = await _activity(session, user_id)
                await link_activity_to_planned_session(
                    session, activity.id, planned.id, scores=sink
                )
            await session.commit()

        linked = {s.metadata["day_of_week"]: s.metadata["generation"] for s in sink.scores}
        assert linked == slot_of_day
