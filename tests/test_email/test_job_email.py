"""Tests for email send triggers in scheduler jobs."""

import json
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.job_run import JobRun
from mycoach.models.user import User
from mycoach.scheduler.jobs import (
    USER_ID,
    _briefing_alert,
    _daily_briefing,
    _notify_post_workout,
    _post_workout_analysis,
    _weekly_recap,
)
from tests.conftest import test_session as db_session


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture
def mock_insight() -> MagicMock:
    insight = MagicMock()
    insight.content = json.dumps(
        {
            "readiness_verdict": "go_hard",
            "recovery_status": "Ready",
            "sleep_assessment": "Good",
        }
    )
    return insight


async def test_daily_briefing_sends_email(mock_session: AsyncMock, mock_insight: MagicMock) -> None:
    """Daily briefing job sends email when preference is enabled."""
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[])),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing()
        mock_send.assert_called_once()


async def test_daily_briefing_email_gets_todays_health_for_the_hrv_line(
    mock_session: AsyncMock, mock_insight: MagicMock
) -> None:
    """The HRV line is rendered from the DB snapshot of the briefed day (#197)."""
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)
    today_health = {"hrv_7day_avg": 52.0, "hrv_baseline_low": 47.0}
    lookup = AsyncMock(return_value=today_health)

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[])),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs._todays_health", lookup),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing(date(2026, 11, 14))

    lookup.assert_awaited_once_with(date(2026, 11, 14))
    assert mock_send.call_args.kwargs["hrv"] == today_health


async def test_daily_briefing_skips_email_when_disabled(
    mock_session: AsyncMock, mock_insight: MagicMock
) -> None:
    """Daily briefing job does NOT send email when preference is disabled."""
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[])),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing()
        mock_send.assert_not_called()



async def test_weekly_recap_sends_email(
    mock_session: AsyncMock,
) -> None:
    """Weekly recap job sends email when preference is enabled."""
    insight = MagicMock()
    insight.content = json.dumps({"week_summary": "Solid"})
    mock_engine = MagicMock()
    mock_engine.generate_weekly_recap = AsyncMock(return_value=insight)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_weekly_recap") as mock_send,
    ):
        await _weekly_recap()
        mock_send.assert_called_once()


async def test_weekly_recap_email_gets_the_recapped_weeks_adherence(
    mock_session: AsyncMock,
) -> None:
    """The completion line comes from the DB, for the week the recap covers (#217)."""
    insight = MagicMock()
    insight.content = json.dumps({"week_summary": "Solid"})
    mock_engine = MagicMock()
    mock_engine.generate_weekly_recap = AsyncMock(return_value=insight)
    adherence = {"total_sessions": 5, "completed_sessions": 4, "substituted_sessions": 1}
    load = AsyncMock(return_value=adherence)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.get_plan_adherence_for_week", load),
        patch("mycoach.scheduler.jobs.send_weekly_recap") as mock_send,
    ):
        await _weekly_recap()

    week_start = mock_engine.generate_weekly_recap.call_args[0][2]
    assert load.call_args[0][2] == week_start
    assert mock_send.call_args.kwargs["adherence"] == adherence


async def test_weekly_recap_email_gets_the_episodes_touching_the_recapped_week(
    mock_session: AsyncMock,
) -> None:
    """The recap's Life context section lists the week's episodes (#232)."""
    insight = MagicMock()
    insight.content = json.dumps({"week_summary": "Solid"})
    mock_engine = MagicMock()
    mock_engine.generate_weekly_recap = AsyncMock(return_value=insight)
    episodes = [MagicMock(name="trip")]
    load = AsyncMock(return_value=episodes)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.get_plan_adherence_for_week", AsyncMock(return_value=None)),
        patch("mycoach.scheduler.jobs.episodes_between", load),
        patch("mycoach.scheduler.jobs.send_weekly_recap") as mock_send,
    ):
        await _weekly_recap()

    week_start = mock_engine.generate_weekly_recap.call_args[0][2]
    assert load.call_args[0][2:] == (week_start, week_start + timedelta(days=6))
    assert mock_send.call_args.kwargs["episodes"] == episodes


