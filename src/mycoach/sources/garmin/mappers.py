"""Map raw Garmin API responses to ORM models and import into the database."""

import json
import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import Activity
from mycoach.models.health import DailyHealthSnapshot
from mycoach.sources.base import ImportResult

logger = logging.getLogger(__name__)

# Garmin activity type name → our sport classification
GARMIN_SPORT_MAP: dict[str, str] = {
    "swimming": "swimming",
    "lap_swimming": "swimming",
    "open_water_swimming": "swimming",
    "pool_swimming": "swimming",
    "strength_training": "gym",
    "cardio": "cardio",
    "running": "cardio",
    "cycling": "cardio",
    "walking": "cardio",
    "hiking": "cardio",
    "indoor_cardio": "cardio",
    "elliptical": "cardio",
    "other": "other",
}


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _classify_sport(activity_type: str) -> str:
    """Map Garmin activity type to our sport categories."""
    normalized = activity_type.lower().replace(" ", "_")
    return GARMIN_SPORT_MAP.get(normalized, "other")


def _parse_garmin_timestamp(ts: str | int | None) -> datetime | None:
    """Parse a Garmin timestamp (ISO string or epoch millis) to datetime."""
    if ts is None:
        return None
    if isinstance(ts, int):
        return datetime.fromtimestamp(ts / 1000)
    try:
        # Garmin uses various ISO formats
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def map_health_snapshot(
    user_id: int,
    snapshot_date: date,
    stats: dict[str, Any],
    sleep: dict[str, Any] | None = None,
    hrv: dict[str, Any] | None = None,
    stress: dict[str, Any] | None = None,
    body_battery: list[dict[str, Any]] | None = None,
    training_readiness: dict[str, Any] | None = None,
    training_status: dict[str, Any] | None = None,
    max_metrics: dict[str, Any] | None = None,
    respiration: dict[str, Any] | None = None,
    spo2: dict[str, Any] | None = None,
) -> DailyHealthSnapshot:
    """Build a DailyHealthSnapshot from raw Garmin API responses.

    Each parameter is the raw dict returned by the corresponding GarminClient method.
    Missing/None parameters are handled gracefully.
    """
    # Heart rate from stats
    resting_hr = _safe_int(stats.get("restingHeartRate"))
    max_hr = _safe_int(stats.get("maxHeartRate"))
    avg_hr = _safe_int(stats.get("averageHeartRate"))

    # HRV
    hrv_status_val = None
    hrv_7day_avg_val = None
    if hrv:
        hrv_summary = hrv.get("hrvSummary") or hrv
        hrv_status_val = _safe_float(hrv_summary.get("lastNightAvg"))
        hrv_7day_avg_val = _safe_float(hrv_summary.get("weeklyAvg"))

    # Sleep
    sleep_duration = None
    sleep_score = None
    sleep_deep = None
    sleep_light = None
    sleep_rem = None
    sleep_awake = None
    if sleep:
        daily_sleep = sleep.get("dailySleepDTO") or sleep
        sleep_duration_secs = _safe_int(daily_sleep.get("sleepTimeSeconds"))
        sleep_duration = sleep_duration_secs // 60 if sleep_duration_secs else None
        sleep_score = _safe_int(daily_sleep.get("sleepScores", {}).get("overall", {}).get("value"))
        sleep_deep_secs = _safe_int(daily_sleep.get("deepSleepSeconds"))
        sleep_deep = sleep_deep_secs // 60 if sleep_deep_secs else None
        sleep_light_secs = _safe_int(daily_sleep.get("lightSleepSeconds"))
        sleep_light = sleep_light_secs // 60 if sleep_light_secs else None
        sleep_rem_secs = _safe_int(daily_sleep.get("remSleepSeconds"))
        sleep_rem = sleep_rem_secs // 60 if sleep_rem_secs else None
        sleep_awake_secs = _safe_int(daily_sleep.get("awakeSleepSeconds"))
        sleep_awake = sleep_awake_secs // 60 if sleep_awake_secs else None

    # Body Battery
    bb_high = None
    bb_low = None
    if body_battery:
        bb_values = [_safe_int(b.get("charged")) for b in body_battery if b.get("charged")]
        bb_drain = [_safe_int(b.get("drained")) for b in body_battery if b.get("drained")]
        if bb_values:
            bb_high = max(v for v in bb_values if v is not None) if any(bb_values) else None
        if bb_drain:
            bb_low = min(v for v in bb_drain if v is not None) if any(bb_drain) else None

    # Stress
    avg_stress_val = None
    if stress:
        avg_stress_val = _safe_int(stress.get("overallStressLevel"))

    # Training readiness
    tr_score = None
    if training_readiness:
        tr_score = _safe_int(training_readiness.get("score"))

    # Training status / load
    t_load = None
    t_status = None
    if training_status:
        t_load = _safe_float(training_status.get("trainingLoad"))
        t_status = training_status.get("trainingStatus")

    # VO2 max
    vo2 = None
    if max_metrics:
        generic = max_metrics.get("generic") or max_metrics
        if isinstance(generic, dict):
            vo2 = _safe_float(generic.get("vo2MaxValue"))
        elif isinstance(generic, list) and generic:
            vo2 = _safe_float(generic[0].get("vo2MaxValue"))

    # Steps
    steps = _safe_int(stats.get("totalSteps"))

    # Respiration
    resp_avg = None
    if respiration:
        resp_avg = _safe_float(respiration.get("avgWakingRespirationValue"))

    # SpO2
    spo2_avg = None
    if spo2:
        spo2_avg = _safe_float(spo2.get("averageSpo2"))

    # Intensity minutes
    intensity = None
    im = stats.get("intensityMinutes") or stats.get("moderateIntensityMinutes")
    if im is not None:
        intensity = _safe_int(im)

    # Store all raw data for debugging
    raw = {
        "stats": stats,
        "sleep": sleep,
        "hrv": hrv,
        "stress": stress,
        "body_battery": body_battery,
        "training_readiness": training_readiness,
        "training_status": training_status,
        "max_metrics": max_metrics,
        "respiration": respiration,
        "spo2": spo2,
    }

    return DailyHealthSnapshot(
        user_id=user_id,
        snapshot_date=snapshot_date,
        resting_hr=resting_hr,
        max_hr=max_hr,
        avg_hr=avg_hr,
        hrv_status=hrv_status_val,
        hrv_7day_avg=hrv_7day_avg_val,
        sleep_duration_minutes=sleep_duration,
        sleep_score=sleep_score,
        sleep_deep_minutes=sleep_deep,
        sleep_light_minutes=sleep_light,
        sleep_rem_minutes=sleep_rem,
        sleep_awake_minutes=sleep_awake,
        body_battery_high=bb_high,
        body_battery_low=bb_low,
        avg_stress=avg_stress_val,
        training_readiness=tr_score,
        training_load=t_load,
        training_status=t_status,
        vo2_max=vo2,
        steps=steps,
        respiration_avg=resp_avg,
        spo2_avg=spo2_avg,
        intensity_minutes=intensity,
        raw_data=json.dumps(raw, default=str),
        data_source="garmin",
        created_at=datetime.utcnow(),
    )


