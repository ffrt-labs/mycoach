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
from mycoach.models.routine import RoutineDay, WorkoutRoutine
from mycoach.models.sport_profile import SportProfile


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
            "sport": s.sport,
        }
        for s in slots
    ]


async def get_mesocycle_context(session: AsyncSession, user_id: int) -> str | None:
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



async def get_plan_adherence_for_week(
    session: AsyncSession, user_id: int, week_start: date
) -> dict[str, Any] | None:
    """Get plan adherence data for a given week.

    Returns a dict with total_sessions, completed_sessions, adherence_pct,
    and per-session breakdown. Returns None if no plan exists for the week.
    """
    stmt = (
        select(WeeklyPlan)
        .where(
            WeeklyPlan.user_id == user_id,
            WeeklyPlan.week_start == week_start,
        )
        .order_by(WeeklyPlan.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    plan = result.scalar_one_or_none()
    if plan is None:
        return None

    sessions_stmt = (
        select(PlannedSession)
        .where(PlannedSession.plan_id == plan.id)
        .order_by(PlannedSession.day_of_week)
    )
    sessions_result = await session.execute(sessions_stmt)
    sessions = sessions_result.scalars().all()

    total = len(sessions)
    completed = sum(1 for s in sessions if s.completed)
    adherence_pct = round(completed / total * 100, 1) if total > 0 else 0.0

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    session_list = [
        {
            "day": day_names[s.day_of_week],
            "sport": s.sport,
            "title": s.title,
            "completed": s.completed,
        }
        for s in sessions
    ]

    return {
        "plan_summary": plan.summary,
        "total_sessions": total,
        "completed_sessions": completed,
        "adherence_pct": adherence_pct,
        "sessions": session_list,
    }


async def get_activities_for_week(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """Get all activities within a specific week (Monday to Sunday)."""
    week_end = week_start + timedelta(days=7)
    stmt = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.start_time >= week_start.isoformat(),
            Activity.start_time < week_end.isoformat(),
        )
        .order_by(Activity.start_time)
    )
    result = await session.execute(stmt)
    return [activity_to_dict(a) for a in result.scalars().all()]


async def get_activity_with_details(
    session: AsyncSession, activity_id: int, user_id: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Get an activity and its gym workout details as dicts.

    Returns (activity_dict, gym_details_list). gym_details_list is empty for non-gym activities.
    Raises ValueError if activity not found.
    """
    stmt = select(Activity).where(Activity.id == activity_id, Activity.user_id == user_id)
    result = await session.execute(stmt)
    activity = result.scalar_one_or_none()
    if activity is None:
        raise ValueError(f"Activity {activity_id} not found")

    activity_dict = activity_to_dict(activity)
    activity_dict["id"] = activity.id

    gym_details: list[dict[str, Any]] = []
    if activity.sport == "gym":
        detail_stmt = (
            select(GymWorkoutDetail)
            .where(GymWorkoutDetail.activity_id == activity_id)
            .order_by(GymWorkoutDetail.set_index)
        )
        detail_result = await session.execute(detail_stmt)
        for d in detail_result.scalars().all():
            gym_details.append(
                {
                    "exercise_title": d.exercise_title,
                    "set_index": d.set_index,
                    "set_type": d.set_type,
                    "weight_kg": d.weight_kg,
                    "reps": d.reps,
                    "rpe": d.rpe,
                    "distance_meters": d.distance_meters,
                    "duration_seconds": d.duration_seconds,
                    "superset_id": d.superset_id,
                }
            )

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


async def get_active_routine(session: AsyncSession, user_id: int) -> dict[str, Any] | None:
    """Get the active workout routine as a nested dict, or None if no active routine."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(WorkoutRoutine)
        .where(WorkoutRoutine.user_id == user_id, WorkoutRoutine.is_active.is_(True))
        .options(selectinload(WorkoutRoutine.days).selectinload(RoutineDay.exercises))
    )
    result = await session.execute(stmt)
    routine = result.scalar_one_or_none()
    if routine is None:
        return None

    return {
        "id": routine.id,
        "name": routine.name,
        "days": [
            {
                "id": day.id,
                "name": day.name,
                "day_of_week": day.day_of_week,
                "order_index": day.order_index,
                "exercises": [
                    {
                        "exercise_name": ex.exercise_name,
                        "sets": ex.sets,
                        "rep_range": ex.rep_range,
                        "notes": ex.notes,
                        "superset_group": ex.superset_group,
                    }
                    for ex in day.exercises
                ],
            }
            for day in routine.days
        ],
    }


async def get_last_week_gym_performance(
    session: AsyncSession,
    user_id: int,
    exercise_names: list[str],
    week_start: date,
) -> list[dict[str, Any]]:
    """Get last week's gym workout details for specific exercises.

    Returns a list of dicts with exercise_title, set_index, weight_kg, reps, rpe.
    """
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = week_start

    # Find gym activities from previous week
    stmt = select(Activity).where(
        Activity.user_id == user_id,
        Activity.sport == "gym",
        Activity.start_time >= prev_week_start.isoformat(),
        Activity.start_time < prev_week_end.isoformat(),
    )
    result = await session.execute(stmt)
    activity_ids = [a.id for a in result.scalars().all()]

    if not activity_ids:
        return []

    # Get gym details for those activities, filtered by exercise names
    detail_stmt = (
        select(GymWorkoutDetail)
        .where(
            GymWorkoutDetail.activity_id.in_(activity_ids),
            GymWorkoutDetail.exercise_title.in_(exercise_names),
        )
        .order_by(GymWorkoutDetail.exercise_title, GymWorkoutDetail.set_index)
    )
    detail_result = await session.execute(detail_stmt)

    return [
        {
            "exercise_title": d.exercise_title,
            "set_index": d.set_index,
            "weight_kg": d.weight_kg,
            "reps": d.reps,
            "rpe": d.rpe,
        }
        for d in detail_result.scalars().all()
    ]


async def get_last_week_all_activities(
    session: AsyncSession,
    user_id: int,
    week_start: date,
) -> list[dict[str, Any]]:
    """Get ALL activities from the previous week (all sports), with gym details nested.

    Returns list of activity dicts. Gym activities include a 'gym_details' key
    with set-level data.
    """
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = week_start

    stmt = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.start_time >= prev_week_start.isoformat(),
            Activity.start_time < prev_week_end.isoformat(),
        )
        .order_by(Activity.start_time)
    )
    result = await session.execute(stmt)
    activities = result.scalars().all()

    output: list[dict[str, Any]] = []
    for a in activities:
        d = activity_to_dict(a)
        if a.sport == "gym":
            detail_stmt = (
                select(GymWorkoutDetail)
                .where(GymWorkoutDetail.activity_id == a.id)
                .order_by(GymWorkoutDetail.exercise_title, GymWorkoutDetail.set_index)
            )
            detail_result = await session.execute(detail_stmt)
            d["gym_details"] = [
                {
                    "exercise_title": det.exercise_title,
                    "set_index": det.set_index,
                    "weight_kg": det.weight_kg,
                    "reps": det.reps,
                    "rpe": det.rpe,
                }
                for det in detail_result.scalars().all()
            ]
        output.append(d)
    return output


