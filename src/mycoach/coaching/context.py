"""Database queries that gather context data for prompt building."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.prompt_builder import activity_to_dict, snapshot_to_dict
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.coaching import MesocycleConfig
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.plan import PlannedSession, WeeklyPlan


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


async def get_mesocycle_context(
    session: AsyncSession, user_id: int
) -> str | None:
    """Build a human-readable mesocycle context string from all configured mesocycles.

    Returns None if no mesocycles are configured.
    """
    stmt = (
        select(MesocycleConfig)
        .where(MesocycleConfig.user_id == user_id)
        .order_by(MesocycleConfig.sport)
    )
    result = await session.execute(stmt)
    configs = result.scalars().all()

    if not configs:
        return None

    parts = []
    for cfg in configs:
        is_deload = cfg.current_week >= cfg.block_length_weeks
        line = (
            f"- {cfg.sport}: Week {cfg.current_week}/{cfg.block_length_weeks} "
            f"({cfg.phase} phase, started {cfg.start_date})"
        )
        if is_deload:
            line += " — DELOAD WEEK: reduce volume ~40%, reduce intensity"
        if cfg.progression_rules:
            line += f"\n  Progression rules: {cfg.progression_rules}"
        parts.append(line)

    return "Current mesocycle status:\n" + "\n".join(parts)


async def get_activity_with_details(
    session: AsyncSession, activity_id: int, user_id: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Get an activity and its gym workout details as dicts.

    Returns (activity_dict, gym_details_list). gym_details_list is empty for non-gym activities.
    Raises ValueError if activity not found.
    """
    stmt = select(Activity).where(
        Activity.id == activity_id, Activity.user_id == user_id
    )
    result = await session.execute(stmt)
    activity = result.scalar_one_or_none()
    if activity is None:
        raise ValueError(f"Activity {activity_id} not found")

    activity_dict = activity_to_dict(activity)
    activity_dict["id"] = activity.id
    activity_dict["data_source"] = activity.data_source
    activity_dict["end_time"] = str(activity.end_time) if activity.end_time else None
    activity_dict["hr_zones"] = activity.hr_zones
    activity_dict["training_effect_anaerobic"] = activity.training_effect_anaerobic

    gym_details: list[dict[str, Any]] = []
    if activity.sport == "gym":
        detail_stmt = (
            select(GymWorkoutDetail)
            .where(GymWorkoutDetail.activity_id == activity_id)
            .order_by(GymWorkoutDetail.set_index)
        )
        detail_result = await session.execute(detail_stmt)
        for d in detail_result.scalars().all():
            gym_details.append({
                "exercise_title": d.exercise_title,
                "set_index": d.set_index,
                "set_type": d.set_type,
                "weight_kg": d.weight_kg,
                "reps": d.reps,
                "rpe": d.rpe,
                "distance_meters": d.distance_meters,
                "duration_seconds": d.duration_seconds,
                "superset_id": d.superset_id,
            })

    return activity_dict, gym_details


async def find_matching_planned_session(
    session: AsyncSession, activity: dict[str, Any], user_id: int
) -> dict[str, Any] | None:
    """Find the planned session that matches this activity by date + sport.

    Looks for an active weekly plan covering the activity date, then finds
    a session matching the day of week and sport.
    """
    start_time_str = activity.get("start_time")
    if not start_time_str:
        return None

    from datetime import datetime as dt

    try:
        activity_dt = dt.fromisoformat(start_time_str)
    except (ValueError, TypeError):
        return None

    activity_date = activity_dt.date()
    day_of_week = activity_date.weekday()  # 0=Monday

    # Find the Monday of the activity's week
    week_start = activity_date - timedelta(days=day_of_week)

    # Find active plan for that week
    plan_stmt = select(WeeklyPlan).where(
        WeeklyPlan.user_id == user_id,
        WeeklyPlan.week_start == week_start,
        WeeklyPlan.status == "active",
    )
    plan_result = await session.execute(plan_stmt)
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        return None

    # Find matching session by day_of_week and sport
    sport = activity.get("sport", "")
    session_stmt = select(PlannedSession).where(
        PlannedSession.plan_id == plan.id,
        PlannedSession.day_of_week == day_of_week,
        PlannedSession.sport == sport,
    )
    session_result = await session.execute(session_stmt)
    planned = session_result.scalar_one_or_none()
    if planned is None:
        return None

    return {
        "id": planned.id,
        "title": planned.title,
        "sport": planned.sport,
        "day_of_week": planned.day_of_week,
        "duration_minutes": planned.duration_minutes,
        "details": planned.details,
        "notes": planned.notes,
        "completed": planned.completed,
    }


async def get_similar_activities(
    session: AsyncSession, user_id: int, sport: str, exclude_id: int, limit: int = 5
) -> list[dict[str, Any]]:
    """Get recent activities of the same sport for trend comparison."""
    stmt = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport == sport,
            Activity.id != exclude_id,
        )
        .order_by(Activity.start_time.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [activity_to_dict(a) for a in result.scalars().all()]


async def link_activity_to_planned_session(
    session: AsyncSession,
    activity_id: int,
    planned_session_id: int,
) -> None:
    """Mark a planned session as completed and link it to the actual activity."""
    stmt = select(PlannedSession).where(PlannedSession.id == planned_session_id)
    result = await session.execute(stmt)
    planned = result.scalar_one_or_none()
    if planned is not None:
        planned.completed = True
        planned.activity_id = activity_id
        await session.flush()
