"""Tests for durable scheduled-job run recording.

Drive a job body through the recording helper and assert on the persisted
``JobRun`` rows, following the scheduler job-test prior art in ``test_jobs.py``.
"""

import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from mycoach.coaching.exceptions import PipelineSkip
from mycoach.models.activity import Activity
from mycoach.models.job_run import JobRun
from mycoach.scheduler.jobs import (
    USER_ID,
    _daily_briefing,
    _garmin_sync,
    _post_workout_analysis,
    _record_run,
    _weekly_plan,
    _weekly_recap,
)
from tests.conftest import test_session


async def _job_runs() -> list[JobRun]:
    async with test_session() as session:
        result = await session.execute(select(JobRun).order_by(JobRun.id))
        return list(result.scalars().all())


async def test_records_success() -> None:
    """A body that returns normally records a single success row."""

    async def body() -> None:
        return None

    with patch("mycoach.scheduler.jobs.async_session", test_session):
        await _record_run("daily_briefing", body())

    runs = await _job_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.job_name == "daily_briefing"
    assert run.status == "success"
    assert run.error is None
    assert run.duration_ms >= 0
    assert run.started_at is not None


async def test_records_skip() -> None:
    """A PipelineSkip is recorded as a skipped run with the skip detail."""

    async def body() -> None:
        raise PipelineSkip("Daily briefing already exists")

    with patch("mycoach.scheduler.jobs.async_session", test_session):
        await _record_run("daily_briefing", body())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    # A skip is not an error, so no detail is persisted in the error column;
    # the skip reason lives only in the structured log line.
    assert runs[0].error is None


async def test_records_failure_with_error_detail() -> None:
    """Any non-skip exception is recorded as failed with the error detail."""

    async def body() -> None:
        raise RuntimeError("LLM call blew up")

    with patch("mycoach.scheduler.jobs.async_session", test_session):
        await _record_run("daily_briefing", body())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error == "LLM call blew up"


async def test_failure_is_not_re_raised() -> None:
    """The helper swallows the failure so the scheduler thread never crashes."""

    async def body() -> None:
        raise RuntimeError("boom")

    with patch("mycoach.scheduler.jobs.async_session", test_session):
        await _record_run("daily_briefing", body())  # must not raise

    assert (await _job_runs())[0].status == "failed"


async def test_log_line_carries_job_fields(caplog: pytest.LogCaptureFixture) -> None:
    """The structured log line carries the same facts as the persisted row."""

    async def body() -> None:
        raise RuntimeError("kaboom")

    with (
        patch("mycoach.scheduler.jobs.async_session", test_session),
        caplog.at_level(logging.INFO),
    ):
        await _record_run("daily_briefing", body())

    failed = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failed
    record = failed[0]
    assert record.job_name == "daily_briefing"
    assert record.job_status == "failed"
    assert record.job_error == "kaboom"
    assert record.duration_ms is not None


async def test_daily_briefing_body_records_run() -> None:
    """Driving the real daily-briefing body through the helper records a run."""
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(return_value=MagicMock())

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _record_run("daily_briefing", _daily_briefing())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].job_name == "daily_briefing"
    assert runs[0].status == "success"
    mock_engine.generate_daily_briefing.assert_awaited_once()


async def test_daily_briefing_body_records_skip() -> None:
    """A PipelineSkip out of the real body records a skipped run."""
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=PipelineSkip("Daily briefing already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
    ):
        await _record_run("daily_briefing", _daily_briefing())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "skipped"


async def test_daily_briefing_body_records_failure() -> None:
    """A real failure out of the body records a failed run with the detail."""
    mock_engine = MagicMock()
    mock_engine.generate_daily_briefing = AsyncMock(
        side_effect=RuntimeError("Gemini call failed")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
    ):
        await _record_run("daily_briefing", _daily_briefing())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error == "Gemini call failed"


def _mock_garmin_source() -> MagicMock:
    source = MagicMock()
    source.authenticate = AsyncMock(return_value=True)
    source.fetch_and_import = AsyncMock(
        return_value=MagicMock(health_snapshots_created=2, activities_created=1)
    )
    return source


async def test_garmin_sync_body_records_run() -> None:
    """A completed Garmin sync records one success row."""
    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=_mock_garmin_source()),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch(
            "mycoach.scheduler.jobs.merge_garmin_hevy",
            AsyncMock(return_value=MagicMock(merged=0)),
        ),
    ):
        await _record_run("garmin_sync", _garmin_sync())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].job_name == "garmin_sync"
    assert runs[0].status == "success"


async def test_garmin_auth_failure_records_failed_run() -> None:
    """Credential expiry is a recorded failure, not a silent return.

    Sync, briefing, and post-workout analysis all sit downstream of this, so an
    unrecorded auth failure would halt the daily pipeline with no signal at all.
    """
    source = _mock_garmin_source()
    source.authenticate = AsyncMock(return_value=False)

    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=source),
        patch("mycoach.scheduler.jobs.async_session", test_session),
    ):
        await _record_run("garmin_sync", _garmin_sync())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error == "Garmin authentication failed"
    source.fetch_and_import.assert_not_awaited()