async def get_health_trends_averaged(
    session: AsyncSession, user_id: int, days: int = 7, today: date | None = None
) -> dict[str, Any]:
    """Get a 7-day averaged health summary instead of individual daily snapshots.

    Computes averages for numeric fields, keeps latest value for text/status fields.
    Returns a single dict with averaged metrics.
    """
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
    snapshots = result.scalars().all()

    if not snapshots:
        return {}

    numeric_fields = [
        "resting_hr", "avg_hr", "hrv_status", "sleep_duration_minutes",
        "sleep_score", "avg_stress", "training_readiness", "training_load",
        "body_battery_morning",
    ]
    text_fields = ["training_status", "hrv_status_text", "load_focus"]
    static_fields = ["vo2_max"]

    averaged: dict[str, Any] = {"days_with_data": len(snapshots)}

    # Compute averages for numeric fields
    for field in numeric_fields:
        values = [getattr(s, field) for s in snapshots if getattr(s, field, None) is not None]
        if values:
            averaged[field] = round(sum(values) / len(values), 1)

    # Latest value for text/status fields (snapshots ordered desc, so [0] is latest)
    latest = snapshots[0]
    for field in text_fields:
        val = getattr(latest, field, None)
        if val is not None:
            averaged[field] = val

    # Latest for static fields
    for field in static_fields:
        val = getattr(latest, field, None)
        if val is not None:
            averaged[field] = val

    return averaged


