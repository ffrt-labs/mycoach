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
    "running": "running",
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
    max_metrics: list[dict[str, Any]] | dict[str, Any] | None = None,
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
    hrv_status_text_val = None
    if isinstance(hrv, dict):
        hrv_summary = hrv.get("hrvSummary") or hrv
        if isinstance(hrv_summary, dict):
            hrv_status_val = _safe_float(hrv_summary.get("lastNightAvg"))
            hrv_7day_avg_val = _safe_float(hrv_summary.get("weeklyAvg"))
            hrv_status_text_val = hrv_summary.get("status")

    # Sleep
    sleep_duration = None
    sleep_score = None
    sleep_deep = None
    sleep_light = None
    sleep_rem = None
    sleep_awake = None
    if isinstance(sleep, dict):
        daily_sleep = sleep.get("dailySleepDTO") or sleep
        if isinstance(daily_sleep, dict):
            sleep_duration_secs = _safe_int(daily_sleep.get("sleepTimeSeconds"))
            sleep_duration = sleep_duration_secs // 60 if sleep_duration_secs else None
            sleep_scores = daily_sleep.get("sleepScores")
            overall = sleep_scores.get("overall") if isinstance(sleep_scores, dict) else None
            sleep_score = _safe_int(overall.get("value")) if isinstance(overall, dict) else None
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
    bb_morning = None
    if body_battery:
        bb_entries = [b for b in body_battery if isinstance(b, dict)]
        bb_values = [_safe_int(b.get("charged")) for b in bb_entries if b.get("charged")]
        bb_drain = [_safe_int(b.get("drained")) for b in bb_entries if b.get("drained")]
        if bb_values:
            bb_high = max(v for v in bb_values if v is not None) if any(bb_values) else None
        if bb_drain:
            bb_low = min(v for v in bb_drain if v is not None) if any(bb_drain) else None

    # Stress
    avg_stress_val = None
    if isinstance(stress, dict):
        avg_stress_val = _safe_int(stress.get("avgStressLevel"))

    # Training readiness
    tr_score = None
    if isinstance(training_readiness, dict):
        tr_score = _safe_int(training_readiness.get("score"))

    # Training status / load
    t_load = None
    t_status = None
    load_focus_val = None
    if isinstance(training_status, dict):
        t_load = _safe_float(training_status.get("trainingLoad"))
        t_status = training_status.get("trainingStatus")
        # Extract load focus from mostRecentTrainingLoadBalance
        tlb = training_status.get("mostRecentTrainingLoadBalance")
        if isinstance(tlb, dict):
            for _dev_id, balance in (tlb.get("metricsTrainingLoadBalanceDTOMap") or {}).items():
                if isinstance(balance, dict) and balance.get("primaryTrainingDevice"):
                    load_focus_val = json.dumps({
                        "aerobic_low": balance.get("monthlyLoadAerobicLow"),
                        "aerobic_high": balance.get("monthlyLoadAerobicHigh"),
                        "anaerobic": balance.get("monthlyLoadAnaerobic"),
                        "feedback": balance.get("trainingBalanceFeedbackPhrase"),
                    })
                    break
        # Extract recovery time from mostRecentTrainingStatus
        tsd = training_status.get("mostRecentTrainingStatus")
        if isinstance(tsd, dict):
            for _dev_id, status_data in (tsd.get("latestTrainingStatusData") or {}).items():
                if isinstance(status_data, dict) and status_data.get("primaryTrainingDevice"):
                    # Use t_status from the detailed data if available
                    if not t_status:
                        ts_val = status_data.get("trainingStatus")
                        if ts_val is not None:
                            t_status = str(ts_val)
                    break

    # VO2 max — get_max_metrics returns a list of daily records (one per queried
    # day), each shaped like {"generic": {"vo2MaxValue": ...}, ...}.
    vo2 = None
    day_metrics: Any = max_metrics
    if isinstance(max_metrics, list) and max_metrics:
        day_metrics = max_metrics[0]
    if isinstance(day_metrics, dict):
        generic = day_metrics.get("generic")
        if isinstance(generic, dict):
            vo2 = _safe_float(generic.get("vo2MaxValue"))

    # Body battery morning (wake time value from stats)
    bb_morning = _safe_int(stats.get("bodyBatteryAtWakeTime"))

    # Steps
    steps = _safe_int(stats.get("totalSteps"))

    # Respiration
    resp_avg = None
    if isinstance(respiration, dict):
        resp_avg = _safe_float(respiration.get("avgWakingRespirationValue"))

    # SpO2
    spo2_avg = None
    if isinstance(spo2, dict):
        spo2_avg = _safe_float(spo2.get("averageSpO2"))

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
        hrv_status_text=hrv_status_text_val,
        sleep_duration_minutes=sleep_duration,
        sleep_score=sleep_score,
        sleep_deep_minutes=sleep_deep,
        sleep_light_minutes=sleep_light,
        sleep_rem_minutes=sleep_rem,
        sleep_awake_minutes=sleep_awake,
        body_battery_high=bb_high,
        body_battery_low=bb_low,
        body_battery_morning=bb_morning,
        avg_stress=avg_stress_val,
        training_readiness=tr_score,
        training_load=t_load,
        training_status=t_status,
        vo2_max=vo2,
        recovery_time_hours=None,
        load_focus=load_focus_val,
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
    if not hr_zones_raw:
        # Fall back to flat hrTimeInZone_* fields (common for swimming)
        flat_zones = {}
        for i in range(1, 6):
            val = _safe_float(raw.get(f"hrTimeInZone_{i}"))
            if val is not None:
                flat_zones[i] = val
        if flat_zones:
            hr_zones_raw = [
                {"zoneNumber": z, "secsInZone": secs} for z, secs in sorted(flat_zones.items())
            ]
    hr_zones_json = json.dumps(hr_zones_raw) if hr_zones_raw else None

    # Cadence: pick the right field based on sport
    cadence = _safe_int(
        raw.get("averageRunningCadenceInStepsPerMinute")
        or raw.get("averageSwimCadenceInStrokesPerMinute")
    )

    return Activity(
        user_id=user_id,
        sport=sport,
        title=raw.get("activityName", type_key or "Unknown"),
        start_time=start or datetime.utcnow(),
        end_time=end,
        duration_minutes=duration_mins,
        distance_meters=_safe_float(raw.get("distance")),
        avg_speed_mps=_safe_float(raw.get("averageSpeed")),
        avg_hr=_safe_int(raw.get("averageHR")),
        max_hr=_safe_int(raw.get("maxHR")),
        calories=_safe_int(raw.get("calories")),
        hr_zones=hr_zones_json,
        training_effect_aerobic=_safe_float(raw.get("aerobicTrainingEffect")),
        training_effect_anaerobic=_safe_float(raw.get("anaerobicTrainingEffect")),
        epoc=_safe_float(raw.get("activityTrainingLoad")),
        recovery_time_minutes=_safe_int(raw.get("recoveryTime")),
        avg_cadence=cadence,
        avg_swolf=_safe_float(raw.get("averageSwolf")),
        moving_duration_seconds=_safe_float(raw.get("movingDuration")),
        fastest_split_100_seconds=_safe_float(raw.get("fastestSplit_100")),
        avg_strokes_per_length=_safe_float(raw.get("avgStrokes")),
        data_source="garmin",
        garmin_activity_id=str(raw.get("activityId")) if raw.get("activityId") else None,
        raw_data=json.dumps(raw, default=str),
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


_UPDATABLE_FIELDS = [
    "resting_hr", "max_hr", "avg_hr", "hrv_status", "hrv_7day_avg",
    "hrv_status_text",
    "sleep_duration_minutes", "sleep_score", "sleep_deep_minutes",
    "sleep_light_minutes", "sleep_rem_minutes", "sleep_awake_minutes",
    "body_battery_high", "body_battery_low", "body_battery_morning",
    "avg_stress",
    "training_readiness", "training_load", "training_status", "vo2_max",
    "recovery_time_hours", "load_focus",
    "steps", "respiration_avg", "spo2_avg", "intensity_minutes", "raw_data",
]


async def import_health_snapshot(session: AsyncSession, snapshot: DailyHealthSnapshot) -> bool:
    """Import a health snapshot, or update an existing one with newer data.

    Existing fields that are non-null are overwritten only when the incoming
    snapshot also has a non-null value. Fields that were previously null get
    filled in (e.g., sleep data arriving later in the day).

    Returns:
        True if created, False if updated.
    """
    stmt = select(DailyHealthSnapshot).where(
        DailyHealthSnapshot.user_id == snapshot.user_id,
        DailyHealthSnapshot.snapshot_date == snapshot.snapshot_date,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is None:
        session.add(snapshot)
        return True

    # Update existing snapshot: fill nulls and refresh non-null values
    for field in _UPDATABLE_FIELDS:
        new_val = getattr(snapshot, field)
        if new_val is not None:
            setattr(existing, field, new_val)
    logger.debug("Updated snapshot for %s with fresh data", snapshot.snapshot_date)
    return False


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