async def test_weekly_plan_body_records_run() -> None:
    """A generated weekly plan records one success row."""
    mock_engine = MagicMock()
    mock_engine.generate_weekly_plan = AsyncMock(return_value=MagicMock())

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _record_run("weekly_plan", _weekly_plan())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].job_name == "weekly_plan"
    assert runs[0].status == "success"


async def test_weekly_plan_body_records_skip() -> None:
    """An existing active plan records a skip, leaving idempotency untouched."""
    mock_engine = MagicMock()
    mock_engine.generate_weekly_plan = AsyncMock(
        side_effect=PipelineSkip("Active plan already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
    ):
        await _record_run("weekly_plan", _weekly_plan())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    assert runs[0].error is None


async def test_weekly_plan_body_records_failure() -> None:
    """A generation failure records a failed run with the cause."""
    mock_engine = MagicMock()
    mock_engine.generate_weekly_plan = AsyncMock(side_effect=RuntimeError("bad JSON"))

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
    ):
        await _record_run("weekly_plan", _weekly_plan())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error == "bad JSON"


async def test_weekly_recap_body_records_run() -> None:
    """A generated weekly recap records one success row."""
    mock_engine = MagicMock()
    mock_engine.generate_weekly_recap = AsyncMock(return_value=MagicMock())

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _record_run("weekly_recap", _weekly_recap())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].job_name == "weekly_recap"
    assert runs[0].status == "success"


async def test_weekly_recap_body_records_skip() -> None:
    """An existing recap records a skip, leaving idempotency untouched."""
    mock_engine = MagicMock()
    mock_engine.generate_weekly_recap = AsyncMock(
        side_effect=PipelineSkip("Weekly recap already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
    ):
        await _record_run("weekly_recap", _weekly_recap())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "skipped"


async def test_weekly_recap_body_records_failure() -> None:
    """A generation failure records a failed run with the cause."""
    mock_engine = MagicMock()
    mock_engine.generate_weekly_recap = AsyncMock(side_effect=RuntimeError("bad JSON"))

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
    ):
        await _record_run("weekly_recap", _weekly_recap())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error == "bad JSON"


async def _add_activities(count: int) -> None:
    """Persist unanalysed activities inside the post-workout scan window."""
    async with test_session() as session:
        for i in range(count):
            session.add(
                Activity(
                    user_id=USER_ID,
                    sport="swimming",
                    title=f"Session {i}",
                    start_time=datetime.utcnow(),
                    data_source="garmin",
                )
            )
        await session.commit()


async def test_post_workout_body_records_single_success_for_whole_batch() -> None:
    """Several activities in one execution still record exactly one run."""
    await _add_activities(2)
    mock_engine = MagicMock()
    mock_engine.generate_post_workout_analysis = AsyncMock(return_value=MagicMock())

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _record_run("post_workout_analysis", _post_workout_analysis())

    runs = await _job_runs()
    assert len(runs) == 1  # one row for the run, not one per activity
    assert runs[0].job_name == "post_workout_analysis"
    assert runs[0].status == "success"
    assert mock_engine.generate_post_workout_analysis.await_count == 2


async def test_post_workout_body_records_skip_when_nothing_to_analyse() -> None:
    """An empty scan records a skip — no activities is not a failure."""
    mock_engine = MagicMock()
    mock_engine.generate_post_workout_analysis = AsyncMock()

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
    ):
        await _record_run("post_workout_analysis", _post_workout_analysis())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    mock_engine.generate_post_workout_analysis.assert_not_awaited()


async def test_post_workout_body_records_partial_failure_as_one_failed_run() -> None:
    """One activity failing out of two fails the run, and only the run."""
    await _add_activities(2)
    mock_engine = MagicMock()
    mock_engine.generate_post_workout_analysis = AsyncMock(
        side_effect=[RuntimeError("bad JSON"), MagicMock()]
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _record_run("post_workout_analysis", _post_workout_analysis())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "1 of 2 activities failed" in runs[0].error
    assert "bad JSON" in runs[0].error
    # The surviving activity was still analysed — resilience is unchanged.
    assert mock_engine.generate_post_workout_analysis.await_count == 2


async def test_post_workout_body_records_skip_when_all_already_analysed() -> None:
    """A batch where every activity already had an insight persists as skipped."""
    await _add_activities(2)
    mock_engine = MagicMock()
    mock_engine.generate_post_workout_analysis = AsyncMock(
        side_effect=PipelineSkip("Post-workout analysis already exists")
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _record_run("post_workout_analysis", _post_workout_analysis())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    assert runs[0].error is None


async def test_post_workout_failed_email_counts_the_activity_once() -> None:
    """An activity whose email send fails is a failure, not both a success and one.

    The tally feeds the run's only durable error detail, so an activity counted
    twice would persist a wrong denominator ("1 of 2" for a single activity).
    """
    await _add_activities(1)
    mock_engine = MagicMock()
    mock_engine.generate_post_workout_analysis = AsyncMock(
        return_value=MagicMock(content='{"performance_summary": "ok"}')
    )

    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=mock_engine),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch(
            "mycoach.scheduler.jobs.send_post_workout",
            side_effect=RuntimeError("SMTP down"),
        ),
    ):
        await _record_run("post_workout_analysis", _post_workout_analysis())

    runs = await _job_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "1 of 1 activities failed" in runs[0].error
