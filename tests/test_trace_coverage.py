"""Tests for the trace-coverage check (#179).

Langfuse is faked at the HTTP level: the check talks to the real SDK API client,
whose transport answers ``GET /api/public/v2/observations`` from an in-memory
list of root observations. Trace IDs in the fake are computed here with
``hashlib`` (``sha256(seed)[:16]``), independently of the code under test.
"""

import asyncio
import hashlib
import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from langfuse.api import LangfuseAPI

from mycoach.models.coaching import CoachingInsight
from mycoach.models.job_run import JobRun
from mycoach.models.plan import WeeklyPlan
from mycoach.models.user import User
from mycoach.tools.trace_coverage import LangfuseTraces, check_trace_coverage, main
from tests.conftest import test_session as sessions

LONDON = ZoneInfo("Europe/London")
SINCE = datetime(2026, 10, 1)


def _tid(seed: str) -> str:
    return hashlib.sha256(seed.encode()).digest()[:16].hex()


class FakeLangfuse:
    """Root observations, served the way the v2 observations endpoint filters them."""

    def __init__(self) -> None:
        self.roots: list[dict[str, Any]] = []

    def add_trace(self, seed: str, *, name: str, session_id: str) -> None:
        # v5 JSON-encodes metadata values, so the string arrives quoted.
        self.roots.append(
            {
                "id": f"obs-{len(self.roots)}",
                "traceId": _tid(seed),
                "startTime": "2026-10-05T07:00:00Z",
                "type": "SPAN",
                "projectId": "p",
                "name": name,
                "sessionId": session_id,
                "isRootObservation": True,
                "metadata": {"seed": json.dumps(seed)},
            }
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/public/v2/observations"
        assert request.headers["authorization"].startswith("Basic ")
        params = request.url.params
        rows = [
            r
            for r in self.roots
            if all(
                params.get(param) in (None, r[key])
                for param, key in (
                    ("traceId", "traceId"),
                    ("sessionId", "sessionId"),
                    ("name", "name"),
                )
            )
        ]
        limit = int(params.get("limit", "50"))
        start = int(params.get("cursor") or 0)
        page = rows[start : start + limit]
        more = start + limit < len(rows)
        return httpx.Response(
            200, json={"data": page, "meta": {"cursor": str(start + limit) if more else None}}
        )


@pytest.fixture
def langfuse() -> Iterator[FakeLangfuse]:
    yield FakeLangfuse()


def _traces(fake: FakeLangfuse) -> LangfuseTraces:
    client = httpx.Client(transport=httpx.MockTransport(fake.handle))
    return LangfuseTraces(
        LangfuseAPI(
            base_url="http://langfuse.test", username="pk", password="sk", httpx_client=client
        )
    )


async def _seed(*rows: object) -> None:
    async with sessions() as session:
        session.add(User(id=1, email="t@example.com", name="T", fitness_level="intermediate"))
        session.add_all(rows)
        await session.commit()


def _run(job: str, started: datetime, *, status: str = "success", ms: int = 20_000) -> JobRun:
    return JobRun(job_name=job, started_at=started, duration_ms=ms, status=status)


def _insight(
    kind: str, day: date, created: datetime, activity_id: str | None = None
) -> CoachingInsight:
    return CoachingInsight(
        user_id=1,
        insight_date=day,
        insight_type=kind,
        content="{}",
        created_at=created,
        activity_external_id=activity_id,
    )


async def _check(fake: FakeLangfuse):  # type: ignore[no-untyped-def]
    async with sessions() as session:
        return await check_trace_coverage(session, _traces(fake), since=SINCE, tz=LONDON)


async def test_lists_exactly_the_runs_whose_trace_is_missing(langfuse: FakeLangfuse) -> None:
    oct5 = datetime(2026, 10, 5, 6, 0)
    oct6 = datetime(2026, 10, 6, 6, 0)
    await _seed(
        _run("daily_briefing", oct5),
        _insight("daily_briefing", date(2026, 10, 5), oct5 + timedelta(seconds=12)),
        _run("daily_briefing", oct6),
        _insight("daily_briefing", date(2026, 10, 6), oct6 + timedelta(seconds=12)),
    )
    langfuse.add_trace(
        "mycoach:daily-briefing:1:2026-10-05",
        name="daily-briefing",
        session_id="mycoach:week:1:2026-10-05",
    )

    report = await _check(langfuse)

    assert [c.trace_id for c in report.missing] == [_tid("mycoach:daily-briefing:1:2026-10-06")]
    assert len(report.covered) == 1
    assert not report.ok


async def test_skipped_failed_and_non_llm_runs_are_ignored(langfuse: FakeLangfuse) -> None:
    t = datetime(2026, 10, 5, 6, 0)
    await _seed(
        _run("daily_briefing", t, status="skipped"),
        _run("daily_briefing", t + timedelta(minutes=15), status="failed"),
        _run("garmin_sync", t),
        _run("daily_briefing_alert", t),
        # Before the window.
        _run("daily_briefing", datetime(2026, 9, 20, 6, 0)),
        _insight("daily_briefing", date(2026, 9, 20), datetime(2026, 9, 20, 6, 0, 5)),
    )

    report = await _check(langfuse)

    assert report.checks == []
    assert report.ok


async def test_post_workout_batch_expects_one_trace_per_activity(langfuse: FakeLangfuse) -> None:
    t = datetime(2026, 10, 5, 21, 0)
    await _seed(
        _run("post_workout_analysis", t, ms=90_000),
        _insight("post_workout", date(2026, 10, 4), t + timedelta(seconds=30), activity_id="7"),
        _insight("post_workout", date(2026, 10, 5), t + timedelta(seconds=80), activity_id="8"),
        # Analysed later, by the manual route: not this batch's.
        _insight("post_workout", date(2026, 10, 5), t + timedelta(hours=2), activity_id="9"),
    )
    langfuse.add_trace(
        "mycoach:post-workout:7", name="post-workout", session_id="mycoach:week:1:2026-09-28"
    )

    report = await _check(langfuse)

    assert [c.trace_id for c in report.covered] == [_tid("mycoach:post-workout:7")]
    assert [c.trace_id for c in report.missing] == [_tid("mycoach:post-workout:8")]


async def test_weekly_plan_and_recap_are_keyed_by_their_artifacts(
    langfuse: FakeLangfuse,
) -> None:
    sunday = datetime(2026, 10, 4, 18, 0)
    monday = datetime(2026, 10, 5, 7, 0)
    await _seed(
        _run("weekly_plan", sunday, ms=60_000),
        WeeklyPlan(
            id=42,
            user_id=1,
            week_start=date(2026, 10, 5),
            created_at=sunday + timedelta(seconds=50),
        ),
        _run("weekly_recap", monday),
        _insight("weekly_recap", date(2026, 9, 28), monday + timedelta(seconds=15)),
    )
    langfuse.add_trace(
        "mycoach:weekly-plan:42", name="weekly-plan", session_id="mycoach:week:1:2026-10-05"
    )

    report = await _check(langfuse)

    assert [c.trace_id for c in report.covered] == ["665f9fe7501f90dd1e2ee79f492b32ed"]
    assert [c.trace_id for c in report.missing] == [_tid("mycoach:weekly-recap:1:2026-09-28")]


async def test_a_regenerated_plan_is_covered_by_the_replacement_plans_trace(
    langfuse: FakeLangfuse,
) -> None:
    sunday = datetime(2026, 10, 4, 18, 0)
    # The scheduled plan was deleted by a forced regeneration on Monday.
    await _seed(
        _run("weekly_plan", sunday, ms=60_000),
        WeeklyPlan(
            id=43, user_id=1, week_start=date(2026, 10, 5), created_at=datetime(2026, 10, 5, 9, 0)
        ),
    )
    langfuse.add_trace(
        "mycoach:weekly-plan:43", name="weekly-plan", session_id="mycoach:week:1:2026-10-05"
    )

    report = await _check(langfuse)

    assert [c.trace_id for c in report.covered] == [_tid("mycoach:weekly-plan:43")]
    assert report.ok


async def test_a_regenerated_briefing_counts_its_natural_or_its_regen_trace(
    langfuse: FakeLangfuse,
) -> None:
    oct5, oct6, oct7 = (datetime(2026, 10, d, 6, 0) for d in (5, 6, 7))
    later = timedelta(hours=3)
    # All three briefings were force-regenerated later the same day.
    await _seed(
        _run("daily_briefing", oct5),
        _insight("daily_briefing", date(2026, 10, 5), oct5 + later),
        _run("daily_briefing", oct6),
        _insight("daily_briefing", date(2026, 10, 6), oct6 + later),
        _run("daily_briefing", oct7),
        _insight("daily_briefing", date(2026, 10, 7), oct7 + later),
    )
    week = "mycoach:week:1:2026-10-05"
    # Oct 5: the scheduled run's own trace survives.
    langfuse.add_trace(
        "mycoach:daily-briefing:1:2026-10-05", name="daily-briefing", session_id=week
    )
    # Oct 6: only the regeneration's trace.
    langfuse.add_trace(
        "mycoach:daily-briefing:1:2026-10-06:regen:20261006T090000Z",
        name="daily-briefing",
        session_id=week,
    )
    # Oct 7: nothing of its own; another day's regen must not count.
    langfuse.add_trace(
        "mycoach:daily-briefing:1:2026-10-08:regen:20261008T090000Z",
        name="daily-briefing",
        session_id=week,
    )

    report = await _check(langfuse)

    assert [c.subject for c in report.covered] == [
        "daily briefing 2026-10-05",
        "daily briefing 2026-10-06",
    ]
    assert [c.trace_id for c in report.missing] == [_tid("mycoach:daily-briefing:1:2026-10-07")]


async def test_a_briefing_key_uses_the_scheduler_local_day(langfuse: FakeLangfuse) -> None:
    # 23:30 UTC on Oct 5 is 00:30 on Oct 6 in London (BST).
    t = datetime(2026, 10, 5, 23, 30)
    await _seed(
        _run("daily_briefing", t),
        _insight("daily_briefing", date(2026, 10, 6), t + timedelta(hours=5)),
    )
    langfuse.add_trace(
        "mycoach:daily-briefing:1:2026-10-06",
        name="daily-briefing",
        session_id="mycoach:week:1:2026-10-05",
    )

    report = await _check(langfuse)

    assert [c.subject for c in report.covered] == ["daily briefing 2026-10-06"]


async def test_a_run_with_no_artifact_left_is_unresolved(langfuse: FakeLangfuse) -> None:
    await _seed(
        _run("daily_briefing", datetime(2026, 10, 5, 6, 0)),
        _run("post_workout_analysis", datetime(2026, 10, 5, 21, 0)),
    )

    report = await _check(langfuse)

    assert [c.job_name for c in report.unresolved] == ["daily_briefing", "post_workout_analysis"]
    assert not report.ok


# --- The command -------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("MYCOACH_SCHEDULER_TIMEZONE", "Europe/London")
    monkeypatch.setattr("mycoach.database.async_session", sessions)


def _client(fake: FakeLangfuse) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake.handle))


