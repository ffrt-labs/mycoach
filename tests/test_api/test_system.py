from datetime import datetime

from httpx import AsyncClient

from mycoach.models.job_run import JobRun
from tests.conftest import test_session


async def test_health_check(client: AsyncClient) -> None:
    response = await client.get("/api/system/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def _seed_runs(*runs: tuple[str, datetime, str, str | None]) -> None:
    async with test_session() as session:
        for job_name, started_at, status, error in runs:
            session.add(
                JobRun(
                    job_name=job_name,
                    started_at=started_at,
                    duration_ms=1200,
                    status=status,
                    error=error,
                )
            )
        await session.commit()


async def test_the_job_log_lists_a_jobs_runs_newest_first(client: AsyncClient) -> None:
    """The recap's Setup notice links here when the weekly plan failed (#242)."""
    await _seed_runs(
        ("weekly_plan", datetime(2026, 11, 8, 18), "success", None),
        ("weekly_plan", datetime(2026, 11, 15, 18), "failed", "LLM quota exceeded"),
        ("weekly_recap", datetime(2026, 11, 16, 7), "success", None),
    )

    response = await client.get("/api/system/jobs/weekly_plan/runs")

    assert response.status_code == 200
    runs = response.json()
    assert [(r["started_at"][:10], r["status"]) for r in runs] == [
        ("2026-11-15", "failed"),
        ("2026-11-08", "success"),
    ]
    assert runs[0]["error"] == "LLM quota exceeded"


async def test_a_job_that_never_ran_has_an_empty_log(client: AsyncClient) -> None:
    response = await client.get("/api/system/jobs/weekly_plan/runs")

    assert response.status_code == 200
    assert response.json() == []
