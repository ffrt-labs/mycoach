"""Reads of the Observed athlete record (map #85, ticket #186, design #111).

Plain-dict return shapes, never an ORM object, never an internal row id, never
prompt-shaped data. ``external_id`` is how an activity is named across the boundary.
Dates and datetimes cross as strings (``str()`` of the value), so every result survives
serialisation: swapping this module's implementation for HTTP changes no caller.

Health side: must never import coaching.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.exercise_catalogue import load_exercise_catalogue
from mycoach.health.runtime import health_session
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.source_record import CanonicalActivitySource, SourceRecord

_SNAPSHOT_FIELDS = [
    "snapshot_date",
    "resting_hr",
    "max_hr",
    "avg_hr",
    "hrv_status",
    "hrv_7day_avg",
    "sleep_duration_minutes",
    "sleep_score",
    "sleep_deep_minutes",
    "sleep_light_minutes",
    "sleep_rem_minutes",
    "avg_stress",
    "training_readiness",
    "training_load",
    "training_status",
    "vo2_max",
    "steps",
    "respiration_avg",
    "spo2_avg",
    "intensity_minutes",
    "sleep_awake_minutes",
    "recovery_time_hours",
    "load_focus",
    "body_battery_morning",
    "hrv_status_text",
]
_SNAPSHOT_EXTRA_FIELDS = ["body_battery_high", "body_battery_low"]


def _snapshot(snapshot: DailyHealthSnapshot) -> dict[str, Any]:
    """A snapshot as a dict, omitting fields with no value."""
    result: dict[str, Any] = {}
    for name in _SNAPSHOT_FIELDS:
        value = getattr(snapshot, name, None)
        if value is not None:
            result[name] = str(value) if isinstance(value, date) else value
    return result


def _snapshot_full(snapshot: DailyHealthSnapshot) -> dict[str, Any]:
    """Every snapshot column (the API and dashboard shape), dates as strings."""
    out = _snapshot(snapshot)
    out["snapshot_date"] = str(snapshot.snapshot_date)
    out["data_source"] = snapshot.data_source
    out["created_at"] = str(snapshot.created_at)
    out["updated_at"] = str(snapshot.updated_at) if snapshot.updated_at else None
    for name in _SNAPSHOT_EXTRA_FIELDS:
        out[name] = getattr(snapshot, name, None)
    for name in [*_SNAPSHOT_FIELDS, *_SNAPSHOT_EXTRA_FIELDS]:
        out.setdefault(name, None)
    return out


async def _sources_by_activity(
    session: AsyncSession, activity_ids: Sequence[int]
) -> dict[int, list[str]]:
    if not activity_ids:
        return {}
    rows = await session.execute(
        select(CanonicalActivitySource.canonical_activity_id, SourceRecord.source)
        .join(SourceRecord, SourceRecord.id == CanonicalActivitySource.source_record_id)
        .where(CanonicalActivitySource.canonical_activity_id.in_(activity_ids))
    )
    out: dict[int, list[str]] = {}
    for activity_id, source in rows:
        out.setdefault(activity_id, []).append(source)
    return {k: sorted(v) for k, v in out.items()}


def _activity(activity: Activity, sources: list[str]) -> dict[str, Any]:
    """A canonical activity as a dict. ``sources`` is its provenance (never a state)."""
    return {
        "external_id": activity.external_id,
        "title": activity.title,
        "sport": activity.sport,
        "start_time": str(activity.start_time) if activity.start_time else None,
        "duration_minutes": activity.duration_minutes,
        "distance_meters": activity.distance_meters,
        "avg_speed_mps": activity.avg_speed_mps,
        "avg_hr": activity.avg_hr,
        "max_hr": activity.max_hr,
        "calories": activity.calories,
        "training_effect_aerobic": activity.training_effect_aerobic,
        "end_time": str(activity.end_time) if activity.end_time else None,
        "hr_zones": activity.hr_zones,
        "training_effect_anaerobic": activity.training_effect_anaerobic,
        "epoc": activity.epoc,
        "recovery_time_minutes": activity.recovery_time_minutes,
        "avg_cadence": activity.avg_cadence,
        "avg_swolf": activity.avg_swolf,
        "moving_duration_seconds": activity.moving_duration_seconds,
        "fastest_split_100_seconds": activity.fastest_split_100_seconds,
        "avg_strokes_per_length": activity.avg_strokes_per_length,
        "notes": activity.notes,
        "sources": sources,
        "settled": activity.settled_at is not None,
        "created_at": str(activity.created_at),
    }


def _set(d: GymWorkoutDetail) -> dict[str, Any]:
    return {
        "exercise_id": d.exercise_id,
        "exercise_title": d.exercise_title,
        "set_index": d.set_index,
        "set_type": d.set_type,
        "weight_kg": d.weight_kg,
        "reps": d.reps,
        "rpe": d.rpe,
        "distance_meters": d.distance_meters,
        "duration_seconds": d.duration_seconds,
        "superset_id": d.superset_id,
        "exercise_notes": d.exercise_notes,
    }


async def _activities(session: AsyncSession, stmt: Any) -> list[dict[str, Any]]:
    rows = list((await session.execute(stmt)).scalars().all())
    sources = await _sources_by_activity(session, [a.id for a in rows])
    return [_activity(a, sources.get(a.id, [])) for a in rows]


# --- daily health ---------------------------------------------------------------------


async def get_today_health(
    athlete_id: int, today: date | None = None
) -> dict[str, Any]:
    """That day's health snapshot as a dict (empty dict if none)."""
    today = today or date.today()
    async with health_session() as session:
        snapshot = (
            await session.execute(
                select(DailyHealthSnapshot).where(
                    DailyHealthSnapshot.user_id == athlete_id,
                    DailyHealthSnapshot.snapshot_date == today,
                )
            )
        ).scalar_one_or_none()
    return _snapshot(snapshot) if snapshot is not None else {}


