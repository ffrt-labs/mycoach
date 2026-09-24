"""Tests for scheduler job functions."""

import logging
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycoach.coaching.exceptions import (
    InsufficientHealthData,
    NoAvailabilityConfigured,
    PipelineSkip,
)
from mycoach.config import get_settings
from mycoach.scheduler.jobs import (
    _briefing_alert,
    _daily_briefing,
    _daily_briefing_poll,
    _garmin_sync,
    _post_workout_analysis,
    _record_run,
    _weekly_plan,
    _weekly_recap,
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
    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
    ):
        await _garmin_sync()

    mock_source.authenticate.assert_awaited_once()
    mock_source.fetch_and_import.assert_awaited_once()
    mock_session.commit.assert_awaited_once()


async def test_garmin_sync_looks_back_seven_days_by_default(
    mock_session: AsyncMock,
) -> None:
    """A late Garmin upload must still be recoverable days later."""
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=True)
    mock_result = MagicMock()
    mock_result.health_snapshots_created = 0
    mock_result.activities_created = 0
    mock_source.fetch_and_import = AsyncMock(return_value=mock_result)
    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
    ):
        await _garmin_sync()

    since = mock_source.fetch_and_import.await_args.kwargs["since"]
    assert (datetime.utcnow().date() - since.date()).days == 7


async def test_garmin_sync_returns_the_import_result(mock_session: AsyncMock) -> None:
    """The briefing job needs the result to decide whether to skip."""
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=True)
    mock_result = MagicMock()
    mock_result.health_snapshots_created = 1
    mock_result.activities_created = 0
    mock_source.fetch_and_import = AsyncMock(return_value=mock_result)
    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
    ):
        result = await _garmin_sync()

    assert result is mock_result


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


def test_garmin_sync_job_logs_auth_failure(
    mock_session: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """The Garmin job wrapper surfaces an auth failure at error level, not silently."""
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=False)

    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        caplog.at_level(logging.INFO),
    ):
        job_garmin_sync()  # must not raise

    assert any(
        r.levelno == logging.ERROR and "garmin_sync failed" in r.message
        for r in caplog.records
    )


async def test_daily_briefing_success(mock_session: AsyncMock, mock_engine: MagicMock) -> None:
    """Daily briefing job should call the coaching engine."""
    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[], errors=None)),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
    ):
        await _daily_briefing()

    mock_engine.generate_daily_briefing.assert_awaited_once()


async def test_daily_briefing_syncs_before_generating(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """The briefing must fetch fresh data, not read a snapshot hours old."""
    calls: list[str] = []

    async def fake_sync(days: int | None = None) -> MagicMock:
        calls.append("sync")
        return MagicMock(empty_health_days=[], errors=None)

    async def fake_generate(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append("generate")
        return MagicMock(content='{"readiness_verdict": "go_hard"}')

    mock_engine.generate_daily_briefing = AsyncMock(side_effect=fake_generate)

    with (
        patch("mycoach.scheduler.jobs._garmin_sync", fake_sync),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _daily_briefing()

    assert calls == ["sync", "generate"]


async def test_daily_briefing_propagates_the_engine_guard(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """No data is a skip with a reason — never a briefing invented from nothing.

    The rule itself lives in the engine now, so what this pins is that the job
    does not paper over it: an ``InsufficientHealthData`` reaches ``_record_run``
    intact and is filed as a skip.
    """
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=InsufficientHealthData("no sleep, HRV, or Body Battery")
    )

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[], errors=None)),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        pytest.raises(InsufficientHealthData, match="no sleep, HRV, or Body Battery"),
    ):
        await _daily_briefing()


async def test_daily_briefing_skip_carries_the_sync_detail(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """The sync already knew why the day was empty; the skip must not drop it.

    Distinguishing "Garmin returned nulls" from "every call raised" cost four
    ad-hoc probe scripts during the diagnosis. Both facts are computed by
    ``fetch_and_import``; this is where they become durable.
    """
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=InsufficientHealthData("no sleep, HRV, or Body Battery")
    )
    sync_result = MagicMock(
        empty_health_days=[],
        errors=["Garmin API calls failed for 2026-08-25: get_sleep_data"],
    )

    with (
        patch("mycoach.scheduler.jobs._garmin_sync", AsyncMock(return_value=sync_result)),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        pytest.raises(InsufficientHealthData) as caught,
    ):
        await _daily_briefing()

    assert "get_sleep_data" in str(caught.value)


