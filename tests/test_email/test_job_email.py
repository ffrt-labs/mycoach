"""Tests for email send triggers in scheduler jobs."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycoach.scheduler.jobs import _daily_briefing, _sleep_coaching, _weekly_recap


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
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing()
        mock_send.assert_called_once()


async def test_daily_briefing_skips_email_when_disabled(
    mock_session: AsyncMock, mock_insight: MagicMock
) -> None:
    """Daily briefing job does NOT send email when preference is disabled."""
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
        patch("mycoach.scheduler.jobs.send_daily_briefing") as mock_send,
    ):
        await _daily_briefing()
        mock_send.assert_not_called()


async def test_sleep_coaching_sends_email(
    mock_session: AsyncMock,
) -> None:
    """Sleep coaching job sends email when preference is enabled."""
    insight = MagicMock()
    insight.content = json.dumps({"sleep_quality_summary": "Good"})
    mock_engine = MagicMock()
    mock_engine.generate_sleep_coaching = AsyncMock(return_value=insight)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_sleep_coaching") as mock_send,
    ):
        await _sleep_coaching()
        mock_send.assert_called_once()


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
