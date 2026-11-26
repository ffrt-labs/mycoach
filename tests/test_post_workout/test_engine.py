"""Tests for post-workout analysis in coaching engine."""

import json
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import PipelineSkip
from mycoach.coaching.llm_client import LLMResponse
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session

VALID_POST_WORKOUT_RESPONSE = """{
  "headline": {
    "tone": "good",
    "verdict": "Solid push day",
    "summary": "Bench moved well and every set was clean."
  },
  "actions": [
    {"do": "Add 2.5 kg to the bench next session.", "why": "All sets at RPE 8 or under."},
    {"do": "Time your rests.", "why": null}
  ],
  "zone_note": null,
  "trend_note": "Bench is climbing steadily.",
  "next_session": [
    {"phase": "main", "set": "3x6", "cue": "Pause on the chest", "target": "85 kg", "rest_s": 150}
  ],
  "guardrails": [{"when": "Shoulder pinches", "then": "Swap to dumbbell press"}],
  "week_addition": null,
  "ready_in_hours": 48,
  "recovery": "Protein within 2 h, 7+ h of sleep.",
  "analysis_md": "## Push day\\nBench moved well."
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
    return activity.external_id


class TestGeneratePostWorkoutAnalysis:
    async def test_success(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_post_workout_analysis(session, user_id, activity_id)

            assert insight.insight_type == "post_workout"
            assert insight.activity_external_id == activity_id
            assert insight.user_id == user_id
            assert json.loads(insight.content)["headline"]["verdict"] == "Solid push day"
            mock_llm.call.assert_called_once()

    async def test_duplicate_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            await engine.generate_post_workout_analysis(session, user_id, activity_id)

            with pytest.raises(PipelineSkip, match="already exists"):
                await engine.generate_post_workout_analysis(session, user_id, activity_id)

    async def test_activity_not_found_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)

            mock_llm = _mock_llm_client()
            engine = CoachingEngine(llm_client=mock_llm)
            with pytest.raises(ValueError, match="not found"):
                await engine.generate_post_workout_analysis(session, user_id, 999)

    async def test_llm_failure_raises(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = Exception("API timeout")
            engine = CoachingEngine(llm_client=mock_llm)

            with pytest.raises(RuntimeError, match="Failed to generate"):
                await engine.generate_post_workout_analysis(session, user_id, activity_id)

    async def test_invalid_json_retries_with_the_parse_error(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = [
                LLMResponse(
                    content="not valid json",
                    model="claude-sonnet-4-5-20250929",
                    input_tokens=800,
                    output_tokens=20,
                    latency_ms=900,
                    estimated_cost_usd=0.003,
                ),
                LLMResponse(
                    content=VALID_POST_WORKOUT_RESPONSE,
                    model="claude-sonnet-4-5-20250929",
                    input_tokens=850,
                    output_tokens=300,
                    latency_ms=1500,
                    estimated_cost_usd=0.007,
                ),
            ]
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_post_workout_analysis(session, user_id, activity_id)

            assert json.loads(insight.content)["headline"]["verdict"] == "Solid push day"
            first, retry = mock_llm.call.call_args_list
            assert retry.kwargs["max_tokens"] == first.kwargs["max_tokens"] == 4096
            assert "Your previous response had this error:" in retry.kwargs["user_message"]

    async def test_truncated_response_retries_with_doubled_budget(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)

            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = [
                LLMResponse(
                    content='{"performance_summary": "Solid',
                    model="claude-sonnet-4-5-20250929",
                    input_tokens=800,
                    output_tokens=4096,
                    latency_ms=900,
                    estimated_cost_usd=0.003,
                    stop_reason="max_tokens",
                ),
                LLMResponse(
                    content="still not json",
                    model="claude-sonnet-4-5-20250929",
                    input_tokens=850,
                    output_tokens=30,
                    latency_ms=1500,
                    estimated_cost_usd=0.007,
                ),
            ]
            engine = CoachingEngine(llm_client=mock_llm)
            insight = await engine.generate_post_workout_analysis(session, user_id, activity_id)

            assert json.loads(insight.content) is None  # Coach notes fallback
            assert mock_llm.call.call_args_list[1].kwargs["max_tokens"] == 8192

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
            session.add(
                GymWorkoutDetail(
                    activity_id=activity.id, exercise_title="Bench Press", set_index=1, reps=8
                )
            )
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
            await engine.generate_post_workout_analysis(session, user_id, activity.external_id)

            # Verify planned session is now linked
            await session.refresh(planned)
            assert planned.completed is True
            assert planned.activity_external_id == activity.external_id

    async def test_empty_activity_does_not_complete_planned_session(self) -> None:
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
            await engine.generate_post_workout_analysis(session, user_id, activity.external_id)

            # The insight is still produced, but the prescription stays open
            await session.refresh(planned)
            assert planned.completed is False
            assert planned.activity_external_id is None

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
            assert insight.activity_external_id == activity_id


def _response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="claude-sonnet-4-5-20250929",
        input_tokens=800,
        output_tokens=300,
        latency_ms=1500,
        estimated_cost_usd=0.007,
    )


def _over_length() -> str:
    """A v5 answer whose first action is longer than the 70-character slot."""
    data = json.loads(VALID_POST_WORKOUT_RESPONSE)
    data["actions"][0]["do"] = "Add 2.5 kg to the bench next session, and keep every rep slow. " * 2
    return json.dumps(data)


class TestCoachNotesContract:
    """Prompt v5 (#238): Coach notes in typed slots, one retry, then the fallback."""

    async def test_the_call_carries_the_strict_schema_of_the_post_workout_notes(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)
            mock_llm = _mock_llm_client()
            await CoachingEngine(llm_client=mock_llm).generate_post_workout_analysis(
                session, user_id, activity_id
            )

        schema = mock_llm.call.call_args.kwargs["response_schema"]
        assert schema.name == "post_workout"
        assert schema.schema["additionalProperties"] is False
        assert "ready_in_hours" in schema.schema["required"]

    async def test_an_over_length_slot_is_retried_with_the_validation_error(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)
            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = [
                _response(_over_length()),
                _response(VALID_POST_WORKOUT_RESPONSE),
            ]
            insight = await CoachingEngine(llm_client=mock_llm).generate_post_workout_analysis(
                session, user_id, activity_id
            )

        assert json.loads(insight.content)["actions"][0]["do"].startswith("Add 2.5 kg")
        retry = mock_llm.call.call_args_list[1]
        assert "at most 70 characters" in retry.kwargs["user_message"]

    async def test_a_second_invalid_answer_stores_the_insight_without_coach_notes(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            activity_id = await _create_activity(session, user_id)
            mock_llm = _mock_llm_client()
            mock_llm.call.side_effect = [_response(_over_length()), _response(_over_length())]
            insight = await CoachingEngine(llm_client=mock_llm).generate_post_workout_analysis(
                session, user_id, activity_id
            )

            assert insight.insight_type == "post_workout"
            assert insight.activity_external_id == activity_id
            assert json.loads(insight.content) is None

    async def test_a_fallback_still_links_the_planned_session(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
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
            session.add(
                GymWorkoutDetail(
                    activity_id=activity.id, exercise_title="Bench Press", set_index=1, reps=8
                )
            )
            plan = WeeklyPlan(
                user_id=user_id, week_start=date(2024, 6, 10), status="active", summary="Plan"
            )
            session.add(plan)
            await session.flush()
            planned = PlannedSession(
                plan_id=plan.id, day_of_week=2, sport="gym", title="Upper Body", duration_minutes=60
            )
            session.add(planned)
            await session.commit()

            mock_llm = _mock_llm_client("not json")
            await CoachingEngine(llm_client=mock_llm).generate_post_workout_analysis(
                session, user_id, activity.external_id
            )

            await session.refresh(planned)
            assert planned.activity_external_id == activity.external_id
