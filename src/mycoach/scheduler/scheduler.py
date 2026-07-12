"""APScheduler setup — configures and manages the background scheduler.

The scheduler runs six jobs as part of the daily coaching pipeline:
0. Hevy sync (default 5:30 AM) — fetch gym workouts from Hevy API
1. Garmin sync (default 6:00 AM) — fetch health + activity data
2. Daily briefing (default 6:30 AM) — LLM-generated coaching for the day
3. Post-workout analysis (default 7:00 AM) — analyze new activities after sync
4. Weekly plan (default Sunday 6:00 PM) — generate next week's training plan
5. Weekly recap (default Monday 7:00 AM) — recap the previous week
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]

from mycoach.config import Settings
from mycoach.scheduler.jobs import (
    job_daily_briefing,
    job_garmin_sync,
    job_hevy_keepalive,
    job_hevy_sync,
    job_post_workout_analysis,
    job_weekly_plan,
    job_weekly_recap,
)

logger = logging.getLogger(__name__)

# Day name → APScheduler day-of-week string
DAY_MAP = {
    "mon": "mon",
    "tue": "tue",
    "wed": "wed",
    "thu": "thu",
    "fri": "fri",
    "sat": "sat",
    "sun": "sun",
}


def create_scheduler(settings: Settings) -> BackgroundScheduler:
    """Create and configure the background scheduler with all coaching pipeline jobs.

    Jobs are not started — call scheduler.start() to begin execution.
    """
    scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)

    # 0. Hevy sync — daily, before Garmin so merge picks up new Hevy data
    scheduler.add_job(
        job_hevy_sync,
        "cron",
        id="hevy_sync",
        hour=settings.scheduler_hevy_sync_hour,
        minute=settings.scheduler_hevy_sync_minute,
        misfire_grace_time=3600,
        replace_existing=True,
    )

    # 0b. Hevy token keep-alive — refresh the pair every N min so the ~15-min
    # access-token chain never lapses between daily syncs.
    if settings.scheduler_hevy_keepalive_minutes > 0:
        scheduler.add_job(
            job_hevy_keepalive,
            "interval",
            id="hevy_keepalive",
            minutes=settings.scheduler_hevy_keepalive_minutes,
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

    # 1. Garmin sync — daily (fetches health + activities, then auto-merges)
    scheduler.add_job(
        job_garmin_sync,
        "cron",
        id="garmin_sync",
        hour=settings.scheduler_sync_hour,
        minute=settings.scheduler_sync_minute,
        misfire_grace_time=3600,
        replace_existing=True,
    )

    # 2. Daily briefing — daily, after Garmin sync
    scheduler.add_job(
        job_daily_briefing,
        "cron",
        id="daily_briefing",
        hour=settings.scheduler_briefing_hour,
        minute=settings.scheduler_briefing_minute,
        misfire_grace_time=3600,
        replace_existing=True,
    )

    # 3. Post-workout analysis — daily, after briefing (analyzes new activities)
    scheduler.add_job(
        job_post_workout_analysis,
        "cron",
        id="post_workout_analysis",
        hour=settings.scheduler_post_workout_hour,
        minute=settings.scheduler_post_workout_minute,
        misfire_grace_time=3600,
        replace_existing=True,
    )

    # 4. Weekly plan — once per week (default Sunday evening)
    plan_day = DAY_MAP.get(settings.scheduler_weekly_plan_day.lower(), "sun")
    scheduler.add_job(
        job_weekly_plan,
        "cron",
        id="weekly_plan",
        day_of_week=plan_day,
        hour=settings.scheduler_weekly_plan_hour,
        minute=0,
        misfire_grace_time=3600,
        replace_existing=True,
    )

    # 5. Weekly recap — Monday morning (after the week ends)
    scheduler.add_job(
        job_weekly_recap,
        "cron",
        id="weekly_recap",
        day_of_week="mon",
        hour=7,
        minute=0,
        misfire_grace_time=3600,
        replace_existing=True,
    )

    logger.info(
        "Scheduler configured: hevy=%02d:%02d, sync=%02d:%02d, briefing=%02d:%02d, "
        "post_workout=%02d:%02d, plan=%s@%02d:00, recap=mon@07:00, tz=%s",
        settings.scheduler_hevy_sync_hour,
        settings.scheduler_hevy_sync_minute,
        settings.scheduler_sync_hour,
        settings.scheduler_sync_minute,
        settings.scheduler_briefing_hour,
        settings.scheduler_briefing_minute,
        settings.scheduler_post_workout_hour,
        settings.scheduler_post_workout_minute,
        plan_day,
        settings.scheduler_weekly_plan_hour,
        settings.scheduler_timezone,
    )

    return scheduler
