"""Every coaching run reads the athlete's open and recent life context episodes (#220).

Briefing, weekly plan (gym and cardio), post-workout and recap each get a
``## Life Context`` section. Nothing regenerates the plan mid-week.
"""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from mycoach.coaching.engine import CoachingEngine
from mycoach.models.activity import Activity
from mycoach.models.life_context import LifeContextEpisode
from tests.conftest import test_session
from tests.test_coaching.test_engine import _add_recovery_snapshot, _create_user
from tests.test_coaching.test_engine import _mock_llm_client as _briefing_llm
from tests.test_observability.test_engine_tracing import (
    _gym_response,
    _setup_two_gym_one_cardio,
)
from tests.test_plans.test_engine import VALID_CARDIO_RESPONSE, VALID_GYM_ADJUSTMENT
from tests.test_post_workout.test_engine import _mock_llm_client as _post_workout_llm
from tests.test_weekly_recap.test_engine import _mock_llm_client as _recap_llm

MONDAY = date(2024, 6, 10)
THURSDAY = date(2024, 6, 13)


def _sick(user_id: int, start: date, end: date | None) -> LifeContextEpisode:
    return LifeContextEpisode(
        user_id=user_id, kind="illness", severity="sick", started_on=start, ended_on=end,
        note="fever",
    )  # fmt: skip


def _clock(day: date) -> object:
    return lambda: datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)


async def test_the_briefing_knows_the_athlete_is_ill_today() -> None:
    async with test_session() as session:
        user_id = await _create_user(session)
        await _add_recovery_snapshot(session, user_id, THURSDAY)
        session.add(_sick(user_id, date(2024, 6, 12), None))
        await session.commit()

        llm = _briefing_llm()
        await CoachingEngine(llm_client=llm).generate_daily_briefing(session, user_id, THURSDAY)

    user_message = llm.call.call_args.kwargs["user_message"]
    assert (
        "## Life Context\n- Illness, sick (below the neck): since Wednesday 2024-06-12, "
        "still unwell today (day 2). Note: fever\n"
    ) in user_message


async def test_the_briefing_carries_the_ramp_back_day() -> None:
    async with test_session() as session:
        user_id = await _create_user(session)
        await _add_recovery_snapshot(session, user_id, THURSDAY)
        session.add(_sick(user_id, date(2024, 6, 5), date(2024, 6, 11)))  # 7 days
        await session.commit()

        llm = _briefing_llm()
        await CoachingEngine(llm_client=llm).generate_daily_briefing(session, user_id, THURSDAY)

    assert (
        "Returning from illness: ramp-back day 2/7, about 57% of normal training load today"
        in llm.call.call_args.kwargs["user_message"]
    )


async def test_the_weekly_plan_gym_and_cardio_prompts_know_about_an_open_illness() -> None:
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
        session.add(_sick(user_id, date(2024, 6, 8), None))
        await session.commit()
        sunday = date(2024, 6, 9)
        await CoachingEngine(llm_client=llm, clock=_clock(sunday)).generate_weekly_plan(
            session, user_id, MONDAY
        )

    for call in llm.call.call_args_list:
        assert "still unwell today (day 2)" in call.kwargs["user_message"]


async def test_the_post_workout_run_reads_the_context_on_the_activity_day() -> None:
    async with test_session() as session:
        user_id = await _create_user(session)
        session.add(_sick(user_id, date(2024, 6, 1), date(2024, 6, 4)))  # 4 days: 4-day ramp
        activity = Activity(
            user_id=user_id,
            sport="running",
            title="Easy run",
            start_time=datetime(2024, 6, 6, 9, 0),
            duration_minutes=20,
            data_source="garmin",
            external_id="run-1",
        )
        session.add(activity)
        await session.commit()
        llm = _post_workout_llm()
        await CoachingEngine(llm_client=llm).generate_post_workout_analysis(
            session, user_id, "run-1"
        )

    user_message = llm.call.call_args.kwargs["user_message"]
    assert "## Life Context\n" in user_message
    assert "ramp-back day 2/4" in user_message


async def test_the_recap_reads_the_weeks_episodes() -> None:
    async with test_session() as session:
        user_id = await _create_user(session)
        session.add(_sick(user_id, date(2024, 6, 10), date(2024, 6, 12)))
        await session.commit()
        llm = _recap_llm()
        await CoachingEngine(llm_client=llm, clock=_clock(date(2024, 6, 17))).generate_weekly_recap(
            session, user_id, MONDAY
        )

    assert (
        "## Life Context\n- Illness, sick (below the neck): Monday 2024-06-10 to "
        "Wednesday 2024-06-12 (3 days). Note: fever"
    ) in llm.call.call_args.kwargs["user_message"]