def map_activity(user_id: int, raw: dict[str, Any]) -> Activity:
    """Map a single raw Garmin activity dict to an Activity model."""
    activity_type = raw.get("activityType", {})
    type_key = activity_type.get("typeKey", "") if isinstance(activity_type, dict) else ""
    sport = _classify_sport(type_key)

    start = _parse_garmin_timestamp(raw.get("startTimeLocal") or raw.get("startTimeGMT"))
    duration_secs = _safe_float(raw.get("duration"))
    duration_mins = int(duration_secs / 60) if duration_secs else None

    end = None
    if start and duration_secs:
        from datetime import timedelta

        end = start + timedelta(seconds=duration_secs)

    hr_zones_raw = raw.get("heartRateZones")
    hr_zones_json = json.dumps(hr_zones_raw) if hr_zones_raw else None

    return Activity(
        user_id=user_id,
        sport=sport,
        title=raw.get("activityName", type_key or "Unknown"),
        start_time=start or datetime.utcnow(),
        end_time=end,
        duration_minutes=duration_mins,
        avg_hr=_safe_int(raw.get("averageHR")),
        max_hr=_safe_int(raw.get("maxHR")),
        calories=_safe_int(raw.get("calories")),
        hr_zones=hr_zones_json,
        training_effect_aerobic=_safe_float(raw.get("aerobicTrainingEffect")),
        training_effect_anaerobic=_safe_float(raw.get("anaerobicTrainingEffect")),
        data_source="garmin",
        garmin_activity_id=str(raw.get("activityId")) if raw.get("activityId") else None,
        created_at=datetime.utcnow(),
    )


async def _snapshot_exists(session: AsyncSession, user_id: int, day: date) -> bool:
    """Check if a health snapshot already exists for this date."""
    stmt = select(DailyHealthSnapshot.id).where(
        DailyHealthSnapshot.user_id == user_id,
        DailyHealthSnapshot.snapshot_date == day,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _activity_exists(session: AsyncSession, garmin_activity_id: str) -> bool:
    """Check if an activity with this Garmin ID already exists."""
    stmt = select(Activity.id).where(
        Activity.garmin_activity_id == garmin_activity_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def import_health_snapshot(session: AsyncSession, snapshot: DailyHealthSnapshot) -> bool:
    """Import a health snapshot, skipping if one already exists for that date.

    Returns:
        True if created, False if skipped (duplicate).
    """
    if await _snapshot_exists(session, snapshot.user_id, snapshot.snapshot_date):
        logger.debug("Snapshot for %s already exists, skipping", snapshot.snapshot_date)
        return False
    session.add(snapshot)
    return True


async def import_activities(
    session: AsyncSession, user_id: int, raw_activities: list[dict[str, Any]]
) -> ImportResult:
    """Import Garmin activities into the database with deduplication.

    Returns:
        ImportResult with counts.
    """
    result = ImportResult(source_type="garmin")
    errors: list[str] = []

    for raw in raw_activities:
        garmin_id = raw.get("activityId")
        if not garmin_id:
            errors.append(f"Activity missing activityId: {raw.get('activityName', 'unknown')}")
            continue

        if await _activity_exists(session, str(garmin_id)):
            result.activities_skipped += 1
            continue

        try:
            activity = map_activity(user_id, raw)
            session.add(activity)
            result.activities_created += 1
        except Exception as e:
            errors.append(f"Failed to map activity {garmin_id}: {e}")

    if errors:
        result.errors = errors
    return result