async def test_weekly_recap_email_still_goes_when_the_episodes_cannot_be_read(
    mock_session: AsyncMock,
) -> None:
    insight = MagicMock()
    insight.content = json.dumps({"week_summary": "Solid"})
    mock_engine = MagicMock()
    mock_engine.generate_weekly_recap = AsyncMock(return_value=insight)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.get_plan_adherence_for_week", AsyncMock(return_value=None)),
        patch("mycoach.scheduler.jobs.episodes_between", AsyncMock(side_effect=RuntimeError)),
        patch("mycoach.scheduler.jobs.send_weekly_recap") as mock_send,
    ):
        await _weekly_recap()

    assert mock_send.call_args.kwargs["episodes"] is None


# --- The code-rendered steps strip (#196) ---------------------------------------------


async def _seed_steps(day: date, steps: int) -> None:
    async with db_session() as session:
        session.add(User(id=USER_ID, email="a@example.com", name="A"))
        session.add(DailyHealthSnapshot(user_id=USER_ID, snapshot_date=day, steps=steps))
        await session.commit()


async def test_daily_briefing_email_carries_the_steps_up_to_yesterday(
    mock_insight: MagicMock,
) -> None:
    await _seed_steps(date(2026, 11, 13), 12431)
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(errors=[])),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", db_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing(today=date(2026, 11, 14))

    steps = mock_send.call_args.kwargs["steps"]
    assert steps.yesterday == 12431
    assert steps.days[-1].day == date(2026, 11, 13)


def _window(day: date) -> MagicMock:
    return MagicMock(
        day=day,
        failed_attempts=3,
        is_broken=True,
        budget_exhausted=True,
        last_detail="LLM failed",
        now=datetime(2026, 11, 14, 14, 0),
    )


async def test_no_briefing_email_carries_the_steps_when_the_llm_failed() -> None:
    await _seed_steps(date(2026, 11, 13), 12431)
    sent = MagicMock(return_value=True)

    with (
        patch("mycoach.scheduler.jobs.async_session", db_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_briefing_unavailable", sent),
    ):
        await _briefing_alert(_window(date(2026, 11, 14)))

    assert sent.call_args.kwargs["steps"].yesterday == 12431


async def test_a_failure_reading_steps_does_not_stop_the_email() -> None:
    sent = MagicMock(return_value=True)

    with (
        patch("mycoach.scheduler.jobs.load_steps_summary", AsyncMock(side_effect=OSError("db"))),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_briefing_unavailable", sent),
    ):
        await _briefing_alert(_window(date(2026, 11, 14)))

    assert sent.call_args.kwargs["steps"] is None


# --- The code-rendered Body block (#198) ----------------------------------------------


async def test_daily_briefing_email_carries_the_body_block(
    mock_session: AsyncMock, mock_insight: MagicMock
) -> None:
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)
    body = MagicMock(name="body summary")

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(errors=[])),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.get_body_summary", AsyncMock(return_value=body)),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing()

    assert mock_send.call_args.kwargs["body"] is body


async def test_a_body_lookup_failure_does_not_cost_the_briefing_email(
    mock_session: AsyncMock, mock_insight: MagicMock
) -> None:
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(errors=[])),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch(
            "mycoach.scheduler.jobs.get_body_summary", AsyncMock(side_effect=RuntimeError("db"))
        ),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing()

    assert mock_send.call_args.kwargs["body"] is None


# --- Life context banner and Open this week (#233) ------------------------------------


async def test_daily_briefing_email_carries_open_this_week_and_life_context(
    mock_session: AsyncMock, mock_insight: MagicMock
) -> None:
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)
    open_week = {"sessions": [], "days_remaining": 3}
    life = MagicMock(name="life context")
    load_life = AsyncMock(return_value=life)

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(errors=[])),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.get_open_this_week", AsyncMock(return_value=open_week)),
        patch("mycoach.scheduler.jobs._todays_health", AsyncMock(return_value={})),
        patch("mycoach.scheduler.jobs.get_health_trends", AsyncMock(return_value=[])),
        # recovery_signals reads the briefed day's snapshot (shared with the HRV line)
        patch("mycoach.scheduler.jobs.life_context_for", load_life),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing(date(2026, 11, 19))

    assert mock_send.call_args.kwargs["open_this_week"] == open_week
    assert mock_send.call_args.kwargs["life"] is life
    # Today only, read for the briefed day.
    assert load_life.call_args.args[2:] == (date(2026, 11, 19), date(2026, 11, 19))
    assert load_life.call_args.kwargs["today"] == date(2026, 11, 19)


