"""Trace-coverage check (#179): is every scheduled coaching run traced in Langfuse?

The evidence for the gate that retires ``prompt_logs``. For the last N days of
``job_runs`` it takes every successful run of a job that calls the LLM, rebuilds
the run's seeded trace ID, looks the trace up through the Langfuse API, and
reports each run with no trace. Run it where the app's ``.env`` is loaded:

    python -m mycoach.tools.trace_coverage [--days 30]

Exit code: 0 when every run is covered, 1 when any trace is missing or a run
could not be matched to its artifact, 2 when Langfuse is not configured or
cannot be reached. Matching rules are in ``docs/trace-coverage.md``.

Trace IDs come from the run builders in ``mycoach.observability.tracing`` (and
so from ``trace_ids``), the same ones the engine traces with; nothing here
spells a seed out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

import httpx
from langfuse.api import LangfuseAPI
from langfuse.api.core.api_error import ApiError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.coaching import CoachingInsight
from mycoach.models.job_run import JobRun
from mycoach.models.plan import WeeklyPlan
from mycoach.observability.trace_ids import is_regen_of, trace_id
from mycoach.observability.tracing import (
    RunSpec,
    daily_briefing_run,
    post_workout_run,
    weekly_plan_run,
    weekly_recap_run,
)
from mycoach.scheduler.weeks import SCHEDULER_USER_ID, plan_week_start, recap_week_start

# Langfuse deletes traces after 90 days, so a longer window reports false gaps.
MAX_DAYS = 89

# Slack around a run's [started_at, started_at + duration] window. Both ends are
# ``datetime.utcnow()`` stamps from the same process, so a second is plenty.
_SLACK = timedelta(seconds=1)

DAILY_BRIEFING = "daily_briefing"
WEEKLY_PLAN = "weekly_plan"
WEEKLY_RECAP = "weekly_recap"
POST_WORKOUT = "post_workout_analysis"
# Jobs that call the LLM. ``daily_briefing_alert`` and ``garmin_sync`` don't.
LLM_JOBS = (DAILY_BRIEFING, WEEKLY_PLAN, WEEKLY_RECAP, POST_WORKOUT)


class TraceLookup(Protocol):
    def exists(self, trace_id: str) -> bool:
        """Does Langfuse hold a trace with this ID?"""
        ...

    def regenerated(self, run: RunSpec) -> bool:
        """Does Langfuse hold a forced regeneration of ``run``?"""
        ...


class LangfuseTraces:
    """``TraceLookup`` over the Langfuse public API.

    SDK v5 has no trace-get endpoint; the v2 observations endpoint (the
    real-time read path) is filtered to a trace's root observation instead.
    """

    def __init__(self, api: LangfuseAPI) -> None:
        self._api = api

    def exists(self, trace_id: str) -> bool:
        page = self._api.observations.get_many(
            trace_id=trace_id, is_root_observation=True, fields="core", limit=1
        )
        return bool(page.data)

    def regenerated(self, run: RunSpec) -> bool:
        """A regen trace's seed carries a timestamp nobody stores, so it can't be
        rebuilt. Instead list the root observations of the run's week session and
        trace name, and recognise one whose seed is a regeneration of ``run``'s
        and whose trace ID is that seed's.
        """
        cursor: str | None = None
        while True:
            page = self._api.observations.get_many(
                session_id=run.session_id,
                name=run.name,
                is_root_observation=True,
                fields="core,basic,metadata",
                limit=100,
                cursor=cursor,
            )
            for obs in page.data:
                seed = _metadata_str((obs.metadata or {}).get("seed"))
                if seed and is_regen_of(seed, run.seed) and obs.trace_id == trace_id(seed):
                    return True
            cursor = page.meta.cursor
            if not cursor or not page.data:
                return False


def _metadata_str(value: object) -> str | None:
    """SDK v5 JSON-encodes metadata values, so a string arrives as ``'"x"'``."""
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except ValueError:
        return value
    return decoded if isinstance(decoded, str) else None


Status = Literal["covered", "missing", "unresolved"]


@dataclass(frozen=True)
class RunCheck:
    job_run_id: int
    job_name: str
    started_at: datetime
    status: Status
    subject: str
    trace_id: str | None = None
    note: str | None = None


@dataclass
class CoverageReport:
    checks: list[RunCheck] = field(default_factory=list)

    def _with(self, status: Status) -> list[RunCheck]:
        return [c for c in self.checks if c.status == status]

    @property
    def covered(self) -> list[RunCheck]:
        return self._with("covered")

    @property
    def missing(self) -> list[RunCheck]:
        return self._with("missing")

    @property
    def unresolved(self) -> list[RunCheck]:
        return self._with("unresolved")

    @property
    def ok(self) -> bool:
        return not self.missing and not self.unresolved


async def check_trace_coverage(
    session: AsyncSession, traces: TraceLookup, *, since: datetime, tz: ZoneInfo
) -> CoverageReport:
    """Check every successful LLM-job run that started at or after ``since``.

    ``since`` is naive UTC, like ``JobRun.started_at``. ``tz`` is the
    scheduler's timezone, which fixes the local day a run worked on when its
    key has to be rebuilt from the clock.
    """
    runs = (
        await session.scalars(
            select(JobRun)
            .where(
                JobRun.status == "success",
                JobRun.job_name.in_(LLM_JOBS),
                JobRun.started_at >= since,
            )
            .order_by(JobRun.started_at, JobRun.id)
        )
    ).all()
    report = CoverageReport()
    for run in runs:
        report.checks.extend(await _check_run(session, traces, run, tz))
    return report


async def _check_run(
    session: AsyncSession, traces: TraceLookup, run: JobRun, tz: ZoneInfo
) -> list[RunCheck]:
    start = run.started_at - _SLACK
    end = run.started_at + timedelta(milliseconds=run.duration_ms) + _SLACK
    local_day = run.started_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()

    def check(
        status: Status, subject: str, tid: str | None = None, note: str | None = None
    ) -> RunCheck:
        return RunCheck(run.id, run.job_name, run.started_at, status, subject, tid, note)

    if run.job_name == WEEKLY_PLAN:
        plans = (
            await session.scalars(
                select(WeeklyPlan)
                .where(WeeklyPlan.created_at.between(start, end))
                .order_by(WeeklyPlan.id)
            )
        ).all()
        regenerated = False
        if not plans:
            # A forced regeneration deletes the plan and writes a new one, with
            # a new ID and so a new trace: the replacement's trace is the one
            # that can still be checked, and it counts.
            week_start = plan_week_start(local_day)
            plans = (
                await session.scalars(
                    select(WeeklyPlan).where(
                        WeeklyPlan.week_start == week_start, WeeklyPlan.created_at > end
                    )
                )
            ).all()
            regenerated = True
            if not plans:
                return [
                    check(
                        "unresolved",
                        f"weekly plan for {week_start}",
                        note="no plan for the run's week",
                    )
                ]
        out = []
        for plan in plans:
            spec = weekly_plan_run(plan.id, plan.user_id, plan.week_start)
            subject = f"weekly plan {plan.id} ({plan.week_start})"
            note = "plan regenerated since the run" if regenerated else None
            status: Status = "covered" if traces.exists(spec.trace_id) else "missing"
            out.append(check(status, subject, spec.trace_id, note))
        return out

    insight_type = "post_workout" if run.job_name == POST_WORKOUT else run.job_name
    insights = (
        await session.scalars(
            select(CoachingInsight)
            .where(
                CoachingInsight.insight_type == insight_type,
                CoachingInsight.created_at.between(start, end),
            )
            .order_by(CoachingInsight.id)
        )
    ).all()
    if insights:
        out = []
        for insight in insights:
            spec, subject = _insight_spec(insight)
            status = "covered" if traces.exists(spec.trace_id) else "missing"
            out.append(check(status, subject, spec.trace_id))
        return out

    if run.job_name == POST_WORKOUT:
        # A batch records no activity IDs, so once its insights have been
        # replaced there is nothing left to say which activities it analysed.
        return [
            check(
                "unresolved",
                "post-workout batch",
                note="no post-workout insight created during the run",
            )
        ]

    # The run's insight has been replaced by a forced regeneration since (the
    # old row is deleted, the new one keeps the key). Scheduled runs never
    # force, so this run's own trace is the natural one; a regeneration's
    # trace, which supersedes it, also counts.
    day = local_day if run.job_name == DAILY_BRIEFING else recap_week_start(local_day)
    replacement = await session.scalar(
        select(CoachingInsight).where(
            CoachingInsight.insight_type == insight_type,
            CoachingInsight.insight_date == day,
            CoachingInsight.user_id == SCHEDULER_USER_ID,
            CoachingInsight.created_at > end,
        )
    )
    if replacement is None:
        return [
            check(
                "unresolved",
                f"{insight_type} for {day}",
                note=f"no {insight_type} insight for {day}",
            )
        ]
    spec, subject = _insight_spec(replacement)
    if traces.exists(spec.trace_id):
        return [check("covered", subject, spec.trace_id, "insight regenerated since the run")]
    if traces.regenerated(spec):
        return [check("covered", subject, spec.trace_id, "covered by the regeneration's trace")]
    return [
        check(
            "missing",
            subject,
            spec.trace_id,
            "insight regenerated since the run; no regeneration trace either",
        )
    ]


def _insight_spec(insight: CoachingInsight) -> tuple[RunSpec, str]:
    if insight.insight_type == "post_workout":
        if insight.activity_id is None:
            raise ValueError(f"post-workout insight {insight.id} has no activity")
        spec = post_workout_run(insight.activity_id, insight.user_id, insight.insight_date)
        return spec, f"post-workout activity {insight.activity_id}"
    if insight.insight_type == WEEKLY_RECAP:
        spec = weekly_recap_run(insight.user_id, insight.insight_date)
        return spec, f"weekly recap {insight.insight_date}"
    spec = daily_briefing_run(insight.user_id, insight.insight_date)
    return spec, f"daily briefing {insight.insight_date}"


def format_report(report: CoverageReport, *, days: int) -> str:
    lines = [
        f"Trace coverage, last {days} days: {len(report.checks)} runs checked, "
        f"{len(report.covered)} covered, {len(report.missing)} missing, "
        f"{len(report.unresolved)} unresolved."
    ]
    for c in report.missing + report.unresolved:
        line = (
            f"  {c.status.upper():<10} job_run {c.job_run_id} {c.job_name} "
            f"{c.started_at:%Y-%m-%d %H:%M}Z  {c.subject}"
        )
        if c.trace_id:
            line += f"  trace {c.trace_id}"
        if c.note:
            line += f"  ({c.note})"
        lines.append(line)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, httpx_client: httpx.Client | None = None) -> int:
    """``httpx_client`` replaces the Langfuse API's transport (tests)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help=f"How many days of job runs to check (1-{MAX_DAYS}; default 30).",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.days <= MAX_DAYS:
        parser.error(f"--days must be between 1 and {MAX_DAYS} (Langfuse keeps 90 days)")

    from mycoach.config import get_settings
    from mycoach.database import async_session

    settings = get_settings()
    if not (
        settings.langfuse_base_url and settings.langfuse_public_key and settings.langfuse_secret_key
    ):
        print(
            "Langfuse is not configured: set LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY "
            "and LANGFUSE_SECRET_KEY.",
            file=sys.stderr,
        )
        return 2
    api = LangfuseAPI(
        base_url=settings.langfuse_base_url,
        username=settings.langfuse_public_key,
        password=settings.langfuse_secret_key,
        timeout=30,
        httpx_client=httpx_client,
    )
    tz = ZoneInfo(settings.scheduler_timezone or settings.timezone)
    since = datetime.utcnow() - timedelta(days=args.days)

    async def run() -> CoverageReport:
        async with async_session() as session:
            return await check_trace_coverage(session, LangfuseTraces(api), since=since, tz=tz)

    try:
        report = asyncio.run(run())
    except (ApiError, httpx.HTTPError) as e:
        print(f"Langfuse lookup failed: {e}", file=sys.stderr)
        return 2
    print(format_report(report, days=args.days))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
