"""Tests for scheduler job functions."""

import logging
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycoach.coaching.exceptions import PipelineSkip
from mycoach.scheduler.jobs import (
    _daily_briefing,
    _garmin_sync,
    _post_workout_analysis,
    _weekly_plan,
    _weekly_recap,
    job_daily_briefing,
    job_garmin_sync,
    job_weekly_plan,
    job_weekly_recap,
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


async def test_garmin_sync_auth_failure_raises(mock_session: AsyncMock) -> None:
    """Garmin auth failure should raise rather than return silently."""
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=False)
    mock_source.fetch_and_import = AsyncMock()

    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        pytest.raises(RuntimeError, match="authentication failed"),
    ):
        await _garmin_sync()

    mock_source.authenticate.assert_awaited_once()
    mock_source.fetch_and_import.assert_not_awaited()


def test_garmin_sync_job_logs_auth_failure(caplog: pytest.LogCaptureFixture) -> None:
    """The Garmin job wrapper surfaces an auth failure at error level, not silently."""
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=False)

    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        caplog.at_level(logging.INFO),
    ):
        job_garmin_sync()  # must not raise

    assert any(
        r.levelno == logging.ERROR and "Garmin sync failed" in r.message
        for r in caplog.records
    )


async def test_daily_briefing_success(mock_session: AsyncMock, mock_engine: MagicMock) -> None:
    """Daily briefing job should call the coaching engine."""
    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
    ):
        await _daily_briefing()

    mock_engine.generate_daily_briefing.assert_awaited_once()


async def test_daily_briefing_raises_skip_on_duplicate(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """The daily briefing coroutine propagates a PipelineSkip when one exists."""
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=PipelineSkip("Daily briefing already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        pytest.raises(PipelineSkip),
    ):
        await _daily_briefing()


def test_daily_briefing_job_logs_skip(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A skip is logged at info level and swallowed by the job wrapper."""
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=PipelineSkip("Daily briefing already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        caplog.at_level(logging.INFO),
    ):
        job_daily_briefing()  # must not raise

    skip_logs = [
        r for r in caplog.records if "daily briefing skipped" in r.message
    ]
    assert skip_logs and all(r.levelno == logging.INFO for r in skip_logs)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_daily_briefing_job_logs_malformed_response_as_failure(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed stored response is a failure (error), not a routine skip.

    The insight content fails to JSON-decode when building the email; that
    JSONDecodeError is a ValueError subtype and must not be swallowed as a skip.
    """
    mock_insight = MagicMock()
    mock_insight.content = "not valid json{"
    mock_engine.generate_daily_briefing = AsyncMock(return_value=mock_insight)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        caplog.at_level(logging.INFO),
    ):
        job_daily_briefing()  # must not raise

    assert any(
        r.levelno == logging.ERROR and "daily briefing failed" in r.message
        for r in caplog.records
    )
    assert not any("skipped" in r.message for r in caplog.records)



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


async def test_weekly_plan_raises_skip_on_duplicate(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """The weekly plan coroutine propagates a PipelineSkip when one exists."""
    mock_engine.generate_weekly_plan = AsyncMock(
        side_effect=PipelineSkip("Active plan already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
    ):
        mock_date.today.return_value = date(2025, 1, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        with pytest.raises(PipelineSkip):
            await _weekly_plan()


def test_weekly_plan_job_logs_skip(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A skip is logged at info level and swallowed by the job wrapper."""
    mock_engine.generate_weekly_plan = AsyncMock(
        side_effect=PipelineSkip("Active plan already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
        caplog.at_level(logging.INFO),
    ):
        mock_date.today.return_value = date(2025, 1, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        job_weekly_plan()  # must not raise

    skip_logs = [r for r in caplog.records if "weekly plan skipped" in r.message]
    assert skip_logs and all(r.levelno == logging.INFO for r in skip_logs)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_weekly_plan_job_logs_failure(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-skip exception is logged at error level by the job wrapper."""
    mock_engine.generate_weekly_plan = AsyncMock(
        side_effect=RuntimeError("Failed to generate weekly plan: bad JSON")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
        caplog.at_level(logging.INFO),
    ):
        mock_date.today.return_value = date(2025, 1, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        job_weekly_plan()  # must not raise

    assert any(
        r.levelno == logging.ERROR and "weekly plan failed" in r.message
        for r in caplog.records
    )
    assert not any("skipped" in r.message for r in caplog.records)


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


async def test_weekly_recap_raises_skip_on_duplicate(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """The weekly recap coroutine propagates a PipelineSkip when one exists."""
    mock_engine.generate_weekly_recap = AsyncMock(
        side_effect=PipelineSkip("Weekly recap already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
    ):
        mock_date.today.return_value = date(2025, 1, 20)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        with pytest.raises(PipelineSkip):
            await _weekly_recap()


def test_weekly_recap_job_logs_failure(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-skip exception is logged at error level by the job wrapper."""
    mock_engine.generate_weekly_recap = AsyncMock(
        side_effect=RuntimeError("Failed to generate weekly recap: bad JSON")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
        caplog.at_level(logging.INFO),
    ):
        mock_date.today.return_value = date(2025, 1, 20)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        job_weekly_recap()  # must not raise

    assert any(
        r.levelno == logging.ERROR and "weekly recap failed" in r.message
        for r in caplog.records
    )
    assert not any("skipped" in r.message for r in caplog.records)


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


async def test_post_workout_analysis_no_activities_raises_skip(
    mock_session: AsyncMock, mock_engine: MagicMock,
) -> None:
    """Post-workout job raises PipelineSkip when there are no new activities."""
    mock_engine.generate_post_workout_analysis = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        pytest.raises(PipelineSkip, match="no new activities"),
    ):
        await _post_workout_analysis()

    mock_engine.generate_post_workout_analysis.assert_not_awaited()


async def test_post_workout_analysis_skips_existing_per_activity(
    mock_session: AsyncMock, mock_engine: MagicMock,
) -> None:
    """A per-activity PipelineSkip is caught in the loop so the batch continues."""
    mock_activity_1 = MagicMock(id=10, title="Morning Swim")
    mock_activity_2 = MagicMock(id=11, title="Gym Session")
    mock_engine.generate_post_workout_analysis = AsyncMock(
        side_effect=[
            PipelineSkip("Post-workout analysis already exists for activity 10"),
            MagicMock(content='{"performance_summary": "ok"}'),
        ]
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_activity_1, mock_activity_2]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        # A skip on activity 10 must not stop activity 11 being analysed.
        await _post_workout_analysis()

    assert mock_engine.generate_post_workout_analysis.await_count == 2


async def test_post_workout_analysis_failure_does_not_abort_batch(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A per-activity failure is logged at error level and the batch continues."""
    mock_activity_1 = MagicMock(id=10, title="Morning Swim")
    mock_activity_2 = MagicMock(id=11, title="Gym Session")
    mock_engine.generate_post_workout_analysis = AsyncMock(
        side_effect=[
            RuntimeError("Failed to generate post-workout analysis: bad JSON"),
            MagicMock(content='{"performance_summary": "ok"}'),
        ]
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_activity_1, mock_activity_2]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
        caplog.at_level(logging.INFO),
    ):
        await _post_workout_analysis()

    assert mock_engine.generate_post_workout_analysis.await_count == 2
    assert any(
        r.levelno == logging.ERROR and "post-workout analysis failed for activity 10" in r.message
        for r in caplog.records
    )


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