async def get_health_trends(
    athlete_id: int, days: int = 3, today: date | None = None
) -> list[dict[str, Any]]:
    """Recent snapshots, newest first, excluding ``today``."""
    today = today or date.today()
    since = today - timedelta(days=days)
    async with health_session() as session:
        rows = (
            await session.execute(
                select(DailyHealthSnapshot)
                .where(
                    DailyHealthSnapshot.user_id == athlete_id,
                    DailyHealthSnapshot.snapshot_date >= since,
                    DailyHealthSnapshot.snapshot_date < today,
                )
                .order_by(DailyHealthSnapshot.snapshot_date.desc())
            )
        ).scalars()
        return [_snapshot(s) for s in rows]


async def get_health_trends_averaged(
    athlete_id: int, days: int = 7, today: date | None = None
) -> dict[str, Any]:
    """A averaged summary of the recent snapshots: means for numbers, latest for statuses."""
    today = today or date.today()
    since = today - timedelta(days=days)
    async with health_session() as session:
        snapshots = list(
            (
                await session.execute(
                    select(DailyHealthSnapshot)
                    .where(
                        DailyHealthSnapshot.user_id == athlete_id,
                        DailyHealthSnapshot.snapshot_date >= since,
                        DailyHealthSnapshot.snapshot_date < today,
                    )
                    .order_by(DailyHealthSnapshot.snapshot_date.desc())
                )
            ).scalars()
        )
    if not snapshots:
        return {}

    numeric_fields = [
        "resting_hr",
        "avg_hr",
        "hrv_status",
        "sleep_duration_minutes",
        "sleep_score",
        "avg_stress",
        "training_readiness",
        "training_load",
        "body_battery_morning",
    ]
    averaged: dict[str, Any] = {"days_with_data": len(snapshots)}
    for name in numeric_fields:
        values = [getattr(s, name) for s in snapshots if getattr(s, name, None) is not None]
        if values:
            averaged[name] = round(sum(values) / len(values), 1)
    latest = snapshots[0]
    for name in ["training_status", "hrv_status_text", "load_focus", "vo2_max"]:
        value = getattr(latest, name, None)
        if value is not None:
            averaged[name] = value
    return averaged


async def get_snapshot(athlete_id: int, snapshot_date: date) -> dict[str, Any] | None:
    """One day's full snapshot (every column), or None."""
    async with health_session() as session:
        snapshot = (
            await session.execute(
                select(DailyHealthSnapshot).where(
                    DailyHealthSnapshot.user_id == athlete_id,
                    DailyHealthSnapshot.snapshot_date == snapshot_date,
                )
            )
        ).scalar_one_or_none()
    return _snapshot_full(snapshot) if snapshot is not None else None


