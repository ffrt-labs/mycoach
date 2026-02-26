"""Tests for scheduler job functions."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycoach.scheduler.jobs import (
    _daily_briefing,
    _garmin_sync,
    _post_workout_analysis,
    _weekly_plan,
    _weekly_recap,
)


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture
def mock_engine(mock_session: AsyncMock) -> MagicMock:
    engine = MagicMock()
    engine.generate_daily_briefing = AsyncMock()
    engine.generate_weekly_plan = AsyncMock()
    engine.generate_weekly_recap = AsyncMock()
    return engine


async def test_garmin_sync_success(mock_session: AsyncMock) -> None:
    """Garmin sync job should authenticate, fetch, merge, and commit."""
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=True)
    mock_result = MagicMock()
    mock_result.health_snapshots_created = 2
    mock_result.activities_created = 1
    mock_source.fetch_and_import = AsyncMock(return_value=mock_result)
    mock_merge = MagicMock(merged=0)

    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.merge_garmin_hevy", AsyncMock(return_value=mock_merge)),
    ):
        await _garmin_sync()

    mock_source.authenticate.assert_awaited_once()
    mock_source.fetch_and_import.assert_awaited_once()
    mock_session.commit.assert_awaited_once()


async def test_garmin_sync_auth_failure(mock_session: AsyncMock) -> None:
    """Garmin sync should log and return on auth failure."""
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=False)
    mock_source.fetch_and_import = AsyncMock()

    with patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source):
        await _garmin_sync()

    mock_source.authenticate.assert_awaited_once()
    mock_source.fetch_and_import.assert_not_awaited()


async def test_daily_briefing_success(mock_session: AsyncMock, mock_engine: MagicMock) -> None:
    """Daily briefing job should call the coaching engine."""
    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
    ):
        await _daily_briefing()

    mock_engine.generate_daily_briefing.assert_awaited_once()


async def test_daily_briefing_skips_duplicate(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """Daily briefing should skip gracefully when one already exists."""
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=ValueError("Daily briefing already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
    ):
        # Should not raise
        await _daily_briefing()



async def test_weekly_plan_calculates_next_monday(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """Weekly plan job should calculate the next Monday correctly."""
    # Mock date.today() to return a known Wednesday
    fake_today = date(2025, 1, 15)  # Wednesday
    expected_monday = date(2025, 1, 20)  # next Monday

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
    ):
        mock_date.today.return_value = fake_today
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        await _weekly_plan()

    mock_engine.generate_weekly_plan.assert_awaited_once_with(mock_session, 1, expected_monday)


async def test_weekly_plan_from_sunday(mock_session: AsyncMock, mock_engine: MagicMock) -> None:
    """When run on Sunday, next Monday should be tomorrow."""
    fake_today = date(2025, 1, 19)  # Sunday
    expected_monday = date(2025, 1, 20)  # tomorrow

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
    ):
        mock_date.today.return_value = fake_today
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        await _weekly_plan()

    mock_engine.generate_weekly_plan.assert_awaited_once_with(mock_session, 1, expected_monday)


async def test_weekly_recap_calculates_last_monday(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """Weekly recap job should calculate last Monday correctly."""
    fake_today = date(2025, 1, 20)  # Monday
    expected_last_monday = date(2025, 1, 13)  # previous Monday

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
    ):
        mock_date.today.return_value = fake_today
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        await _weekly_recap()

    mock_engine.generate_weekly_recap.assert_awaited_once_with(
        mock_session, 1, expected_last_monday
    )


async def test_weekly_recap_skips_duplicate(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """Weekly recap should skip gracefully when one already exists."""
    mock_engine.generate_weekly_recap = AsyncMock(
        side_effect=ValueError("Weekly recap already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
    ):
        mock_date.today.return_value = date(2025, 1, 20)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        # Should not raise
        await _weekly_recap()


async def test_post_workout_analysis_processes_new_activities(
    mock_session: AsyncMock, mock_engine: MagicMock,
) -> None:
    """Post-workout job should analyze activities without existing insights."""
    mock_engine.generate_post_workout_analysis = AsyncMock()

    # Simulate two activities returned by the query
    mock_activity_1 = MagicMock(id=10, title="Morning Swim")
    mock_activity_2 = MagicMock(id=11, title="Gym Session")

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_activity_1, mock_activity_2]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _post_workout_analysis()

    assert mock_engine.generate_post_workout_analysis.await_count == 2
    mock_engine.generate_post_workout_analysis.assert_any_await(mock_session, 1, 10)
    mock_engine.generate_post_workout_analysis.assert_any_await(mock_session, 1, 11)


async def test_post_workout_analysis_no_activities(
    mock_session: AsyncMock, mock_engine: MagicMock,
) -> None:
    """Post-workout job should skip when no new activities exist."""
    mock_engine.generate_post_workout_analysis = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
    ):
        await _post_workout_analysis()

    mock_engine.generate_post_workout_analysis.assert_not_awaited()


async def test_post_workout_analysis_skips_existing(
    mock_session: AsyncMock, mock_engine: MagicMock,
) -> None:
    """Post-workout job should skip activities that already have analysis (ValueError)."""
    mock_activity = MagicMock(id=10, title="Morning Swim")
    mock_engine.generate_post_workout_analysis = AsyncMock(
        side_effect=ValueError("Post-workout analysis already exists for activity 10")
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_activity]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        # Should not raise
        await _post_workout_analysis()


async def test_post_workout_analysis_sends_email(
    mock_session: AsyncMock, mock_engine: MagicMock,
) -> None:
    """Post-workout job should send email when user preference is enabled."""
    mock_insight = MagicMock()
    mock_insight.content = '{"performance_summary": "Great workout"}'
    mock_engine.generate_post_workout_analysis = AsyncMock(return_value=mock_insight)

    mock_activity = MagicMock(id=10, title="Morning Swim")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_activity]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_post_workout") as mock_send,
    ):
        await _post_workout_analysis()

    mock_send.assert_called_once_with({"performance_summary": "Great workout"}, "Morning Swim")
