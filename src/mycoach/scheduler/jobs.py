"""Scheduled job functions for the daily coaching pipeline.

Each job creates its own async DB session, runs the relevant pipeline step,
and logs results. Jobs are designed to be idempotent — they skip gracefully
if the output already exists for the current day/week.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from mycoach.coaching.engine import CoachingEngine
from mycoach.database import async_session
from mycoach.sources.garmin.source import GarminSource
from mycoach.sources.merger import merge_garmin_hevy

logger = logging.getLogger(__name__)

USER_ID = 1  # Single-user MVP


def _run_async(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine from a sync APScheduler job."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def job_garmin_sync() -> None:
    """Sync health and activity data from Garmin Connect.

    Fetches the last 2 days of data to handle timezone edge cases and overnight sync.
    """
    logger.info("Scheduler: starting Garmin sync")
    _run_async(_garmin_sync())


async def _garmin_sync() -> None:
    source = GarminSource()
    try:
        if not await source.authenticate():
            logger.error("Scheduler: Garmin authentication failed")
            return
    except Exception:
        logger.exception("Scheduler: Garmin authentication error")
        return

    async with async_session() as session:
        try:
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
        except Exception:
            logger.exception("Scheduler: Garmin sync failed")


def job_daily_briefing() -> None:
    """Generate the daily coaching briefing."""
    logger.info("Scheduler: generating daily briefing")
    _run_async(_daily_briefing())


async def _daily_briefing() -> None:
    engine = CoachingEngine()
    async with async_session() as session:
        try:
            await engine.generate_daily_briefing(session, USER_ID)
            logger.info("Scheduler: daily briefing generated")
        except ValueError as e:
            logger.info("Scheduler: daily briefing skipped — %s", e)
        except Exception:
            logger.exception("Scheduler: daily briefing failed")


def job_sleep_coaching() -> None:
    """Generate sleep coaching analysis."""
    logger.info("Scheduler: generating sleep coaching")
    _run_async(_sleep_coaching())


async def _sleep_coaching() -> None:
    engine = CoachingEngine()
    async with async_session() as session:
        try:
            await engine.generate_sleep_coaching(session, USER_ID)
            logger.info("Scheduler: sleep coaching generated")
        except ValueError as e:
            logger.info("Scheduler: sleep coaching skipped — %s", e)
        except Exception:
            logger.exception("Scheduler: sleep coaching failed")


def job_weekly_plan() -> None:
    """Generate the weekly training plan (runs Sunday evening for next week)."""
    logger.info("Scheduler: generating weekly plan")
    _run_async(_weekly_plan())


async def _weekly_plan() -> None:
    engine = CoachingEngine()
    # Next Monday from today
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = today + timedelta(days=days_until_monday)

    async with async_session() as session:
        try:
            await engine.generate_weekly_plan(session, USER_ID, next_monday)
            logger.info("Scheduler: weekly plan generated for %s", next_monday)
        except ValueError as e:
            logger.info("Scheduler: weekly plan skipped — %s", e)
        except Exception:
            logger.exception("Scheduler: weekly plan generation failed")


def job_weekly_recap() -> None:
    """Generate the weekly recap (runs Monday morning for the previous week)."""
    logger.info("Scheduler: generating weekly recap")
    _run_async(_weekly_recap())


async def _weekly_recap() -> None:
    engine = CoachingEngine()
    today = date.today()
    # Last Monday = start of the week that just ended
    last_monday = today - timedelta(days=today.weekday() + 7)

    async with async_session() as session:
        try:
            await engine.generate_weekly_recap(session, USER_ID, last_monday)
            logger.info("Scheduler: weekly recap generated for week of %s", last_monday)
        except ValueError as e:
            logger.info("Scheduler: weekly recap skipped — %s", e)
        except Exception:
            logger.exception("Scheduler: weekly recap generation failed")