async def test_a_life_context_or_open_week_failure_does_not_cost_the_briefing_email(
    mock_session: AsyncMock, mock_insight: MagicMock
) -> None:
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(errors=[])),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch(
            "mycoach.scheduler.jobs.get_open_this_week", AsyncMock(side_effect=RuntimeError("db"))
        ),
        patch(
            "mycoach.scheduler.jobs.life_context_for", AsyncMock(side_effect=RuntimeError("db"))
        ),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing(date(2026, 11, 19))

    assert mock_send.call_args.kwargs["open_this_week"] is None
    assert mock_send.call_args.kwargs["life"] is None


# --- The recap's cause quick replies (#237) -------------------------------------------


async def _recap_on(today: date) -> MagicMock:
    """Run the recap job on ``today`` against the test DB; returns the mocked sender."""
    insight = MagicMock()
    insight.content = json.dumps({"week_summary": "Quiet"})
    mock_engine = MagicMock()
    mock_engine.generate_weekly_recap = AsyncMock(return_value=insight)
    with (
        patch("mycoach.scheduler.jobs.local_today", return_value=today),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", db_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_weekly_recap") as mock_send,
    ):
        await _weekly_recap()
    return mock_send


async def _seed_health(*days: date) -> None:
    async with db_session() as session:
        session.add(User(id=USER_ID, email="a@example.com", name="A"))
        for day in days:
            session.add(DailyHealthSnapshot(user_id=USER_ID, snapshot_date=day, steps=8000))
        await session.commit()


async def _seed_sync_run(started_at: datetime, status: str, error: str | None = None) -> None:
    async with db_session() as session:
        session.add(
            JobRun(
                job_name="garmin_sync",
                started_at=started_at,
                duration_ms=10,
                status=status,
                error=error,
            )
        )
        await session.commit()


async def test_weekly_recap_email_counts_the_weeks_days_without_health_data() -> None:
    # Week of 9 Nov: Monday to Friday synced, the weekend not.
    await _seed_health(*(date(2026, 11, 9) + timedelta(days=i) for i in range(5)))

    mock_send = await _recap_on(date(2026, 11, 16))

    assert mock_send.call_args.kwargs["health_days_missing"] == 2
    assert mock_send.call_args.kwargs["today"] == date(2026, 11, 16)


async def test_weekly_recap_email_gets_an_unknown_health_gap_when_it_cannot_be_read() -> None:
    with patch("mycoach.scheduler.jobs.health.list_snapshots", AsyncMock(side_effect=OSError)):
        mock_send = await _recap_on(date(2026, 11, 16))

    assert mock_send.call_args.kwargs["health_days_missing"] is None


async def test_weekly_recap_email_knows_the_last_garmin_sync_failed_to_log_in() -> None:
    await _seed_sync_run(datetime(2026, 11, 14, 6), "success")
    await _seed_sync_run(
        datetime(2026, 11, 15, 6), "failed", "Garmin authentication failed: MFA required"
    )

    mock_send = await _recap_on(date(2026, 11, 16))

    assert mock_send.call_args.kwargs["garmin_auth_failed"] is True


async def test_a_garmin_login_failure_since_recovered_is_not_reported() -> None:
    await _seed_sync_run(
        datetime(2026, 11, 14, 6), "failed", "Garmin authentication failed: MFA required"
    )
    await _seed_sync_run(datetime(2026, 11, 15, 6), "success")

    mock_send = await _recap_on(date(2026, 11, 16))

    assert mock_send.call_args.kwargs["garmin_auth_failed"] is False


async def test_a_garmin_sync_that_failed_for_another_reason_is_not_a_login_failure() -> None:
    await _seed_sync_run(datetime(2026, 11, 15, 6), "failed", "Garmin API timed out")

    mock_send = await _recap_on(date(2026, 11, 16))

    assert mock_send.call_args.kwargs["garmin_auth_failed"] is False


# --- Post-workout week status and Still open this week (#235) -------------------------

THURSDAY = date(2026, 11, 19)
TUESDAY_SWIM = {
    "external_id": "swim-1",
    "title": "Pool swim",
    "sport": "swimming",
    "start_time": "2026-11-17 07:00:00",
}
POST_WORKOUT = {"performance_summary": "Steady"}
POST_WORKOUT_INSIGHT_ID = 77

