"""Tests for scheduler configuration and job registration."""

from mycoach.config import Settings
from mycoach.scheduler.scheduler import create_scheduler


def test_create_scheduler_registers_all_jobs() -> None:
    """Scheduler should register all pipeline jobs, including the Hevy keep-alive."""
    settings = Settings(
        scheduler_timezone="UTC",
        scheduler_sync_hour=6,
        scheduler_sync_minute=0,
        scheduler_briefing_hour=6,
        scheduler_briefing_minute=30,
        scheduler_post_workout_hour=7,
        scheduler_post_workout_minute=0,
        scheduler_weekly_plan_day="sun",
        scheduler_weekly_plan_hour=18,
    )
    scheduler = create_scheduler(settings)

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {
        "hevy_sync",
        "hevy_keepalive",
        "garmin_sync",
        "daily_briefing",
        "post_workout_analysis",
        "weekly_plan",
        "weekly_recap",
    }


def test_create_scheduler_keepalive_disabled() -> None:
    """Setting the keep-alive interval to 0 omits the keep-alive job."""
    settings = Settings(scheduler_timezone="UTC", scheduler_hevy_keepalive_minutes=0)
    scheduler = create_scheduler(settings)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "hevy_keepalive" not in job_ids


def test_create_scheduler_uses_configured_timezone() -> None:
    """Scheduler should use the timezone from settings."""
    settings = Settings(scheduler_timezone="America/New_York")
    scheduler = create_scheduler(settings)
    assert str(scheduler.timezone) == "America/New_York"


def test_create_scheduler_default_timezone() -> None:
    """Scheduler should default to Europe/London."""
    settings = Settings()
    scheduler = create_scheduler(settings)
    assert str(scheduler.timezone) == "Europe/London"


def test_weekly_plan_day_configurable() -> None:
    """Weekly plan job should respect the configured day."""
    settings = Settings(scheduler_weekly_plan_day="sat", scheduler_weekly_plan_hour=20)
    scheduler = create_scheduler(settings)
    plan_job = scheduler.get_job("weekly_plan")
    assert plan_job is not None
    trigger_str = str(plan_job.trigger)
    assert "sat" in trigger_str


def test_scheduler_not_started() -> None:
    """create_scheduler should not start the scheduler automatically."""
    settings = Settings()
    scheduler = create_scheduler(settings)
    assert not scheduler.running
