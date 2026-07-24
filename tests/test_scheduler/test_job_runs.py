"""Tests for durable scheduled-job run recording.

Drive a job body through the recording helper and assert on the persisted
``JobRun`` rows, following the scheduler job-test prior art in ``test_jobs.py``.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from mycoach.coaching.exceptions import PipelineSkip
from mycoach.models.job_run import JobRun
from mycoach.scheduler.jobs import _daily_briefing, _record_run
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
