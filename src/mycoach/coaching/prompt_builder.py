"""Loads prompt templates and formats them with context data."""

from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

_PROMPT_DIR: Path | None = None


def _get_prompt_dir(version: str = "v1") -> Path:
    global _PROMPT_DIR
    if _PROMPT_DIR is not None:
        return _PROMPT_DIR / version
    pkg = resources.files("mycoach") / "prompts" / version
    return Path(str(pkg))


def set_prompt_dir(path: Path) -> None:
    """Override prompt directory (for testing)."""
    global _PROMPT_DIR
    _PROMPT_DIR = path


def _load_template(name: str, version: str = "v1") -> str:
    path = _get_prompt_dir(version) / name
    return path.read_text()


def get_system_prompt(version: str = "v1") -> str:
    return _load_template("system.txt", version)


def _format_health(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "No health data available for today."
    lines = []
    field_labels = {
        "resting_hr": "Resting HR",
        "avg_hr": "Avg HR",
        "max_hr": "Max HR",
        "hrv_status": "HRV",
        "hrv_7day_avg": "HRV 7-day avg",
        "sleep_duration_minutes": "Sleep duration (min)",
        "sleep_score": "Sleep score",
        "sleep_deep_minutes": "Deep sleep (min)",
        "sleep_rem_minutes": "REM sleep (min)",
        "body_battery_high": "Body Battery high",
        "body_battery_low": "Body Battery low",
        "avg_stress": "Avg stress",
        "training_readiness": "Training readiness",
        "training_load": "Training load",
        "training_status": "Training status",
        "vo2_max": "VO2 max",
        "steps": "Steps",
    }
    for key, label in field_labels.items():
        val = snapshot.get(key)
        if val is not None:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines) if lines else "No health data available for today."


def _format_health_trends(snapshots: list[dict[str, Any]]) -> str:
    if not snapshots:
        return "No recent health data."
    parts = []
    for s in snapshots:
        d = s.get("snapshot_date", "?")
        parts.append(f"### {d}")
        parts.append(_format_health(s))
    return "\n\n".join(parts)


def _format_activities(activities: list[dict[str, Any]]) -> str:
    if not activities:
        return "No recent activities."
    lines = []
    for a in activities:
        sport = a.get("sport", "unknown")
        title = a.get("title", "Untitled")
        start = a.get("start_time", "?")
        duration = a.get("duration_minutes")
        dur_str = f" ({duration} min)" if duration else ""
        lines.append(f"- {start}: {title} [{sport}]{dur_str}")
    return "\n".join(lines)


def build_daily_briefing_prompt(
    *,
    health_today: dict[str, Any],
    health_trends: list[dict[str, Any]],
    recent_activities: list[dict[str, Any]],
    planned_workout: str | None = None,
    version: str = "v1",
) -> str:
    """Build the user message for a daily briefing LLM call."""
    template = _load_template("daily_briefing.txt", version)
    return template.format(
        health_data=_format_health(health_today),
        health_trends=_format_health_trends(health_trends),
        recent_activities=_format_activities(recent_activities),
        planned_workout=planned_workout or "No planned workout for today.",
    )


def _format_availability(slots: list[dict[str, Any]]) -> str:
    if not slots:
        return "No availability slots set."
    lines = []
    for s in slots:
        day = s.get("day_name", f"Day {s.get('day_of_week', '?')}")
        start = s.get("start_time", "?")
        dur = s.get("duration_minutes", "?")
        sport = s.get("preferred_sport", "any")
        lines.append(f"- {day} at {start} ({dur} min) — {sport}")
    return "\n".join(lines)


def build_weekly_plan_prompt(
    *,
    availability: list[dict[str, Any]],
    health_trends: list[dict[str, Any]],
    recent_activities: list[dict[str, Any]],
    mesocycle_context: str | None = None,
    version: str = "v1",
) -> str:
    """Build the user message for a weekly plan LLM call."""
    template = _load_template("weekly_plan.txt", version)
    return template.format(
        availability=_format_availability(availability),
        health_trends=_format_health_trends(health_trends),
        recent_activities=_format_activities(recent_activities),
        mesocycle_context=mesocycle_context
        or "No mesocycle configured. Use general progressive programming.",
    )


def snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    """Convert a DailyHealthSnapshot ORM object to a plain dict."""
    fields = [
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
        "body_battery_high",
        "body_battery_low",
        "avg_stress",
        "training_readiness",
        "training_load",
        "training_status",
        "vo2_max",
        "steps",
        "respiration_avg",
        "spo2_avg",
        "intensity_minutes",
    ]
    result: dict[str, Any] = {}
    for f in fields:
        val = getattr(snapshot, f, None)
        if val is not None:
            result[f] = str(val) if isinstance(val, date) else val
    return result


def activity_to_dict(activity: Any) -> dict[str, Any]:
    """Convert an Activity ORM object to a plain dict."""
    return {
        "title": activity.title,
        "sport": activity.sport,
        "start_time": str(activity.start_time) if activity.start_time else None,
        "duration_minutes": activity.duration_minutes,
        "avg_hr": activity.avg_hr,
        "max_hr": activity.max_hr,
        "calories": activity.calories,
        "training_effect_aerobic": activity.training_effect_aerobic,
    }