async def list_snapshots(
    athlete_id: int, since: date, until: date | None = None, newest_first: bool = True
) -> list[dict[str, Any]]:
    """Full snapshots from ``since`` (inclusive) up to ``until`` (exclusive)."""
    async with health_session() as session:
        stmt = select(DailyHealthSnapshot).where(
            DailyHealthSnapshot.user_id == athlete_id,
            DailyHealthSnapshot.snapshot_date >= since,
        )
        if until is not None:
            stmt = stmt.where(DailyHealthSnapshot.snapshot_date < until)
        order = DailyHealthSnapshot.snapshot_date
        rows = (await session.execute(stmt.order_by(order.desc() if newest_first else order)))
        return [_snapshot_full(s) for s in rows.scalars()]


async def get_last_sync(athlete_id: int) -> dict[str, str | None]:
    """When each source last delivered data: ``{"garmin": ..., "hevy": ...}`` (ISO or None)."""
    async with health_session() as session:
        garmin_health = await session.scalar(
            select(
                func.max(
                    func.coalesce(DailyHealthSnapshot.updated_at, DailyHealthSnapshot.created_at)
                )
            ).where(
                DailyHealthSnapshot.user_id == athlete_id,
                DailyHealthSnapshot.data_source == "garmin",
            )
        )
        received: dict[str, datetime | None] = {}
        for source in ("garmin", "hevy"):
            received[source] = await session.scalar(
                select(func.max(SourceRecord.received_at)).where(
                    SourceRecord.athlete_id == athlete_id, SourceRecord.source == source
                )
            )
    garmin_times = [t for t in (garmin_health, received["garmin"]) if t is not None]
    return {
        "garmin": str(max(garmin_times)) if garmin_times else None,
        "garmin_snapshots": str(garmin_health) if garmin_health else None,
        "hevy": str(received["hevy"]) if received["hevy"] else None,
    }


# --- activities -----------------------------------------------------------------------


async def get_recent_activities(
    athlete_id: int, days: int = 3, today: date | None = None
) -> list[dict[str, Any]]:
    today = today or date.today()
    since = today - timedelta(days=days)
    async with health_session() as session:
        return await _activities(
            session,
            select(Activity)
            .where(Activity.user_id == athlete_id, Activity.start_time >= since)
            .order_by(Activity.start_time.desc()),
        )


async def get_activities_for_week(athlete_id: int, week_start: date) -> list[dict[str, Any]]:
    week_end = week_start + timedelta(days=7)
    async with health_session() as session:
        return await _activities(
            session,
            select(Activity)
            .where(
                Activity.user_id == athlete_id,
                Activity.start_time >= week_start,
                Activity.start_time < week_end,
            )
            .order_by(Activity.start_time),
        )