async def test_daily_briefing_skip_carries_a_sync_that_raised(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """A sync that blew up outright is detail too, not just a log line."""
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=InsufficientHealthData("no sleep, HRV, or Body Battery")
    )

    async def failing_sync(days: int | None = None) -> MagicMock:
        raise RuntimeError("Garmin authentication failed")

    with (
        patch("mycoach.scheduler.jobs._garmin_sync", failing_sync),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        pytest.raises(InsufficientHealthData) as caught,
    ):
        await _daily_briefing()

    assert "Garmin authentication failed" in str(caught.value)


async def test_daily_briefing_generates_for_the_day_it_was_given(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """A window that straddles midnight must not silently change days.

    Every attempt in one retry window targets the day the poll decided on.
    """
    mock_engine.generate_daily_briefing = AsyncMock(
        return_value=MagicMock(content='{"readiness_verdict": "moderate"}')
    )
    day = date(2026, 8, 25)

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[], errors=None)),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _daily_briefing(day)

    assert mock_engine.generate_daily_briefing.await_args.kwargs["today"] == day


async def test_daily_briefing_proceeds_when_sync_fails(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """A sync failure must not block a briefing when the DB already has data."""

    async def failing_sync(days: int | None = None) -> MagicMock:
        raise RuntimeError("Garmin authentication failed")

    mock_engine.generate_daily_briefing = AsyncMock(
        return_value=MagicMock(content='{"readiness_verdict": "moderate"}')
    )

    with (
        patch("mycoach.scheduler.jobs._garmin_sync", failing_sync),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _daily_briefing()

    mock_engine.generate_daily_briefing.assert_awaited_once()


async def test_daily_briefing_sync_failure_logged_at_error_level(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A sync failure inside the briefing must not vanish into a WARNING nobody reads.

    R7 says the briefing proceeds regardless — that is pinned by
    ``test_daily_briefing_proceeds_when_sync_fails`` above and must not change here.
    This only pins that the failure is loud enough that a 'success' job_runs row
    is not the only trace of it.
    """

    async def failing_sync(days: int | None = None) -> MagicMock:
        raise RuntimeError("Garmin authentication failed")

    mock_engine.generate_daily_briefing = AsyncMock(
        return_value=MagicMock(content='{"readiness_verdict": "moderate"}')
    )

    with (
        patch("mycoach.scheduler.jobs._garmin_sync", failing_sync),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
        caplog.at_level(logging.INFO),
    ):
        await _daily_briefing()

    assert any(
        r.levelno == logging.ERROR and "pre-briefing Garmin sync failed" in r.message
        for r in caplog.records
    )


async def test_daily_briefing_raises_skip_on_duplicate(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """The daily briefing coroutine propagates a PipelineSkip when one exists."""
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=PipelineSkip("Daily briefing already exists")
    )

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[], errors=None)),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        pytest.raises(PipelineSkip),
    ):
        await _daily_briefing()


async def test_daily_briefing_job_logs_skip(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A skip is logged at info level and swallowed by the job wrapper."""
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=PipelineSkip("Daily briefing already exists")
    )

    with (
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[], errors=None)),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        caplog.at_level(logging.INFO),
    ):
        # The poll's own gating is exercised in test_briefing_window.py; here
        # only the recording wrapper the poll delegates to is under test.
        await _record_run("daily_briefing", _daily_briefing())

    skip_logs = [
        r for r in caplog.records if "daily_briefing skipped" in r.message
    ]
    assert skip_logs and all(r.levelno == logging.INFO for r in skip_logs)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_daily_briefing_job_logs_malformed_response_as_failure(
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
        patch(
            "mycoach.scheduler.jobs._garmin_sync",
            AsyncMock(return_value=MagicMock(empty_health_days=[], errors=None)),
        ),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        caplog.at_level(logging.INFO),
    ):
        # The poll's own gating is exercised in test_briefing_window.py; here
        # only the recording wrapper the poll delegates to is under test.
        await _record_run("daily_briefing", _daily_briefing())

    assert any(
        r.levelno == logging.ERROR and "daily_briefing failed" in r.message
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


async def test_weekly_plan_sends_no_availability_email_on_no_availability_configured(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """NoAvailabilityConfigured triggers the no-availability email, then still propagates."""
    mock_engine.generate_weekly_plan = AsyncMock(
        side_effect=NoAvailabilityConfigured("No availability configured for week of 2025-01-20")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_no_availability", return_value=True) as mock_send,
    ):
        mock_date.today.return_value = date(2025, 1, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        with pytest.raises(NoAvailabilityConfigured):
            await _weekly_plan()

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["week_start"] == "2025-01-20"


async def test_weekly_plan_no_availability_email_respects_preference(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """The no-availability email is not sent when the user has the preference off."""
    mock_engine.generate_weekly_plan = AsyncMock(
        side_effect=NoAvailabilityConfigured("No availability configured for week of 2025-01-20")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
        patch("mycoach.scheduler.jobs.send_no_availability", return_value=True) as mock_send,
    ):
        mock_date.today.return_value = date(2025, 1, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        with pytest.raises(NoAvailabilityConfigured):
            await _weekly_plan()

    mock_send.assert_not_called()


async def test_weekly_plan_duplicate_skip_does_not_send_no_availability_email(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """A plain duplicate-plan PipelineSkip must not trigger the no-availability email."""
    mock_engine.generate_weekly_plan = AsyncMock(
        side_effect=PipelineSkip("Active plan already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_no_availability", return_value=True) as mock_send,
    ):
        mock_date.today.return_value = date(2025, 1, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        with pytest.raises(PipelineSkip):
            await _weekly_plan()

    mock_send.assert_not_called()


def test_weekly_plan_job_logs_skip_for_no_availability_configured(
    mock_session: AsyncMock, mock_engine: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """The run is still recorded as skipped when NoAvailabilityConfigured is raised."""
    mock_engine.generate_weekly_plan = AsyncMock(
        side_effect=NoAvailabilityConfigured("No availability configured for week of 2025-01-20")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs.date") as mock_date,
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
        caplog.at_level(logging.INFO),
    ):
        mock_date.today.return_value = date(2025, 1, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        job_weekly_plan()  # must not raise

    skip_logs = [r for r in caplog.records if "weekly_plan skipped" in r.message]
    assert skip_logs and all(r.levelno == logging.INFO for r in skip_logs)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


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

    skip_logs = [r for r in caplog.records if "weekly_plan skipped" in r.message]
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
        r.levelno == logging.ERROR and "weekly_plan failed" in r.message
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
        r.levelno == logging.ERROR and "weekly_recap failed" in r.message
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
    """A per-activity failure is logged, the batch continues, and the run still fails.

    Continuing the batch is resilience, not absolution: the surviving activity is
    analysed, then the coroutine raises so the run is recorded as a failure.
    """
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
        pytest.raises(RuntimeError, match="1 of 2 activities failed"),
    ):
        await _post_workout_analysis()

    assert mock_engine.generate_post_workout_analysis.await_count == 2
    assert any(
        r.levelno == logging.ERROR and "post-workout analysis failed for activity 10" in r.message
        for r in caplog.records
    )


async def test_post_workout_analysis_all_skipped_raises_skip(
    mock_session: AsyncMock, mock_engine: MagicMock,
) -> None:
    """A batch where every activity already had an insight is a skip, not a success."""
    mock_engine.generate_post_workout_analysis = AsyncMock(
        side_effect=[
            PipelineSkip("Post-workout analysis already exists for activity 10"),
            PipelineSkip("Post-workout analysis already exists for activity 11"),
        ]
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        MagicMock(id=10, title="Morning Swim"),
        MagicMock(id=11, title="Gym Session"),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
        pytest.raises(PipelineSkip, match="all 2 activities already analysed"),
    ):
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


class TestDailyBriefingPoll:
    """The tick that decides whether today's window has anything left to do.

    Each test pins one branch by stubbing ``load_briefing_window``; what the
    window itself concludes from ``job_runs`` is pinned in
    ``test_briefing_window.py``.
    """

    @staticmethod
    def _window(**kwargs: object) -> MagicMock:
        window = MagicMock(
            should_generate=False,
            should_alert=False,
            attempts=0,
            day=date(2026, 8, 25),
        )
        window.configure_mock(**kwargs)
        return window

    async def test_a_quiet_tick_records_nothing(self, mock_session: AsyncMock) -> None:
        """~44 ticks a day; a row per tick would drown the table it reads from."""
        recorded = AsyncMock()
        with (
            patch(
                "mycoach.scheduler.jobs.load_briefing_window",
                AsyncMock(return_value=self._window()),
            ),
            patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
            patch("mycoach.scheduler.jobs._record_run", recorded),
        ):
            await _daily_briefing_poll()

        recorded.assert_not_awaited()

    async def test_generating_tick_records_a_daily_briefing_run(
        self, mock_session: AsyncMock
    ) -> None:
        recorded = AsyncMock()
        with (
            patch(
                "mycoach.scheduler.jobs.load_briefing_window",
                AsyncMock(return_value=self._window(should_generate=True)),
            ),
            patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
            patch("mycoach.scheduler.jobs._record_run", recorded),
        ):
            await _daily_briefing_poll()

        assert [c.args[0] for c in recorded.await_args_list] == ["daily_briefing"]

    async def test_generates_for_the_windows_day(self, mock_session: AsyncMock) -> None:
        """The poll picks the day, so every attempt in a window targets the same one."""
        seen: list[date] = []

        async def fake_briefing(day: date | None = None) -> None:
            seen.append(day)  # type: ignore[arg-type]

        async def run_body(job_name: str, coro) -> None:  # type: ignore[no-untyped-def]
            await coro

        with (
            patch(
                "mycoach.scheduler.jobs.load_briefing_window",
                AsyncMock(return_value=self._window(should_generate=True)),
            ),
            patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
            patch("mycoach.scheduler.jobs._record_run", run_body),
            patch("mycoach.scheduler.jobs._daily_briefing", fake_briefing),
        ):
            await _daily_briefing_poll()

        assert seen == [date(2026, 8, 25)]

    async def test_rereads_the_window_after_an_attempt(
        self, mock_session: AsyncMock
    ) -> None:
        """The attempt just made is the one that decides whether to alert."""
        before = self._window(should_generate=True, should_alert=True)
        after = self._window(should_generate=False, should_alert=False)
        load = AsyncMock(side_effect=[before, after])

        recorded = AsyncMock()
        with (
            patch("mycoach.scheduler.jobs.load_briefing_window", load),
            patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
            patch("mycoach.scheduler.jobs._record_run", recorded),
        ):
            await _daily_briefing_poll()

        # The stale `should_alert=True` must not survive an attempt that succeeded.
        assert [c.args[0] for c in recorded.await_args_list] == ["daily_briefing"]

    async def test_alerts_and_generates_on_the_same_tick(
        self, mock_session: AsyncMock
    ) -> None:
        """Hitting the grace period is no reason to skip that tick's attempt."""
        still_stuck = self._window(should_generate=True, should_alert=True)
        load = AsyncMock(side_effect=[still_stuck, still_stuck])

        recorded = AsyncMock()
        with (
            patch("mycoach.scheduler.jobs.load_briefing_window", load),
            patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
            patch("mycoach.scheduler.jobs._record_run", recorded),
        ):
            await _daily_briefing_poll()

        assert [c.args[0] for c in recorded.await_args_list] == [
            "daily_briefing",
            "daily_briefing_alert",
        ]

    async def test_alerts_without_generating_past_the_cutoff(
        self, mock_session: AsyncMock
    ) -> None:
        """Between 14:00 and 20:00 the loop no longer tries but still reports."""
        recorded = AsyncMock()
        with (
            patch(
                "mycoach.scheduler.jobs.load_briefing_window",
                AsyncMock(return_value=self._window(should_alert=True)),
            ),
            patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
            patch("mycoach.scheduler.jobs._record_run", recorded),
        ):
            await _daily_briefing_poll()

        assert [c.args[0] for c in recorded.await_args_list] == ["daily_briefing_alert"]


class TestBriefingAlert:
    """What the alert email is told, which is the difference between it helping and not."""

    @staticmethod
    def _window(is_broken: bool = False, **kwargs: object) -> MagicMock:
        window = MagicMock(
            day=date(2026, 8, 25),
            attempts=4,
            failed_attempts=0,
            is_broken=is_broken,
            budget_exhausted=False,
            last_detail="no usable Garmin health data",
            now=datetime(2026, 8, 25, 10, 30),
        )
        window.configure_mock(**kwargs)
        return window

    async def test_names_the_real_gap_from_the_device_upload_time(self) -> None:
        """'No briefing today' is not actionable; 'your watch hasn't uploaded since' is."""
        sent = MagicMock(return_value=True)
        upload = MagicMock(uploaded_at=datetime(2026, 8, 19, 5, 20), device_name="Forerunner 255")

        with (
            patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
            patch("mycoach.scheduler.jobs._last_device_upload", AsyncMock(return_value=upload)),
            patch("mycoach.scheduler.jobs.send_briefing_unavailable", sent),
        ):
            await _briefing_alert(self._window())

        kwargs = sent.call_args.kwargs
        assert kwargs["last_upload"] == datetime(2026, 8, 19, 5, 20)
        assert kwargs["device_name"] == "Forerunner 255"
        assert kwargs["is_broken"] is False

    async def test_does_not_blame_the_watch_for_our_own_failure(self) -> None:
        """A 'check your Bluetooth' email for a Pydantic error is worse than none."""
        sent = MagicMock(return_value=True)
        lookup = AsyncMock()

        with (
            patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
            patch("mycoach.scheduler.jobs._last_device_upload", lookup),
            patch("mycoach.scheduler.jobs.send_briefing_unavailable", sent),
        ):
            await _briefing_alert(self._window(is_broken=True))

        lookup.assert_not_awaited()
        assert sent.call_args.kwargs["is_broken"] is True

    async def test_still_alerts_when_garmin_will_not_say_when_it_last_synced(self) -> None:
        """Losing the decoration must not lose the alert."""
        sent = MagicMock(return_value=True)

        with (
            patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
            patch("mycoach.scheduler.jobs._last_device_upload", AsyncMock(return_value=None)),
            patch("mycoach.scheduler.jobs.send_briefing_unavailable", sent),
        ):
            await _briefing_alert(self._window())

        assert sent.call_args.kwargs["last_upload"] is None

    async def test_reports_whether_more_attempts_are_coming(self) -> None:
        sent = MagicMock(return_value=True)

        with (
            patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
            patch("mycoach.scheduler.jobs._last_device_upload", AsyncMock(return_value=None)),
            patch("mycoach.scheduler.jobs.send_briefing_unavailable", sent),
        ):
            await _briefing_alert(self._window(now=datetime(2026, 8, 25, 15, 0)))

        assert sent.call_args.kwargs["can_still_retry"] is False

    async def test_skips_when_the_user_has_briefing_email_switched_off(self) -> None:
        """Recorded as a skip, not a success — a skip does not lock out tomorrow."""
        sent = MagicMock(return_value=True)

        with (
            patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
            patch("mycoach.scheduler.jobs.send_briefing_unavailable", sent),
            pytest.raises(PipelineSkip, match="switched off"),
        ):
            await _briefing_alert(self._window())

        sent.assert_not_called()


async def test_garmin_sync_logs_the_failure_detail_it_already_computed(
    mock_session: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """``_safe_call`` collects every failed call and ``fetch_and_import`` folds them in.

    The job then logged three counts and dropped ``errors`` on the floor —
    nothing read it. Re-deriving "Garmin returned nulls" vs "every call raised"
    afterwards cost four ad-hoc probe scripts.
    """
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=True)
    mock_result = MagicMock(
        health_snapshots_created=0,
        activities_created=0,
        errors=[
            "Garmin API calls failed for 2026-08-25: get_sleep_data, get_hrv_data",
            "1 day(s) synced with no usable Garmin health data: 2026-08-25",
        ],
    )
    mock_source.fetch_and_import = AsyncMock(return_value=mock_result)

    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        caplog.at_level(logging.INFO),
    ):
        await _garmin_sync()

    logged = " ".join(r.message for r in caplog.records if r.levelno == logging.WARNING)
    assert "get_sleep_data" in logged
    assert "no usable Garmin health data" in logged


async def test_garmin_sync_stays_quiet_when_nothing_failed(
    mock_session: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A clean sync must not manufacture warnings out of an empty error list."""
    mock_source = MagicMock()
    mock_source.authenticate = AsyncMock(return_value=True)
    mock_source.fetch_and_import = AsyncMock(
        return_value=MagicMock(health_snapshots_created=7, activities_created=1, errors=None)
    )

    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=mock_source),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        caplog.at_level(logging.INFO),
    ):
        await _garmin_sync()

    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


async def test_briefing_sync_uses_the_narrow_per_tick_lookback(
    mock_session: AsyncMock, mock_engine: MagicMock
) -> None:
    """A 7-day window is ~80 Garmin requests; forty ticks a day would be ~1,400.

    Throttling the account the whole retry loop depends on would defeat the
    point. Recovering older late uploads stays the 06:00 sync's job.
    """
    sync = AsyncMock(return_value=MagicMock(empty_health_days=[], errors=None))
    mock_engine.generate_daily_briefing = AsyncMock(
        return_value=MagicMock(content='{"readiness_verdict": "moderate"}')
    )

    with (
        patch("mycoach.scheduler.jobs._garmin_sync", sync),
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", return_value=mock_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _daily_briefing()

    days = sync.await_args.kwargs["days"]
    assert days == get_settings().scheduler_briefing_sync_lookback_days
    assert days < get_settings().scheduler_sync_lookback_days