async def get_last_week_cardio_performance(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """Get swimming and running activities from the previous week."""
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = week_start

    stmt = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport.in_(["swimming", "running", "cardio"]),
            Activity.start_time >= prev_week_start.isoformat(),
            Activity.start_time < prev_week_end.isoformat(),
        )
        .order_by(Activity.start_time)
    )
    result = await session.execute(stmt)
    return [activity_to_dict(a) for a in result.scalars().all()]


async def get_sport_profiles(
    session: AsyncSession, user_id: int
) -> list[dict[str, Any]]:
    """Get all sport profiles for a user as a list of dicts."""
    stmt = (
        select(SportProfile)
        .where(SportProfile.user_id == user_id)
        .order_by(SportProfile.sport)
    )
    result = await session.execute(stmt)
    return [
        {
            "sport": p.sport,
            "skill_level": p.skill_level,
            "goals": p.goals,
            "preferences": p.preferences,
            "benchmarks": p.benchmarks,
        }
        for p in result.scalars().all()
    ]


async def get_today_planned_sessions(
    session: AsyncSession, user_id: int, today: date | None = None
) -> list[dict[str, Any]]:
    """Get planned sessions for today from the active weekly plan."""
    today = today or date.today()
    day_of_week = today.weekday()
    week_start = today - timedelta(days=day_of_week)

    plan_stmt = select(WeeklyPlan).where(
        WeeklyPlan.user_id == user_id,
        WeeklyPlan.week_start == week_start,
        WeeklyPlan.status == "active",
    )
    plan_result = await session.execute(plan_stmt)
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        return []

    session_stmt = (
        select(PlannedSession)
        .where(
            PlannedSession.plan_id == plan.id,
            PlannedSession.day_of_week == day_of_week,
        )
        .order_by(PlannedSession.id)
    )
    session_result = await session.execute(session_stmt)
    return [
        {
            "title": s.title,
            "sport": s.sport,
            "duration_minutes": s.duration_minutes,
            "details": s.details,
            "track": s.track,
            "notes": s.notes,
        }
        for s in session_result.scalars().all()
    ]


async def get_gym_details_for_week(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """Get all gym set/rep/weight details for every gym session in the given week.

    Returns list of dicts: {session_date, exercise_title, set_index, set_type, weight_kg, reps, rpe}
    sorted by session date, then exercise, then set index.
    """
    week_end = week_start + timedelta(days=7)
    stmt = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport == "gym",
            Activity.start_time >= week_start.isoformat(),
            Activity.start_time < week_end.isoformat(),
        )
        .order_by(Activity.start_time)
    )
    result = await session.execute(stmt)
    activities = result.scalars().all()

    rows: list[dict[str, Any]] = []
    for act in activities:
        detail_stmt = (
            select(GymWorkoutDetail)
            .where(GymWorkoutDetail.activity_id == act.id)
            .order_by(GymWorkoutDetail.exercise_title, GymWorkoutDetail.set_index)
        )
        detail_result = await session.execute(detail_stmt)
        for d in detail_result.scalars().all():
            rows.append(
                {
                    "session_date": str(act.start_time)[:10] if act.start_time else None,
                    "session_title": act.title,
                    "exercise_title": d.exercise_title,
                    "set_index": d.set_index,
                    "set_type": d.set_type,
                    "weight_kg": d.weight_kg,
                    "reps": d.reps,
                    "rpe": d.rpe,
                }
            )
    return rows


