"""Tests for scheduler API endpoints."""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from mycoach.main import app

TOKEN = "secret-token"


class _FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.next_run_time: datetime | None = None
        self.trigger = "cron"
        self.modify_calls: list[dict[str, Any]] = []

    def modify(self, **kwargs: Any) -> None:
        self.modify_calls.append(kwargs)
        self.next_run_time = kwargs.get("next_run_time")


class _FakeScheduler:
    def __init__(self, jobs: list[_FakeJob]) -> None:
        self._jobs = {job.id: job for job in jobs}
        self.running = True
        self.timezone = UTC

    def get_jobs(self) -> list[_FakeJob]:
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> _FakeJob | None:
        return self._jobs.get(job_id)


@pytest.fixture(autouse=True)
def _api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Overrides any value from .env for deterministic auth tests.
    monkeypatch.setenv("MYCOACH_API_TOKEN", TOKEN)


@pytest.fixture
def fake_scheduler():  # type: ignore[no-untyped-def]
    """Attach a fake scheduler to app.state, and detach it afterward.

    ``app`` is one module-level instance shared across the whole test suite
    (see tests/conftest.py), so a leaked ``app.state.scheduler`` would break
    other scheduler tests depending on run order.
    """
    scheduler = _FakeScheduler([_FakeJob("daily_briefing"), _FakeJob("weekly_recap")])
    app.state.scheduler = scheduler
    yield scheduler
    del app.state.scheduler


async def test_scheduler_status_no_scheduler(client: AsyncClient) -> None:
    """When scheduler is not running (test mode), should return empty state."""
    resp = await client.get("/api/system/scheduler")
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert data["jobs"] == []


class TestTriggerSchedulerJob:
    async def test_requires_key(self, client: AsyncClient, fake_scheduler: _FakeScheduler) -> None:
        resp = await client.post("/api/system/scheduler/trigger/daily_briefing")
        assert resp.status_code == 401

    async def test_wrong_key_rejected(
        self, client: AsyncClient, fake_scheduler: _FakeScheduler
    ) -> None:
        resp = await client.post(
            "/api/system/scheduler/trigger/daily_briefing",
            headers={"X-API-Key": "nope"},
        )
        assert resp.status_code == 401

    async def test_503_when_scheduler_not_running(self, client: AsyncClient) -> None:
        # No fake_scheduler fixture here — app.state.scheduler is unset, as in test mode.
        resp = await client.post(
            "/api/system/scheduler/trigger/daily_briefing",
            headers={"X-API-Key": TOKEN},
        )
        assert resp.status_code == 503

    async def test_404_for_unknown_job(
        self, client: AsyncClient, fake_scheduler: _FakeScheduler
    ) -> None:
        resp = await client.post(
            "/api/system/scheduler/trigger/nonsense",
            headers={"X-API-Key": TOKEN},
        )
        assert resp.status_code == 404
        assert "daily_briefing" in resp.json()["detail"]

    async def test_202_reschedules_job_to_now(
        self, client: AsyncClient, fake_scheduler: _FakeScheduler
    ) -> None:
        resp = await client.post(
            "/api/system/scheduler/trigger/daily_briefing",
            headers={"X-API-Key": TOKEN},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["job_id"] == "daily_briefing"

        job = fake_scheduler.get_job("daily_briefing")
        assert job is not None
        assert len(job.modify_calls) == 1
        run_at = job.modify_calls[0]["next_run_time"]
        assert isinstance(run_at, datetime)
        assert run_at.tzinfo is not None

        # The other job must be untouched.
        other = fake_scheduler.get_job("weekly_recap")
        assert other is not None
        assert other.modify_calls == []
