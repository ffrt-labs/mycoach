"""Scheduled job functions for the daily coaching pipeline.

Each job creates its own async DB session, runs the relevant pipeline step,
and logs results. Jobs are designed to be idempotent — they skip gracefully
if the output already exists for the current day/week.
"""

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta

from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import PipelineSkip
from mycoach.config import get_settings
from mycoach.database import async_session
from mycoach.email.sender import (
    send_daily_briefing,
    send_post_workout,
    send_weekly_plan,
    send_weekly_recap,
)
from mycoach.models.activity import Activity
from mycoach.models.coaching import CoachingInsight
from mycoach.models.job_run import JobRun
from mycoach.models.plan import PlannedSession
from mycoach.models.user import User
from mycoach.sources.garmin.source import GarminSource
from mycoach.sources.merger import merge_garmin_hevy

logger = logging.getLogger(__name__)

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _run_async(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine from a sync APScheduler job."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _run_job(label: str, coro) -> None:  # type: ignore[no-untyped-def]
    """Run a pipeline coroutine, translating its outcome into a log line.

    A ``PipelineSkip`` is a deliberate no-op and logged at info level. Every
    other exception is a real failure and logged at error level. This is the
    skip-versus-failure boundary the rest of the pipeline keys on.
    """
    try:
        _run_async(coro)
    except PipelineSkip as e:
        logger.info("Scheduler: %s skipped — %s", label, e)
    except Exception:
        logger.exception("Scheduler: %s failed", label)


async def _record_run(job_name: str, coro) -> None:  # type: ignore[no-untyped-def]
    """Run a job body, timing it and recording its outcome durably.

    Writes one append-only ``JobRun`` row — job identifier, start time,
    duration, status (``success`` / ``skipped`` / ``failed``), and error detail
    on failure — and emits a structured log line carrying the same facts. A
    ``PipelineSkip`` is a deliberate no-op (``skipped``); every other exception
    is a real failure (``failed``). Exceptions never propagate out to the
    scheduler.
    """
    started_at = datetime.utcnow()
    start = time.perf_counter()
    status = "success"
    detail: str | None = None  # skip reason or failure message, for the log line
    failure: Exception | None = None
    try:
        await coro
    except PipelineSkip as e:
        status = "skipped"
        detail = str(e)
    except Exception as e:  # noqa: BLE001 - outcome is recorded, not re-raised
        status = "failed"
        detail = str(e)
        failure = e
    duration_ms = int((time.perf_counter() - start) * 1000)

    # The row's error column is failure detail only, per the spec; a skip's
    # reason is not an error and lives only in the log line below.
    error = detail if status == "failed" else None

    async with async_session() as session:
        session.add(
            JobRun(
                job_name=job_name,
                started_at=started_at,
                duration_ms=duration_ms,
                status=status,
                error=error,
            )
        )
        await session.commit()

    extra = {
        "job_name": job_name,
        "job_status": status,
        "job_error": error,
        "duration_ms": duration_ms,
    }
    if status == "failed":
        logger.error("Scheduler: %s failed", job_name, exc_info=failure, extra=extra)
    elif status == "skipped":
        logger.info("Scheduler: %s skipped — %s", job_name, detail, extra=extra)
    else:
        logger.info("Scheduler: %s succeeded", job_name, extra=extra)


def _run_recorded_job(job_name: str, coro) -> None:  # type: ignore[no-untyped-def]
    """Sync entry point for a recorded job — the single call site per job."""
    _run_async(_record_run(job_name, coro))


async def _get_user_email_pref(pref_field: str) -> bool:
    """Check if user has a specific email preference enabled."""
    settings = get_settings()
    if not settings.email_enabled:
        return False
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == USER_ID))
        user = result.scalar_one_or_none()
        if user is None:
            return False
        return bool(getattr(user, pref_field, False))


def job_garmin_sync() -> None:
    """Sync health and activity data from Garmin Connect.

    Fetches the last 2 days of data to handle timezone edge cases and overnight sync.
    """
    logger.info("Scheduler: starting Garmin sync")
    _run_job("Garmin sync", _garmin_sync())


async def _garmin_sync() -> None:
    source = GarminSource()
    if not await source.authenticate():
        raise RuntimeError("Garmin authentication failed")

    async with async_session() as session:
        since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        since = since - timedelta(days=2)
        result = await source.fetch_and_import(session, USER_ID, since=since)
        merge_result = await merge_garmin_hevy(session, USER_ID)
        await session.commit()
        logger.info(
            "Scheduler: Garmin sync complete — health=%d, activities=%d, merged=%d",
            result.health_snapshots_created,
            result.activities_created,
            merge_result.merged,
        )