async def _recent_briefing() -> str:
    t = datetime.utcnow().replace(microsecond=0) - timedelta(days=1)
    await _seed(
        _run("daily_briefing", t), _insight("daily_briefing", t.date(), t + timedelta(seconds=5))
    )
    return t.date().isoformat()


async def test_command_exits_non_zero_and_lists_a_missing_trace(
    configured: None, langfuse: FakeLangfuse, capsys: pytest.CaptureFixture[str]
) -> None:
    await _recent_briefing()

    code = await asyncio.to_thread(main, ["--days", "7"], httpx_client=_client(langfuse))

    out = capsys.readouterr().out
    assert code == 1
    assert "1 missing" in out
    assert "MISSING" in out


async def test_command_exits_zero_when_every_run_is_traced(
    configured: None, langfuse: FakeLangfuse, capsys: pytest.CaptureFixture[str]
) -> None:
    day = await _recent_briefing()
    # The session ID only matters for regeneration lookups.
    langfuse.add_trace(f"mycoach:daily-briefing:1:{day}", name="daily-briefing", session_id="s")

    code = await asyncio.to_thread(main, ["--days", "7"], httpx_client=_client(langfuse))

    assert code == 0
    assert "1 covered" in capsys.readouterr().out


def test_command_refuses_when_langfuse_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LANGFUSE_BASE_URL", "")

    assert main(["--days", "7"]) == 2
    assert "LANGFUSE_BASE_URL" in capsys.readouterr().err


def test_command_rejects_a_window_past_langfuse_retention() -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["--days", "90"])
    assert exit_.value.code == 2
