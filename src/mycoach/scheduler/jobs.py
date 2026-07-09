"""Scheduled job functions for the daily coaching pipeline.

Each job creates its own async DB session, runs the relevant pipeline step,
and logs results. Jobs are designed to be idempotent — they skip gracefully
if the output already exists for the current day/week.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
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
from mycoach.models.plan import PlannedSession
from mycoach.models.user import User
from mycoach.sources.garmin.source import GarminSource
from mycoach.sources.hevy.api_source import HevyApiSource
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


def job_hevy_sync() -> None:
    """Sync gym workouts from Hevy API (runs before Garmin so merge picks up new data)."""
    logger.info("Scheduler: starting Hevy sync")
    _run_async(_hevy_sync())


async def _hevy_sync() -> None:
    source = HevyApiSource()
    try:
        if not await source.authenticate():
            logger.error("Scheduler: Hevy authentication failed")
            return
    except Exception:
        logger.exception("Scheduler: Hevy authentication error")
        return

    async with async_session() as session:
        try:
            since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            since = since - timedelta(days=14)
            result = await source.fetch_and_import(session, USER_ID, since=since)
            merge_result = await merge_garmin_hevy(session, USER_ID)
            await session.commit()
            logger.info(
                "Scheduler: Hevy sync complete — activities=%d, skipped=%d, merged=%d",
                result.activities_created,
                result.activities_skipped,
                merge_result.merged,
            )
        except Exception:
            logger.exception("Scheduler: Hevy sync failed")


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
            insight = await engine.generate_daily_briefing(session, USER_ID)
            logger.info("Scheduler: daily briefing generated")

            if await _get_user_email_pref("email_daily_briefing"):
                content = json.loads(insight.content)
                send_daily_briefing(content)
                logger.info("Scheduler: daily briefing email sent")
        except ValueError as e:
            logger.info("Scheduler: daily briefing skipped — %s", e)
        except Exception:
            logger.exception("Scheduler: daily briefing failed")



def job_weekly_plan() -> None:
    """Generate the weekly training plan (runs Sunday evening for next week)."""
    logger.info("Scheduler: generating weekly plan")
    _run_async(_weekly_plan())


async def _weekly_plan() -> None:
    engine = CoachingEngine()
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = today + timedelta(days=days_until_monday)

    async with async_session() as session:
        try:
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
    last_monday = today - timedelta(days=today.weekday() + 7)

    async with async_session() as session:
        try:
            insight = await engine.generate_weekly_recap(session, USER_ID, last_monday)
            logger.info("Scheduler: weekly recap generated for week of %s", last_monday)

            if await _get_user_email_pref("email_weekly_recap"):
                content = json.loads(insight.content)
                send_weekly_recap(content, week_start=str(last_monday))
                logger.info("Scheduler: weekly recap email sent")
        except ValueError as e:
            logger.info("Scheduler: weekly recap skipped — %s", e)
        except Exception:
            logger.exception("Scheduler: weekly recap generation failed")


def job_post_workout_analysis() -> None:
    """Analyze recent activities that don't have a post-workout insight yet.

    Runs after Garmin sync. Finds activities from the last 2 days without
    an existing CoachingInsight, generates analysis for each, and sends email.
    """
    logger.info("Scheduler: starting post-workout analysis scan")
    _run_async(_post_workout_analysis())


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
            logger.info("Scheduler: no new activities to analyze")
            return

        logger.info("Scheduler: found %d activities to analyze", len(activities))
        send_email = await _get_user_email_pref("email_post_workout")

        for activity in activities:
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
            except ValueError as e:
                logger.info(
                    "Scheduler: post-workout analysis skipped for activity %d — %s",
                    activity.id,
                    e,
                )
            except Exception:
                logger.exception(
                    "Scheduler: post-workout analysis failed for activity %d", activity.id
                )
