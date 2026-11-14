"""Tests for email send triggers in scheduler jobs."""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycoach.scheduler.jobs import _daily_briefing, _weekly_recap


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
