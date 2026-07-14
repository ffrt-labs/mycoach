"""Tests for scheduler configuration and job registration."""

from mycoach.config import Settings
from mycoach.scheduler.scheduler import create_scheduler


def test_create_scheduler_registers_all_jobs() -> None:
    """Scheduler should register all coaching-pipeline jobs.

    Gym workouts arrive via CSV import / the offline logger push endpoint, so
    there is no scheduled gym-sync job.
    """
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
        "garmin_sync",
        "daily_briefing",
        "post_workout_analysis",
        "weekly_plan",
        "weekly_recap",
    }


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


def test_weekly_plan_minute_configurable() -> None:
    """Weekly plan job should respect the configured minute."""
    settings = Settings(scheduler_weekly_plan_minute=30)
    scheduler = create_scheduler(settings)
    plan_job = scheduler.get_job("weekly_plan")
    assert plan_job is not None
    assert "minute='30'" in str(plan_job.trigger)


def test_weekly_recap_day_configurable() -> None:
    """Weekly recap job should respect the configured day."""
    settings = Settings(scheduler_weekly_recap_day="sun")
    scheduler = create_scheduler(settings)
    recap_job = scheduler.get_job("weekly_recap")
    assert recap_job is not None
    assert "sun" in str(recap_job.trigger)


def test_weekly_recap_time_configurable() -> None:
    """Weekly recap job should respect the configured hour and minute."""
    settings = Settings(scheduler_weekly_recap_hour=9, scheduler_weekly_recap_minute=15)
    scheduler = create_scheduler(settings)
    recap_job = scheduler.get_job("weekly_recap")
    assert recap_job is not None
    trigger_str = str(recap_job.trigger)
    assert "hour='9'" in trigger_str
    assert "minute='15'" in trigger_str


def test_scheduler_not_started() -> None:
    """create_scheduler should not start the scheduler automatically."""
    settings = Settings()
    scheduler = create_scheduler(settings)
    assert not scheduler.running