PostWorkoutRun = Callable[[ExitStack, AsyncMock], Awaitable[None]]


async def _analysis_scan(stack: ExitStack, session: AsyncMock) -> None:
    """The catch-up scan's call site."""
    insight = MagicMock(id=POST_WORKOUT_INSIGHT_ID, content=json.dumps(POST_WORKOUT))
    engine = MagicMock()
    engine.generate_post_workout_analysis = AsyncMock(return_value=insight)
    result = MagicMock()
    result.scalars.return_value.all.return_value = []  # no insight exists yet
    session.execute = AsyncMock(return_value=result)
    stack.enter_context(patch("mycoach.scheduler.jobs.CoachingEngine", return_value=engine))
    stack.enter_context(
        patch(
            "mycoach.scheduler.jobs.health.list_settled_activities_since",
            AsyncMock(return_value=[TUESDAY_SWIM]),
        )
    )
    await _post_workout_analysis()


async def _settled_event(stack: ExitStack, session: AsyncMock) -> None:
    """The health-event call site."""
    stack.enter_context(patch("mycoach.scheduler.jobs.athlete_id_for", AsyncMock(return_value=1)))
    stack.enter_context(
        patch("mycoach.scheduler.jobs.health.get_activity", AsyncMock(return_value=TUESDAY_SWIM))
    )
    insight = MagicMock(id=POST_WORKOUT_INSIGHT_ID, content=json.dumps(POST_WORKOUT))
    await _notify_post_workout(insight, "swim-1")


async def _send_post_workout(
    run: PostWorkoutRun,
    session: AsyncMock,
    *,
    adherence: AsyncMock,
    life: AsyncMock,
    open_week: AsyncMock,
    planned: AsyncMock | None = None,
    history: AsyncMock | None = None,
) -> MagicMock:
    with ExitStack() as stack:
        for target, value in (
            ("async_session", MagicMock(return_value=session)),
            ("_get_user_email_pref", AsyncMock(return_value=True)),
            ("local_today", MagicMock(return_value=THURSDAY)),
            ("get_plan_adherence_for_week", adherence),
            ("life_context_for", life),
            ("get_open_this_week", open_week),
            ("find_matching_planned_session", planned or AsyncMock(return_value=None)),
            ("get_similar_activities", history or AsyncMock(return_value=[])),
        ):
            stack.enter_context(patch(f"mycoach.scheduler.jobs.{target}", value))
        mock_send = stack.enter_context(patch("mycoach.scheduler.jobs.send_post_workout"))
        await run(stack, session)
    return mock_send


@pytest.mark.parametrize("run", [_analysis_scan, _settled_event])
async def test_post_workout_email_gets_the_activitys_week_its_day_and_open_this_week(
    run: PostWorkoutRun, mock_session: AsyncMock
) -> None:
    """Both call sites: the activity's week Completion and its day's Life context,
    and what is still open as of the send."""
    adherence = {"plan_id": 1, "sessions": [], "extras": []}
    life = MagicMock(name="life context")
    open_week = {"sessions": [], "days_remaining": 4}
    load_adherence = AsyncMock(return_value=adherence)
    load_life = AsyncMock(return_value=life)
    load_open = AsyncMock(return_value=open_week)

    mock_send = await _send_post_workout(
        run, mock_session, adherence=load_adherence, life=load_life, open_week=load_open
    )

    mock_send.assert_called_once()
    assert mock_send.call_args.args[:2] == (POST_WORKOUT, "Pool swim")
    kwargs = mock_send.call_args.kwargs
    assert kwargs["activity"] == TUESDAY_SWIM
    assert kwargs["week"] == adherence
    assert kwargs["life"] is life
    assert kwargs["open_this_week"] == open_week
    # The activity's week (Mon 16 Nov), measured as of today.
    assert load_adherence.call_args.args[2] == date(2026, 11, 16)
    assert load_adherence.call_args.kwargs["today"] == THURSDAY
    # The activity's own day: Tuesday's ramp-back or trip.
    assert load_life.call_args.args[2:] == (date(2026, 11, 17), date(2026, 11, 17))
    assert load_life.call_args.kwargs["today"] == date(2026, 11, 17)
    assert load_open.call_args.args[2] == THURSDAY