async def get_activity_with_details(
    athlete_id: int, activity_external_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """An activity and its performed sets. Raises ValueError if it is not found."""
    async with health_session() as session:
        activity = (
            await session.execute(
                select(Activity).where(
                    Activity.external_id == activity_external_id, Activity.user_id == athlete_id
                )
            )
        ).scalar_one_or_none()
        if activity is None:
            raise ValueError(f"Activity {activity_external_id} not found")
        sources = await _sources_by_activity(session, [activity.id])
        sets: list[dict[str, Any]] = []
        if activity.sport == "gym":
            rows = (
                await session.execute(
                    select(GymWorkoutDetail)
                    .where(GymWorkoutDetail.activity_id == activity.id)
                    .order_by(GymWorkoutDetail.set_index)
                )
            ).scalars()
            sets = [_set(d) for d in rows]
        return _activity(activity, sources.get(activity.id, [])), sets


async def has_performed_sets(athlete_id: int, activity_external_id: str) -> bool:
    """Whether the activity has at least one performed set (the claim guard, #129)."""
    async with health_session() as session:
        found = (
            await session.execute(
                select(GymWorkoutDetail.id)
                .join(Activity, Activity.id == GymWorkoutDetail.activity_id)
                .where(
                    Activity.external_id == activity_external_id, Activity.user_id == athlete_id
                )
                .limit(1)
            )
        ).first()
    return found is not None


async def get_activity_facts(
    athlete_id: int, week_start: date, extra_external_ids: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Who-did-what for a week's plan adherence: the week's activities plus ``extra``.

    Each row: ``external_id``, ``sport``, ``sources``, ``has_performed_sets``, ``in_week``.
    """
    week_end = week_start + timedelta(days=7)
    async with health_session() as session:
        stmt = select(Activity).where(
            Activity.user_id == athlete_id,
            (
                (Activity.start_time >= week_start) & (Activity.start_time < week_end)
                | Activity.external_id.in_(list(extra_external_ids))
            ),
        )
        rows = list((await session.execute(stmt)).scalars().all())
        sources = await _sources_by_activity(session, [a.id for a in rows])
        with_sets = set(
            (
                await session.execute(
                    select(GymWorkoutDetail.activity_id).where(
                        GymWorkoutDetail.activity_id.in_([a.id for a in rows])
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "external_id": a.external_id,
                "sport": a.sport,
                "sources": sources.get(a.id, []),
                "has_performed_sets": a.id in with_sets,
                "in_week": week_start <= a.start_time.date() < week_end,
            }
            for a in rows
        ]


async def get_similar_activities(
    athlete_id: int,
    sport: str,
    exclude_external_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Recent activities of the same sport, for trend comparison."""
    async with health_session() as session:
        return await _activities(
            session,
            select(Activity)
            .where(
                Activity.user_id == athlete_id,
                Activity.sport == sport,
                Activity.external_id != exclude_external_id,
            )
            .order_by(Activity.start_time.desc())
            .limit(limit),
        )


async def get_last_week_gym_performance(
    athlete_id: int, exercise_ids: list[str], week_start: date
) -> list[dict[str, Any]]:
    """The previous week's performed sets for the given catalogue exercises."""
    start, end = week_start - timedelta(days=7), week_start
    async with health_session() as session:
        ids = (
            (
                await session.execute(
                    select(Activity.id).where(
                        Activity.user_id == athlete_id,
                        Activity.sport == "gym",
                        Activity.start_time >= start,
                        Activity.start_time < end,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return []
        rows = (
            await session.execute(
                select(GymWorkoutDetail)
                .where(
                    GymWorkoutDetail.activity_id.in_(ids),
                    GymWorkoutDetail.exercise_id.in_(exercise_ids),
                )
                .order_by(GymWorkoutDetail.exercise_id, GymWorkoutDetail.set_index)
            )
        ).scalars()
        return [
            {
                "exercise_id": d.exercise_id,
                "exercise_title": d.exercise_title,
                "set_index": d.set_index,
                "weight_kg": d.weight_kg,
                "reps": d.reps,
                "rpe": d.rpe,
            }
            for d in rows
        ]


async def get_last_week_all_activities(athlete_id: int, week_start: date) -> list[dict[str, Any]]:
    """Every activity of the previous week; gym ones carry ``gym_details`` (performed sets)."""
    start, end = week_start - timedelta(days=7), week_start
    async with health_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Activity)
                    .where(
                        Activity.user_id == athlete_id,
                        Activity.start_time >= start,
                        Activity.start_time < end,
                    )
                    .order_by(Activity.start_time)
                )
            )
            .scalars()
            .all()
        )
        sources = await _sources_by_activity(session, [a.id for a in rows])
        out: list[dict[str, Any]] = []
        for a in rows:
            d = _activity(a, sources.get(a.id, []))
            if a.sport == "gym":
                details = (
                    await session.execute(
                        select(GymWorkoutDetail)
                        .where(GymWorkoutDetail.activity_id == a.id)
                        .order_by(GymWorkoutDetail.exercise_id, GymWorkoutDetail.set_index)
                    )
                ).scalars()
                d["gym_details"] = [_set(det) for det in details]
            out.append(d)
        return out


async def get_last_week_cardio_performance(
    athlete_id: int, week_start: date
) -> list[dict[str, Any]]:
    start, end = week_start - timedelta(days=7), week_start
    async with health_session() as session:
        return await _activities(
            session,
            select(Activity)
            .where(
                Activity.user_id == athlete_id,
                Activity.sport.in_(["swimming", "running", "padel", "cardio"]),
                Activity.start_time >= start,
                Activity.start_time < end,
            )
            .order_by(Activity.start_time),
        )


async def get_gym_details_for_week(athlete_id: int, week_start: date) -> list[dict[str, Any]]:
    """Every performed set of the week's gym sessions, by date, exercise, set index."""
    week_end = week_start + timedelta(days=7)
    async with health_session() as session:
        activities = (
            (
                await session.execute(
                    select(Activity)
                    .where(
                        Activity.user_id == athlete_id,
                        Activity.sport == "gym",
                        Activity.start_time >= week_start,
                        Activity.start_time < week_end,
                    )
                    .order_by(Activity.start_time)
                )
            )
            .scalars()
            .all()
        )
        rows: list[dict[str, Any]] = []
        for act in activities:
            details = (
                await session.execute(
                    select(GymWorkoutDetail)
                    .where(GymWorkoutDetail.activity_id == act.id)
                    .order_by(GymWorkoutDetail.exercise_id, GymWorkoutDetail.set_index)
                )
            ).scalars()
            for d in details:
                rows.append(
                    {
                        "session_date": str(act.start_time)[:10] if act.start_time else None,
                        "session_title": act.title,
                        "exercise_id": d.exercise_id,
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
    athlete_id: int, week_start: date, weeks: int = 3
) -> list[dict[str, Any]]:
    """Per-exercise performance aggregated by week, for ``weeks`` weeks before ``week_start``.

    Aggregation lives here, not in each consumer (decision on #111). Catalogue exercises only:
    a custom (null-id) exercise does not take part in cross-week reasoning.
    """
    rows: list[dict[str, Any]] = []
    async with health_session() as session:
        for i in range(weeks, 0, -1):
            w_start = week_start - timedelta(days=7 * i)
            w_end = w_start + timedelta(days=7)
            ids = (
                (
                    await session.execute(
                        select(Activity.id).where(
                            Activity.user_id == athlete_id,
                            Activity.sport == "gym",
                            Activity.start_time >= w_start,
                            Activity.start_time < w_end,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                continue
            details = (
                await session.execute(
                    select(GymWorkoutDetail)
                    .where(GymWorkoutDetail.activity_id.in_(ids))
                    .order_by(GymWorkoutDetail.exercise_id, GymWorkoutDetail.set_index)
                )
            ).scalars()

            by_exercise: dict[str, dict[str, Any]] = {}
            for d in details:
                if d.exercise_id is None:
                    continue
                entry = by_exercise.setdefault(
                    d.exercise_id,
                    {
                        "exercise_title": d.exercise_title,
                        "best_weight_kg": None,
                        "best_reps": None,
                        "total_sets": 0,
                        "rpe_sum": 0.0,
                        "rpe_count": 0,
                    },
                )
                entry["total_sets"] += 1
                w = d.weight_kg
                if w is not None and (
                    entry["best_weight_kg"] is None or w > entry["best_weight_kg"]
                ):
                    entry["best_weight_kg"] = w
                    entry["best_reps"] = d.reps
                if d.rpe is not None:
                    entry["rpe_sum"] += d.rpe
                    entry["rpe_count"] += 1

            for exercise_id, agg in sorted(by_exercise.items()):
                avg_rpe = (
                    round(agg["rpe_sum"] / agg["rpe_count"], 1) if agg["rpe_count"] > 0 else None
                )
                rows.append(
                    {
                        "week_start": str(w_start),
                        "exercise_id": exercise_id,
                        "exercise_title": agg["exercise_title"],
                        "best_weight_kg": agg["best_weight_kg"],
                        "best_reps": agg["best_reps"],
                        "total_sets": agg["total_sets"],
                        "avg_rpe": avg_rpe,
                    }
                )
    return rows


async def list_activities(
    athlete_id: int, sport: str | None = None, page: int = 1, per_page: int = 20
) -> dict[str, Any]:
    """A page of activities, newest first: ``{"items": [...], "total": n}``.

    Gym items carry ``gym_details`` (their performed sets).
    """
    async with health_session() as session:
        where = [Activity.user_id == athlete_id]
        if sport:
            where.append(Activity.sport == sport)
        total = (await session.execute(select(func.count(Activity.id)).where(*where))).scalar()
        rows = list(
            (
                await session.execute(
                    select(Activity)
                    .where(*where)
                    .order_by(Activity.start_time.desc(), Activity.id.desc())
                    .offset((page - 1) * per_page)
                    .limit(per_page)
                )
            )
            .scalars()
            .all()
        )
        sources = await _sources_by_activity(session, [a.id for a in rows])
        gym_ids = [a.id for a in rows if a.sport == "gym"]
        by_activity: dict[int, list[dict[str, Any]]] = {}
        if gym_ids:
            details = (
                await session.execute(
                    select(GymWorkoutDetail)
                    .where(GymWorkoutDetail.activity_id.in_(gym_ids))
                    .order_by(GymWorkoutDetail.activity_id, GymWorkoutDetail.set_index)
                )
            ).scalars()
            for d in details:
                by_activity.setdefault(d.activity_id, []).append(_set(d))
        items = []
        for a in rows:
            item = _activity(a, sources.get(a.id, []))
            if a.sport == "gym":
                item["gym_details"] = by_activity.get(a.id, [])
            items.append(item)
    return {"items": items, "total": total or 0}


async def get_activity(athlete_id: int, activity_external_id: str) -> dict[str, Any] | None:
    """One activity with ``gym_details`` for a gym one, or None."""
    try:
        activity, sets = await get_activity_with_details(athlete_id, activity_external_id)
    except ValueError:
        return None
    if activity["sport"] == "gym":
        activity["gym_details"] = sets
    return activity


async def list_sports(athlete_id: int) -> list[str]:
    async with health_session() as session:
        rows = await session.execute(
            select(Activity.sport)
            .where(Activity.user_id == athlete_id)
            .distinct()
            .order_by(Activity.sport)
        )
        return [r[0] for r in rows.all()]


async def list_settled_activities_since(athlete_id: int, since: datetime) -> list[dict[str, Any]]:
    """Settled activities that started at or after ``since`` (the post-workout catch-up scan)."""
    async with health_session() as session:
        return await _activities(
            session,
            select(Activity)
            .where(
                Activity.user_id == athlete_id,
                Activity.start_time >= since,
                Activity.settled_at.isnot(None),
            )
            .order_by(Activity.start_time),
        )


# --- the logger's reads ---------------------------------------------------------------


async def list_exercise_catalogue() -> list[dict[str, str]]:
    """The pinned exercise catalogue: ``[{"id", "name"}]``."""
    return [{"id": e.id, "name": e.name} for e in load_exercise_catalogue()]


def _key(exercise_id: str | None, title: str) -> tuple[str | None, str | None]:
    # A custom exercise has no id, so its display title is the only handle it has (#55).
    return (exercise_id, None) if exercise_id else (None, title)


async def get_last_performed(athlete_id: int) -> list[dict[str, Any]]:
    """For every exercise in history, the sets from the last time it was trained.

    Shared by coaching and the Logger's grey reference row. Each row: ``exercise_id``,
    ``title``, ``date`` (ISO), ``sets`` (set_index, set_type, weight_kg, reps), sorted by title.
    """
    async with health_session() as session:
        pairs = await session.execute(
            select(
                GymWorkoutDetail.exercise_id,
                GymWorkoutDetail.exercise_title,
                GymWorkoutDetail.activity_id,
                Activity.start_time,
            )
            .join(Activity, Activity.id == GymWorkoutDetail.activity_id)
            .where(Activity.user_id == athlete_id)
            .distinct()
        )
        latest: dict[tuple[str | None, str | None], tuple[datetime, int, str]] = {}
        for exercise_id, title, activity_id, start_time in pairs:
            key = _key(exercise_id, title)
            current = latest.get(key)
            # Ties on start_time break on the higher activity id: the later import.
            if current is None or (start_time, activity_id) > (current[0], current[1]):
                latest[key] = (start_time, activity_id, title)
        if not latest:
            return []

        details = (
            await session.execute(
                select(GymWorkoutDetail)
                .where(GymWorkoutDetail.activity_id.in_({v[1] for v in latest.values()}))
                .order_by(GymWorkoutDetail.set_index, GymWorkoutDetail.id)
            )
        ).scalars()
        sets_by_key: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
        for d in details:
            key = _key(d.exercise_id, d.exercise_title)
            won = latest.get(key)
            if won is None or won[1] != d.activity_id:
                continue
            sets_by_key.setdefault(key, []).append(
                {
                    "set_index": d.set_index,
                    "set_type": d.set_type,
                    "weight_kg": d.weight_kg,
                    "reps": d.reps,
                }
            )

    rows = [
        {
            "exercise_id": key[0],
            "title": title,
            "date": start_time.date().isoformat(),
            "sets": sets_by_key.get(key, []),
        }
        for key, (start_time, _id, title) in latest.items()
    ]
    rows.sort(key=lambda row: row["title"])
    return rows
