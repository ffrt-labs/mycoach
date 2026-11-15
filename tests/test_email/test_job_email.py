"""Tests for email send triggers in scheduler jobs."""

import json
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.user import User
from mycoach.scheduler.jobs import USER_ID, _briefing_alert, _daily_briefing, _weekly_recap
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
        patch("mycoach.scheduler.jobs._health_for_email", lookup),
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