@pytest.mark.parametrize("run", [_analysis_scan, _settled_event])
async def test_a_week_or_life_context_failure_does_not_cost_the_post_workout_email(
    run: PostWorkoutRun, mock_session: AsyncMock
) -> None:
    def failing() -> AsyncMock:
        return AsyncMock(side_effect=RuntimeError("db"))

    mock_send = await _send_post_workout(
        run, mock_session, adherence=failing(), life=failing(), open_week=failing()
    )

    kwargs = mock_send.call_args.kwargs
    assert kwargs["week"] is None
    assert kwargs["life"] is None
    assert kwargs["open_this_week"] is None


@pytest.mark.parametrize("run", [_analysis_scan, _settled_event])
async def test_post_workout_email_links_the_revision_it_renders(
    run: PostWorkoutRun, mock_session: AsyncMock
) -> None:
    """Both call sites pass the insight's id, so "Full analysis →" opens that revision."""
    mock_send = await _send_post_workout(
        run,
        mock_session,
        adherence=AsyncMock(return_value=None),
        life=AsyncMock(return_value=None),
        open_week=AsyncMock(return_value=None),
    )

    assert mock_send.call_args.kwargs["insight_id"] == POST_WORKOUT_INSIGHT_ID


@pytest.mark.parametrize("run", [_analysis_scan, _settled_event])
async def test_post_workout_email_gets_the_planned_session_and_the_similar_history(
    run: PostWorkoutRun, mock_session: AsyncMock
) -> None:
    """Both call sites feed the stat grid, zone target and trend (#239)."""
    planned = {"title": "Aerobic base swim", "intent": "aerobic_base"}
    history = [{"external_id": "swim-0", "sport": "swimming"}]
    load_planned = AsyncMock(return_value=planned)
    load_history = AsyncMock(return_value=history)

    mock_send = await _send_post_workout(
        run,
        mock_session,
        adherence=AsyncMock(return_value=None),
        life=AsyncMock(return_value=None),
        open_week=AsyncMock(return_value=None),
        planned=load_planned,
        history=load_history,
    )

    kwargs = mock_send.call_args.kwargs
    assert kwargs["planned"] == planned
    assert kwargs["history"] == history
    assert kwargs["sets"] is None, "a swim has no performed sets"
    assert load_planned.call_args.args[1] == TUESDAY_SWIM
    assert load_history.call_args.args[2:] == ("swimming", "swim-1")


@pytest.mark.parametrize("run", [_analysis_scan, _settled_event])
async def test_a_planned_session_or_history_failure_does_not_cost_the_post_workout_email(
    run: PostWorkoutRun, mock_session: AsyncMock
) -> None:
    def failing() -> AsyncMock:
        return AsyncMock(side_effect=RuntimeError("db"))

    mock_send = await _send_post_workout(
        run,
        mock_session,
        adherence=AsyncMock(return_value=None),
        life=AsyncMock(return_value=None),
        open_week=AsyncMock(return_value=None),
        planned=failing(),
        history=failing(),
    )

    kwargs = mock_send.call_args.kwargs
    assert kwargs["planned"] is None
    assert kwargs["history"] is None


async def test_a_gym_post_workout_email_gets_its_performed_sets_with_prescriptions(
    mock_session: AsyncMock,
) -> None:
    gym = {**TUESDAY_SWIM, "external_id": "gym-1", "sport": "gym", "title": "Squat Day"}
    sets = [{"exercise_title": "Squat", "set_index": 0, "prescribed_reps": 5}]
    load_sets = AsyncMock(return_value=(gym, sets))

    async def run(stack: ExitStack, session: AsyncMock) -> None:
        stack.enter_context(
            patch("mycoach.scheduler.jobs.athlete_id_for", AsyncMock(return_value=1))
        )
        stack.enter_context(
            patch("mycoach.scheduler.jobs.health.get_activity", AsyncMock(return_value=gym))
        )
        stack.enter_context(patch("mycoach.scheduler.jobs.get_activity_with_details", load_sets))
        await _notify_post_workout(MagicMock(content=json.dumps(POST_WORKOUT)), "gym-1")

    mock_send = await _send_post_workout(
        run,
        mock_session,
        adherence=AsyncMock(return_value=None),
        life=AsyncMock(return_value=None),
        open_week=AsyncMock(return_value=None),
    )

    assert mock_send.call_args.kwargs["sets"] == sets
    assert load_sets.call_args.args[1:] == ("gym-1", USER_ID)