async def get_gym_performance_history(
    session: AsyncSession, user_id: int, week_start: date, weeks: int = 3
) -> list[dict[str, Any]]:
    """Get per-exercise aggregated performance for N weeks before week_start.

    Returns list of {week_start, exercise_title, best_weight_kg, best_reps, total_sets, avg_rpe},
    sorted by week_start asc, then exercise name.  Used for plateau detection.
    """
    rows: list[dict[str, Any]] = []
    for i in range(weeks, 0, -1):
        w_start = week_start - timedelta(days=7 * i)
        w_end = w_start + timedelta(days=7)

        act_stmt = select(Activity).where(
            Activity.user_id == user_id,
            Activity.sport == "gym",
            Activity.start_time >= w_start.isoformat(),
            Activity.start_time < w_end.isoformat(),
        )
        act_result = await session.execute(act_stmt)
        activity_ids = [a.id for a in act_result.scalars().all()]

        if not activity_ids:
            continue

        detail_stmt = (
            select(GymWorkoutDetail)
            .where(GymWorkoutDetail.activity_id.in_(activity_ids))
            .order_by(GymWorkoutDetail.exercise_title, GymWorkoutDetail.set_index)
        )
        detail_result = await session.execute(detail_stmt)
        details = detail_result.scalars().all()

        # Aggregate per exercise
        by_exercise: dict[str, dict[str, Any]] = {}
        for d in details:
            ex = d.exercise_title or "Unknown"
            if ex not in by_exercise:
                by_exercise[ex] = {
                    "best_weight_kg": None,
                    "best_reps": None,
                    "total_sets": 0,
                    "rpe_sum": 0.0,
                    "rpe_count": 0,
                }
            entry = by_exercise[ex]
            entry["total_sets"] += 1
            w = d.weight_kg
            r = d.reps
            if w is not None and (entry["best_weight_kg"] is None or w > entry["best_weight_kg"]):
                entry["best_weight_kg"] = w
                entry["best_reps"] = r  # reps at best weight
            if d.rpe is not None:
                entry["rpe_sum"] += d.rpe
                entry["rpe_count"] += 1

        for ex_name, agg in sorted(by_exercise.items()):
            avg_rpe = (
                round(agg["rpe_sum"] / agg["rpe_count"], 1) if agg["rpe_count"] > 0 else None
            )
            rows.append(
                {
                    "week_start": str(w_start),
                    "exercise_title": ex_name,
                    "best_weight_kg": agg["best_weight_kg"],
                    "best_reps": agg["best_reps"],
                    "total_sets": agg["total_sets"],
                    "avg_rpe": avg_rpe,
                }
            )

    return rows


async def get_recent_plan_summaries(
    session: AsyncSession,
    user_id: int,
    weeks: int = 4,
    before_date: date | None = None,
) -> list[dict[str, Any]]:
    """Get summaries of recent weekly plans for trend analysis."""
    before_date = before_date or date.today()
    stmt = (
        select(WeeklyPlan)
        .where(
            WeeklyPlan.user_id == user_id,
            WeeklyPlan.week_start < before_date,
        )
        .order_by(WeeklyPlan.week_start.desc())
        .limit(weeks)
    )
    result = await session.execute(stmt)
    plans = result.scalars().all()

    summaries: list[dict[str, Any]] = []
    for plan in plans:
        sessions_stmt = select(PlannedSession).where(PlannedSession.plan_id == plan.id)
        sessions_result = await session.execute(sessions_stmt)
        sessions = sessions_result.scalars().all()

        total = len(sessions)
        completed = sum(1 for s in sessions if s.completed)
        adherence_pct = round(completed / total * 100, 1) if total > 0 else 0.0

        summaries.append(
            {
                "week_start": str(plan.week_start),
                "summary": plan.summary,
                "total_sessions": total,
                "completed_sessions": completed,
                "adherence_pct": adherence_pct,
            }
        )

    return summaries