def job_daily_briefing() -> None:
    """Generate the daily coaching briefing."""
    logger.info("Scheduler: generating daily briefing")
    _run_recorded_job("daily_briefing", _daily_briefing())


async def _daily_briefing() -> None:
    engine = CoachingEngine()
    async with async_session() as session:
        insight = await engine.generate_daily_briefing(session, USER_ID)
        logger.info("Scheduler: daily briefing generated")

        if await _get_user_email_pref("email_daily_briefing"):
            content = json.loads(insight.content)
            send_daily_briefing(content)
            logger.info("Scheduler: daily briefing email sent")



def job_weekly_plan() -> None:
    """Generate the weekly training plan (runs Sunday evening for next week)."""
    logger.info("Scheduler: generating weekly plan")
    _run_job("weekly plan", _weekly_plan())


async def _weekly_plan() -> None:
    engine = CoachingEngine()
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = today + timedelta(days=days_until_monday)

    async with async_session() as session:
        plan = await engine.generate_weekly_plan(session, USER_ID, next_monday)
        logger.info("Scheduler: weekly plan generated for %s", next_monday)

        if await _get_user_email_pref("email_weekly_plan"):
            result = await session.execute(
                select(PlannedSession)
                .where(PlannedSession.plan_id == plan.id)
                .order_by(PlannedSession.day_of_week)
            )
            sessions = result.scalars().all()
            session_dicts = [
                {
                    "day_name": DAY_NAMES[s.day_of_week],
                    "title": s.title,
                    "sport": s.sport,
                    "duration_minutes": s.duration_minutes,
                    "notes": s.notes,
                    "details": json.loads(s.details) if s.details else None,
                }
                for s in sessions
            ]
            send_weekly_plan(
                summary=plan.summary or "",
                sessions=session_dicts,
                week_start=str(next_monday),
            )
            logger.info("Scheduler: weekly plan email sent")


def job_weekly_recap() -> None:
    """Generate the weekly recap (runs Monday morning for the previous week)."""
    logger.info("Scheduler: generating weekly recap")
    _run_job("weekly recap", _weekly_recap())


async def _weekly_recap() -> None:
    engine = CoachingEngine()
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)

    async with async_session() as session:
        insight = await engine.generate_weekly_recap(session, USER_ID, last_monday)
        logger.info("Scheduler: weekly recap generated for week of %s", last_monday)

        if await _get_user_email_pref("email_weekly_recap"):
            content = json.loads(insight.content)
            send_weekly_recap(content, week_start=str(last_monday))
            logger.info("Scheduler: weekly recap email sent")


def job_post_workout_analysis() -> None:
    """Analyze recent activities that don't have a post-workout insight yet.

    Runs after Garmin sync. Finds activities from the last 2 days without
    an existing CoachingInsight, generates analysis for each, and sends email.
    """
    logger.info("Scheduler: starting post-workout analysis scan")
    _run_job("post-workout analysis", _post_workout_analysis())


async def _post_workout_analysis() -> None:
    engine = CoachingEngine()
    since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    since = since - timedelta(days=2)

    async with async_session() as session:
        # Find activities from the last 2 days that have no post-workout insight
        existing_ids_stmt = (
            select(CoachingInsight.activity_id)
            .where(
                CoachingInsight.user_id == USER_ID,
                CoachingInsight.insight_type == "post_workout",
                CoachingInsight.activity_id.isnot(None),
            )
        )
        activities_stmt = (
            select(Activity)
            .where(
                Activity.user_id == USER_ID,
                Activity.start_time >= since,
                Activity.id.notin_(existing_ids_stmt),
            )
            .order_by(Activity.start_time)
        )
        result = await session.execute(activities_stmt)
        activities = list(result.scalars().all())

        if not activities:
            raise PipelineSkip("no new activities to analyse")

        logger.info("Scheduler: found %d activities to analyze", len(activities))
        send_email = await _get_user_email_pref("email_post_workout")

        for activity in activities:
            # Per-activity resilience: one activity's skip or failure must not
            # abort the batch. A whole-batch skip (no activities) is raised above.
            try:
                insight = await engine.generate_post_workout_analysis(
                    session, USER_ID, activity.id
                )
                logger.info(
                    "Scheduler: post-workout analysis generated for activity %d (%s)",
                    activity.id,
                    activity.title,
                )

                if send_email:
                    content = json.loads(insight.content)
                    send_post_workout(content, activity.title)
                    logger.info(
                        "Scheduler: post-workout email sent for activity %d", activity.id
                    )
            except PipelineSkip as e:
                logger.info(
                    "Scheduler: post-workout analysis skipped for activity %d — %s",
                    activity.id,
                    e,
                )
            except Exception:
                logger.exception(
                    "Scheduler: post-workout analysis failed for activity %d", activity.id
                )
