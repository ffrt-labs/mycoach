"""The job log page: a scheduled job's recent runs, read-only (#242's Setup notice)."""

import re
from datetime import datetime

from httpx import AsyncClient

from mycoach.models.job_run import JobRun
from tests.conftest import test_session


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


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


async def test_the_job_log_page_lists_the_jobs_runs_newest_first(client: AsyncClient) -> None:
    await _seed_runs(
        ("weekly_plan", datetime(2026, 11, 8, 12), "success", None),
        ("weekly_plan", datetime(2026, 11, 15, 12), "failed", "LLM quota exceeded"),
        ("weekly_recap", datetime(2026, 11, 16, 7), "success", None),
    )

    resp = await client.get("/jobs/weekly_plan")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    text = _text(resp.text)
    assert "weekly_plan" in text
    failed, ok = text.index("LLM quota exceeded"), text.index("Nov 08")
    assert text.index("Nov 15") < failed < ok
    assert "weekly_recap" not in text


async def test_a_job_that_never_ran_says_so(client: AsyncClient) -> None:
    resp = await client.get("/jobs/weekly_plan")

    assert resp.status_code == 200
    assert "No runs recorded" in resp.text
