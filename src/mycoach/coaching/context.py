"""Database queries that gather context data for prompt building."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.prompt_builder import activity_to_dict, snapshot_to_dict
from mycoach.models.activity import Activity
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.health import DailyHealthSnapshot


async def get_today_health(
    session: AsyncSession, user_id: int, today: date | None = None
) -> dict[str, Any]:
    """Get today's health snapshot as a dict (empty dict if none)."""
    today = today or date.today()
    stmt = select(DailyHealthSnapshot).where(
        DailyHealthSnapshot.user_id == user_id,
        DailyHealthSnapshot.snapshot_date == today,
    )
    result = await session.execute(stmt)
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        return {}
    return snapshot_to_dict(snapshot)


async def get_health_trends(
    session: AsyncSession, user_id: int, days: int = 3, today: date | None = None
) -> list[dict[str, Any]]:
    """Get recent health snapshots (excluding today) as a list of dicts."""
    today = today or date.today()
    since = today - timedelta(days=days)
    stmt = (
        select(DailyHealthSnapshot)
        .where(
            DailyHealthSnapshot.user_id == user_id,
            DailyHealthSnapshot.snapshot_date >= since,
            DailyHealthSnapshot.snapshot_date < today,
        )
        .order_by(DailyHealthSnapshot.snapshot_date.desc())
    )
    result = await session.execute(stmt)
    return [snapshot_to_dict(s) for s in result.scalars().all()]


async def get_recent_activities(
    session: AsyncSession, user_id: int, days: int = 3, today: date | None = None
) -> list[dict[str, Any]]:
    """Get recent activities as a list of dicts."""
    today = today or date.today()
    since = today - timedelta(days=days)
    stmt = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.start_time >= since.isoformat(),
        )
        .order_by(Activity.start_time.desc())
    )
    result = await session.execute(stmt)
    return [activity_to_dict(a) for a in result.scalars().all()]


async def get_availability_for_week(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """Get availability slots for a given week as a list of dicts."""
    stmt = (
        select(WeeklyAvailability)
        .where(
            WeeklyAvailability.user_id == user_id,
            WeeklyAvailability.week_start == week_start,
        )
        .order_by(WeeklyAvailability.day_of_week)
    )
    result = await session.execute(stmt)
    slots = result.scalars().all()
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return [
        {
            "day_of_week": s.day_of_week,
            "day_name": day_names[s.day_of_week],
            "start_time": str(s.start_time),
            "duration_minutes": s.duration_minutes,
            "preferred_sport": s.preferred_sport,
        }
        for s in slots
    ]
