"""Loads prompt templates and formats them with context data."""

import json
from collections.abc import Callable, Mapping
from datetime import date
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from mycoach.coaching.body import BodySummary
from mycoach.coaching.email_schemas import CardioPlanV5, PostWorkoutEmail, WeeklyRecapEmail
from mycoach.coaching.hrv import format_ms, hrv_band, hrv_band_text, hrv_status_word
from mycoach.coaching.life_context import (
    EXCUSED_LABEL,
    STATUS_LABEL,
    Episode,
    EpisodeKind,
    LifeContext,
    RampBack,
    excused_summary,
    is_jet_lag,
)
from mycoach.coaching.strict_schema import strict_json_schema
from mycoach.localtime import WEEKDAY_NAMES
from mycoach.models.plan import INTENT_LABEL

if TYPE_CHECKING:
    from pydantic import BaseModel

    from mycoach.coaching.context import OpenThisWeek
    from mycoach.coaching.steps import StepsSummary

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


class Operation(StrEnum):
    """A coaching LLM call. The value names its bundled template file."""

    DAILY_BRIEFING = "daily_briefing"
    GYM_ADJUSTMENT = "gym_adjustment"
    CARDIO_PLAN = "cardio_plan"
    POST_WORKOUT = "post_workout"
    WEEKLY_RECAP = "weekly_recap"


BUNDLED_VERSION = "v4"
"""The bundled template set (``prompts/v4``) of an operation not yet on the
structured-output contract."""

COACH_NOTES_VERSION = "v5"
"""The bundled template set of the structured-output contract (``prompts/v5``):
an operation whose answer is Coach notes (``coaching/email_schemas.py``)."""

BUNDLED_VERSIONS: Mapping[Operation, str] = {
    **{operation: BUNDLED_VERSION for operation in Operation},
    Operation.POST_WORKOUT: COACH_NOTES_VERSION,
    Operation.WEEKLY_RECAP: COACH_NOTES_VERSION,
    Operation.CARDIO_PLAN: COACH_NOTES_VERSION,
}
"""Each operation's bundled template set: its default compile, the fallback the
prompt store serves, and the seed of its Langfuse prompt. An operation moves to
``COACH_NOTES_VERSION`` with its email (#238 post-workout; #241-#243 the rest)."""


def bundled_version(operation: Operation) -> str:
    return BUNDLED_VERSIONS[operation]


PRINCIPLES_VERSION = "v1"
"""The coaching principles file (``prompts/principles/v1.md``) appended to the
bundled system prompt. Sourced from the Huberman Lab Neural Network Digest
Vol. 1; each principle names the data that triggers it (#219)."""


def coaching_principles(version: str = PRINCIPLES_VERSION) -> str:
    """The coaching principles text, as appended to the system prompt."""
    return _load_template(f"{version}.md", "principles").strip()


def operation_system_prompt(version: str = BUNDLED_VERSION) -> str:
    """The system message every coaching operation sends: the shared system
    prompt, then the coaching principles. Seeded into Langfuse as-is."""
    return f"{get_system_prompt(version)}\n\n{coaching_principles()}"


PromptVariables = dict[str, str]
"""An operation's formatted Coaching context, keyed by template placeholder."""


class CompiledPrompt(NamedTuple):
    system: str
    user: str


def operation_template(operation: Operation, version: str | None = None) -> str:
    """An operation's bundled user-message template, uncompiled (``str.format`` syntax).

    ``version`` defaults to the operation's ``bundled_version``.
    """
    return _load_template(f"{operation.value}.txt", version or bundled_version(operation))


def compile_prompt(
    operation: Operation, variables: Mapping[str, str], *, version: str | None = None
) -> CompiledPrompt:
    """Render an operation's bundled template with its variable map.

    Returns the system message (shared system prompt plus coaching
    principles) and the user message sent to the model. ``version`` defaults
    to the operation's ``bundled_version``.
    Raises ``KeyError`` when the map lacks a placeholder the template uses.
    """
    version = version or bundled_version(operation)
    template = operation_template(operation, version)
    return CompiledPrompt(
        system=operation_system_prompt(version), user=template.format(**variables)
    )


_TRAINING_STATUS_MAP = {
    "0": "No Status",
    "1": "Detraining",
    "2": "Recovery",
    "3": "Maintaining",
    "4": "Productive",
    "5": "Peaking",
    "6": "Overreaching",
    "7": "Unproductive",
    "8": "Strained",
}

_LOAD_FOCUS_FEEDBACK_MAP = {
    "AEROBIC_LOW_SHORTAGE": "Lacking low-aerobic training load",
    "AEROBIC_HIGH_SHORTAGE": "Lacking high-aerobic training load",
    "ANAEROBIC_SHORTAGE": "Lacking anaerobic training load",
    "BALANCED": "Balanced training load",
    "AEROBIC_LOW_SURPLUS": "Excess low-aerobic training load",
    "AEROBIC_HIGH_SURPLUS": "Excess high-aerobic training load",
    "ANAEROBIC_SURPLUS": "Excess anaerobic training load",
    "LOW_LOAD": "Low overall training load",
}


def _format_training_status(val: Any) -> str:
    """Format training_status from numeric or string Garmin value."""
    s = str(val)
    if s in _TRAINING_STATUS_MAP:
        return _TRAINING_STATUS_MAP[s]
    if s.isupper():
        return s.replace("_", " ").title()
    return s


def _format_load_focus(val: Any) -> str:
    """Parse load_focus JSON and format as readable string."""
    if not val:
        return str(val)
    try:
        data = json.loads(val) if isinstance(val, str) else val
    except (json.JSONDecodeError, TypeError):
        return str(val)
    if not isinstance(data, dict):
        return str(val)

    parts = []
    for key, label in [
        ("aerobic_low", "Low aerobic"),
        ("aerobic_high", "High aerobic"),
        ("anaerobic", "Anaerobic"),
    ]:
        v = data.get(key)
        if v is not None:
            parts.append(f"{label} {v:.1f}")
    result = ", ".join(parts)
    feedback = data.get("feedback")
    if feedback:
        fb_text = _LOAD_FOCUS_FEEDBACK_MAP.get(feedback, feedback.replace("_", " ").title())
        result += f". Feedback: {fb_text}"
    return result


_HRV_KEYS = {"hrv_status", "hrv_7day_avg", "hrv_status_text"}


def _format_hrv_band(snapshot: dict[str, Any]) -> str:
    """One HRV line led by the 7-day avg against the band (#197), last night as context."""
    band = f"range {hrv_band_text(snapshot)}"
    status = snapshot.get("hrv_status_text")
    if status:
        band += f", {hrv_status_word(status)}"
    weekly = snapshot.get("hrv_7day_avg")
    line = (
        f"7d avg {format_ms(weekly)} ms ({band})" if weekly is not None else f"7d avg n/a ({band})"
    )
    if snapshot.get("hrv_status") is not None:
        line += f", last night {format_ms(snapshot['hrv_status'])} ms"
    if snapshot.get("hrv_baseline_low_upper") is not None:
        line += f"; Low below {format_ms(snapshot['hrv_baseline_low_upper'])} ms"
    return f"- HRV: {line}"


def _format_health(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "No health data available for today."
    lines = []
    band = hrv_band(snapshot) is not None
    band_written = False
    field_labels = {
        "resting_hr": "Resting Heart Rate",
        "avg_hr": "Avg HR",
        "max_hr": "Max HR (all day)",
        "hrv_status": "HRV last night avg (ms)",
        "hrv_7day_avg": "HRV 7-day avg",
        "sleep_duration_minutes": "Sleep duration (min)",
        "sleep_score": "Sleep score",
        "sleep_deep_minutes": "Deep sleep (min)",
        "sleep_light_minutes": "Light sleep (min)",
        "sleep_rem_minutes": "REM sleep (min)",
        "sleep_awake_minutes": "Awake time (min)",
        "wake_time": "Wake time",
        "avg_stress": "Avg stress",
        "training_readiness": "Training readiness",
        "training_load": "Training load",
        "training_status": "Training status",
        "vo2_max": "VO2 max",
        "load_focus": "Load Focus",
        "body_battery_morning": "Body Battery (morning)",
        "hrv_status_text": "HRV Status",
        "spo2_avg": "Avg SpO2",
    }
    for key, label in field_labels.items():
        val = snapshot.get(key)
        if band and key in _HRV_KEYS:
            if val is not None and not band_written:
                lines.append(_format_hrv_band(snapshot))
                band_written = True
            continue
        if val is not None:
            if key == "training_status":
                val = _format_training_status(val)
            elif key == "load_focus":
                val = _format_load_focus(val)
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
        extras: list[str] = []
        if duration:
            extras.append(f"{duration} min")
        distance = a.get("distance_meters")
        if sport == "swimming":
            # Swimming-specific compact format
            if distance:
                extras.append(f"{distance:.0f}m")
                moving_secs = a.get("moving_duration_seconds")
                pace_secs = moving_secs if moving_secs else (duration * 60 if duration else None)
                if pace_secs and distance > 0:
                    pace_per_100 = pace_secs / (distance / 100)
                    extras.append(f"pace {_format_seconds_as_pace(pace_per_100)}/100m")
            swolf = a.get("avg_swolf")
            if swolf is not None:
                extras.append(f"SWOLF {swolf}")
            strokes = a.get("avg_strokes_per_length")
            if strokes is not None:
                extras.append(f"{strokes} str/len")
            epoc = a.get("epoc")
            if epoc is not None:
                extras.append(f"load {epoc}")
        else:
            if distance:
                extras.append(f"{distance:.0f}m")
                if duration and distance > 0 and sport != "gym":
                    extras.append(f"pace {_format_pace(distance, duration, sport)}")
            avg_hr = a.get("avg_hr")
            if avg_hr:
                extras.append(f"avg HR {avg_hr}")
            calories = a.get("calories")
            if calories:
                extras.append(f"{calories} cal")
            te = a.get("training_effect_aerobic")
            if te:
                extras.append(f"TE {te}")
        detail = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"- {start}: {title} [{sport}]{detail}")
    return "\n".join(lines)


def _format_sport_profiles(profiles: list[dict[str, Any]]) -> str:
    if not profiles:
        return "No sport profiles configured."
    lines = []
    for p in profiles:
        parts = [f"### {p['sport'].capitalize()} — {p['skill_level']}"]
        if p.get("goals"):
            parts.append(f"- Goals: {p['goals']}")
        if p.get("preferences"):
            parts.append(f"- Preferences: {p['preferences']}")
        if p.get("benchmarks"):
            parts.append(f"- Benchmarks: {p['benchmarks']}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


def _format_planned_sessions(sessions: list[dict[str, Any]]) -> str:
    if not sessions:
        return "No planned workout for today."
    lines = []
    for s in sessions:
        # A session standing in for a failed generation carries failure text in its title
        # and notes; the model would coach around that text as if it were the plan (#187).
        failed = s.get("generation_error") is not None
        if failed and not s.get("details"):
            track_tag = f" [{s['track']} track]" if s.get("track") else ""
            lines.append(f"- {s.get('sport', '?')}{track_tag}: No plan available for this session.")
            continue
        parts = [f"- {s.get('title', 'Untitled')} [{s.get('sport', '?')}]"]
        dur = s.get("duration_minutes")
        if dur:
            parts.append(f"({dur} min)")
        track = s.get("track")
        if track:
            parts.append(f"[{track} track]")
        lines.append(" ".join(parts))
        if s.get("details"):
            lines.append(f"  Details: {s['details']}")
        if s.get("notes") and not failed:
            lines.append(f"  Notes: {s['notes']}")
    return "\n".join(lines)


def _format_plan_history(summaries: list[dict[str, Any]]) -> str:
    if not summaries:
        return "No previous plans available."
    lines = []
    for s in summaries:
        adherence = f"{s['completed_sessions']}/{s['total_sessions']} ({s['adherence_pct']}%)"
        line = f"- Week of {s['week_start']}: {s.get('summary', 'N/A')} — Adherence: {adherence}"
        lines.append(line)
    return "\n".join(lines)


def _format_routine_summary(routine: dict[str, Any] | None) -> str:
    if not routine:
        return "No active workout routine."
    lines = [f"Routine: {routine['name']}"]
    for day in routine.get("days", []):
        day_name = WEEKDAY_NAMES[day["day_of_week"]] if day.get("day_of_week") is not None else "?"
        ex_count = len(day.get("exercises", []))
        lines.append(f"- {day_name}: {day['name']} ({ex_count} exercises)")
    return "\n".join(lines)


_NO_STEPS = "No step data for the last 7 days."


def _format_steps(steps: "StepsSummary | None") -> str:
    """The steps numbers the email shows, for the coach to interpret (#196)."""
    if steps is None or not steps.has_data:
        return _NO_STEPS

    def count(n: int | None) -> str:
        return "no data" if n is None else f"{n:,}"

    lines = [
        f"Daily target: {steps.target:,} steps",
        f"Yesterday: {count(steps.yesterday)}" + (" steps" if steps.yesterday is not None else ""),
        f"7-day avg: {count(steps.avg_7d)}/day ({steps.days_at_goal}/7 days at or above target)",
        "Previous 7-day avg: "
        + ("no data" if steps.prev_avg_7d is None else f"{steps.prev_avg_7d:,}/day"),
    ]
    if steps.change is not None and steps.prev_avg_7d:
        pct = steps.change * 100 / steps.prev_avg_7d
        noise = ", within +/-10%: noise" if steps.trend == "neutral" else ""
        lines.append(f"Change: {steps.change:+,}/day ({pct:+.1f}%{noise})")
    lines.append(
        "Daily counts, oldest first: "
        + "; ".join(f"{d.weekday} {count(d.steps)}" for d in steps.days)
    )
    return "\n".join(lines)


def _format_body(body: BodySummary | None) -> str:
    """The body summary for the prompt (#198): code's numbers, with what each one rests on."""
    if body is None or body.empty:
        return "No body measurements logged."
    lines: list[str] = []
    if body.weight is not None:
        w = body.weight
        stale = f" STALE: last weigh-in {w.days_ago} days ago." if w.stale else ""
        lines.append(f"Latest weight: {w.latest_kg:.1f} kg, {w.when}.{stale}")
        if w.trend_kg is not None and w.rate_kg_per_week is not None:
            lines.append(
                f"Weight trend (time-aware EMA): {w.trend_kg:.1f} kg; "
                f"kg/week over 28 days: {w.rate_kg_per_week:+.2f}."
            )
        else:
            lines.append("Weight trend: not enough weigh-ins (needs 4+ days in the last 14).")
    if body.waist is not None:
        waist = body.waist
        lines.append(f"Latest waist: {waist.latest_cm:.1f} cm, {waist.when}.")
        if waist.ratio is not None:
            lines.append(f"Waist-to-height: {waist.ratio:.2f} (target < 0.50).")
        if waist.change_cm is None:
            lines.append("4-week waist change: no reading about 4 weeks earlier.")
        elif waist.change_meaningful:
            lines.append(f"4-week waist change: {waist.change_cm:+.1f} cm.")
        else:
            lines.append(
                f"4-week waist change: none ({waist.change_cm:+.1f} cm "
                "is within measurement noise)."
            )
    if body.weigh_in_due:
        since = f"none for {body.weight.days_ago} days" if body.weight else "none logged"
        lines.append(f"Nudge due: weigh-in ({since}).")
    if body.waist_check_due:
        since = f"none for {body.waist.days_ago} days" if body.waist else "none logged"
        lines.append(f"Nudge due: waist check ({since}).")
    if body.recent_notes:
        notes = "; ".join(f"{day}: {note}" for day, note in body.recent_notes)
        lines.append(f"Recent notes: {notes}")
    return "\n".join(lines)


_NO_LIFE_CONTEXT = "Nothing to report: no illness, travel or ramp-back."
_SEVERITY_TEXT = {"mild": "mild (above the neck)", "sick": "sick (below the neck)"}


def _day_text(day: date) -> str:
    return f"{day.strftime('%A')} {day.isoformat()}"


def _format_episode(e: Episode, today: date) -> str:
    """One episode line: an illness, or a trip with what it allows (#221)."""
    if e.kind == EpisodeKind.TRAVEL:
        return _format_trip(e, today)
    label = e.kind.capitalize()
    if e.kind == EpisodeKind.ILLNESS and e.severity:
        label += f", {_SEVERITY_TEXT.get(e.severity, e.severity)}"
    days = e.length_days(today)
    if e.ended_on is None:
        when = f"since {_day_text(e.started_on)}, still unwell today (day {days})"
    else:
        noun = "day" if days == 1 else "days"
        when = f"{_day_text(e.started_on)} to {_day_text(e.ended_on)} ({days} {noun})"
    return f"- {label}: {when}" + (f". Note: {e.note}" if e.note else "")


_EQUIPMENT_TEXT = {
    "none": "none (bodyweight only)",
    "hotel_gym": "hotel gym (a few machines, dumbbells, a treadmill)",
    "full_gym": "full gym",
}


def _format_trip(e: Episode, today: date) -> str:
    label = "Travel, planned" if e.started_on > today else "Travel"
    if e.started_on > today:
        if e.ended_on is None:
            when = f"from {_day_text(e.started_on)}, return date not set"
        else:
            days = e.length_days(today)
            noun = "day" if days == 1 else "days"
            when = f"{_day_text(e.started_on)} to {_day_text(e.ended_on)} ({days} {noun})"
    elif e.ended_on is not None and e.ended_on < today:
        days = e.length_days(today)
        noun = "day" if days == 1 else "days"
        when = f"{_day_text(e.started_on)} to {_day_text(e.ended_on)} ({days} {noun})"
    else:
        day = (today - e.started_on).days + 1
        until = "return date not set" if e.ended_on is None else f"until {_day_text(e.ended_on)}"
        when = f"since {_day_text(e.started_on)} (day {day}), {until}"
    parts = [f"- {label}: {when}."]
    if e.equipment:
        parts.append(f"Equipment: {_EQUIPMENT_TEXT.get(e.equipment, e.equipment)}.")
    if e.tz_shift_hours:
        shift = f"{e.tz_shift_hours:+g} h from home"
        jet_lag = ": jet lag, apply the jet-lag principles" if is_jet_lag(e.tz_shift_hours) else ""
        parts.append(f"Time zone: {shift}{jet_lag}.")
    if e.no_training:
        parts.append("No training possible on these days.")
    if e.note:
        parts.append(f"Note: {e.note}")
    return " ".join(parts)


def _format_ramp_back(r: RampBack) -> str:
    text = (
        f"Returning from illness: ramp-back day {r.day}/{r.length}, about {r.load_pct}% of "
        f"normal training load today (50% on day 1 rising to 90% on day {r.length}; "
        f"last day {_day_text(r.ends_on)})."
    )
    if r.extend_because is not None:
        reasons = []
        if r.extend_because.hrv_below_baseline:
            reasons.append("HRV is still below baseline")
        if r.extend_because.resting_hr_elevated:
            reasons.append("resting HR is still elevated")
        because = " and ".join(reasons)
        text += (
            f"\n{because[0].upper()}{because[1:]} on the last ramp-back day: you may suggest "
            "extending the ramp-back by a few days. Never shorten it."
        )
    return text


def _format_life_context(ctx: LifeContext | None) -> str:
    """The ``life_context`` variable (#220): open and recent episodes, and the ramp-back."""
    if ctx is None or ctx.is_empty:
        return _NO_LIFE_CONTEXT
    lines = [_format_episode(e, ctx.today) for e in ctx.episodes]
    if ctx.ramp_back is not None:
        lines.append(_format_ramp_back(ctx.ramp_back))
    return "\n".join(lines)


_OPEN_STATUS_NOTE = {"overdue": ", planned day passed", "today": ", planned for today"}


def _format_open_this_week(open_week: "OpenThisWeek | None") -> str:
    """The week's open sessions for the briefing, earliest planned day first (#218)."""
    if open_week is None:
        return "No plan this week."
    sessions = open_week["sessions"]
    if not sessions:
        return "Every planned session this week is done."
    days = open_week["days_remaining"]
    noun = "session" if len(sessions) == 1 else "sessions"
    day_noun = "day" if days == 1 else "days"
    lines = [f"{len(sessions)} {noun} open, {days} {day_noun} left including today:"]
    for s in sessions:
        dur = f" ({s['duration_minutes']} min)" if s["duration_minutes"] else ""
        note = _OPEN_STATUS_NOTE.get(s["status"], "")
        lines.append(f"- {s['day']}: {s['title']} [{s['sport']}]{dur}{note}")
    if len(sessions) > days:
        lines.append("Warning: more sessions open than days left.")
    return "\n".join(lines)


def daily_briefing_variables(
    *,
    health_today: dict[str, Any],
    health_trends: list[dict[str, Any]],
    recent_activities: list[dict[str, Any]],
    planned_workout: str | list[dict[str, Any]] | None = None,
    steps: "StepsSummary | None" = None,
    body: BodySummary | None = None,
    open_this_week: "OpenThisWeek | None" = None,
    life_context: LifeContext | None = None,
) -> PromptVariables:
    """Format the Coaching context of a daily briefing LLM call."""
    if isinstance(planned_workout, list):
        formatted_workout = _format_planned_sessions(planned_workout)
    else:
        formatted_workout = planned_workout or "No planned workout for today."
    return {
        "health_data": _format_health(health_today),
        "health_trends": _format_health_trends(health_trends),
        "recent_activities": _format_activities(recent_activities),
        "planned_workout": formatted_workout,
        "open_this_week": _format_open_this_week(open_this_week),
        "steps_summary": _format_steps(steps),
        "body_summary": _format_body(body),
        "life_context": _format_life_context(life_context),
    }


def build_daily_briefing_prompt(
    *,
    health_today: dict[str, Any],
    health_trends: list[dict[str, Any]],
    recent_activities: list[dict[str, Any]],
    planned_workout: str | list[dict[str, Any]] | None = None,
    body: BodySummary | None = None,
    version: str = "v1",
) -> str:
    """Build the user message for a daily briefing LLM call."""
    variables = daily_briefing_variables(
        health_today=health_today,
        health_trends=health_trends,
        recent_activities=recent_activities,
        planned_workout=planned_workout,
        body=body,
    )
    return compile_prompt(Operation.DAILY_BRIEFING, variables, version=version).user


def _format_availability(slots: list[dict[str, Any]]) -> str:
    if not slots:
        return "No availability slots set."
    lines = []
    for s in slots:
        day = s.get("day_name", f"Day {s.get('day_of_week', '?')}")
        sport = s.get("sport")
        sport_str = f" — {sport.capitalize()}" if sport else ""
        lines.append(f"- {day}{sport_str}")
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


def _format_hr_zones(hr_zones_json: str) -> str:
    """Parse JSON hr_zones into a readable format for the LLM.

    Handles Garmin format: list of objects with zone/zoneNumber and
    minutes/secsInZone fields.
    """
    try:
        zones = json.loads(hr_zones_json)
    except (json.JSONDecodeError, TypeError):
        return hr_zones_json  # fall back to raw string

    if not isinstance(zones, list):
        return hr_zones_json

    parts = []
    for z in zones:
        zone_num = z.get("zoneNumber") or z.get("zone")
        if zone_num is None:
            continue
        minutes = z.get("minutes")
        if minutes is None:
            secs = z.get("secsInZone")
            if secs is not None:
                minutes = round(secs / 60, 1)
        if minutes is not None:
            parts.append(f"Zone {zone_num}: {minutes} min")

    return ", ".join(parts) if parts else hr_zones_json


def _format_seconds_as_pace(seconds: float) -> str:
    """Format seconds into min:sec string."""
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}:{secs:02d}"


def _format_swimming_activity_detail(activity: dict[str, Any]) -> str:
    """Format a swimming activity showing only the 10 key metrics."""
    lines = []

    # 1. Total distance
    distance = activity.get("distance_meters")
    if distance is not None:
        lines.append(f"- Total distance: {distance:.0f}m")

    # 2. Moving duration
    moving_secs = activity.get("moving_duration_seconds")
    if moving_secs is not None:
        lines.append(f"- Moving duration: {_format_seconds_as_pace(moving_secs)}")

    # 3. Average pace (from distance / moving_duration, or fallback to total duration)
    pace_secs = moving_secs
    if pace_secs is None:
        dur_min = activity.get("duration_minutes")
        if dur_min is not None:
            pace_secs = dur_min * 60
    if distance and pace_secs and distance > 0:
        pace_per_100 = pace_secs / (distance / 100)
        lines.append(f"- Avg pace: {_format_seconds_as_pace(pace_per_100)}/100m")

    # 4. HR time in zones
    hr_zones = activity.get("hr_zones")
    if hr_zones:
        lines.append(f"- HR time in zones: {_format_hr_zones(hr_zones)}")

    # 5. Aerobic training effect
    te = activity.get("training_effect_aerobic")
    if te is not None:
        lines.append(f"- Aerobic training effect: {te}")

    # 6. Activity training load (epoc)
    epoc = activity.get("epoc")
    if epoc is not None:
        lines.append(f"- Activity training load: {epoc}")

    # 7. Rest vs active time ratio
    dur_min = activity.get("duration_minutes")
    if dur_min and moving_secs and moving_secs > 0:
        total_secs = dur_min * 60
        rest_secs = total_secs - moving_secs
        ratio = rest_secs / moving_secs
        rest_str = f"{rest_secs:.0f}s rest / {moving_secs:.0f}s active"
        lines.append(f"- Rest/active ratio: {ratio:.2f} ({rest_str})")

    # 8. Fastest 100m
    fastest = activity.get("fastest_split_100_seconds")
    if fastest is not None:
        lines.append(f"- Fastest 100m: {_format_seconds_as_pace(fastest)}/100m")

    # 9. Average SWOLF
    swolf = activity.get("avg_swolf")
    if swolf is not None:
        lines.append(f"- Avg SWOLF: {swolf}")

    # 10. Strokes per length
    strokes = activity.get("avg_strokes_per_length")
    if strokes is not None:
        lines.append(f"- Strokes per length: {strokes}")

    return "\n".join(lines) if lines else "No activity data."


def _format_activity_detail(activity: dict[str, Any]) -> str:
    """Format a single activity with all available fields."""
    sport = activity.get("sport", "unknown")
    if sport == "swimming":
        return _format_swimming_activity_detail(activity)
    lines = []
    field_labels = {
        "title": "Title",
        "sport": "Sport",
        "start_time": "Start",
        "end_time": "End",
        "duration_minutes": "Duration (min)",
        "distance_meters": "Distance (m)",
        "avg_hr": "Avg HR",
        "max_hr": "Max HR (activity)",
        "calories": "Calories",
        "training_effect_aerobic": "Aerobic training effect",
        "training_effect_anaerobic": "Anaerobic training effect",
        "epoc": "EPOC",
        "recovery_time_minutes": "Recovery time (min)",
        "avg_swolf": "Avg SWOLF",
    }
    for key, label in field_labels.items():
        val = activity.get(key)
        if val is not None:
            lines.append(f"- {label}: {val}")

    # Computed pace (replaces raw avg_speed_mps)
    distance = activity.get("distance_meters")
    duration = activity.get("duration_minutes")
    if distance and duration and distance > 0 and sport != "gym":
        pace_label = "Avg pace (min/100m)" if sport == "swimming" else "Avg pace (min/km)"
        lines.append(f"- {pace_label}: {_format_pace(distance, duration, sport)}")

    # Sport-aware cadence label
    cadence = activity.get("avg_cadence")
    if cadence is not None and sport != "gym":
        cadence_label = "Avg stroke rate (spm)" if sport == "swimming" else "Avg cadence (spm)"
        lines.append(f"- {cadence_label}: {cadence}")

    hr_zones = activity.get("hr_zones")
    if hr_zones:
        lines.append(f"- HR zones: {_format_hr_zones(hr_zones)}")
    return "\n".join(lines) if lines else "No activity data."


def _format_weight_reps(d: dict[str, Any]) -> str:
    """Format a set's performed weight/reps, prefixed with its prescription if one exists.

    A set with no prescribed_weight_kg/prescribed_reps (manual entry, Hevy history)
    renders as plain performed data — it must read as unprescribed, not as missed.
    """
    performed = []
    weight = d.get("weight_kg")
    reps = d.get("reps")
    if weight is not None:
        performed.append(f"{weight}kg")
    if reps is not None:
        performed.append(f"x{reps}")
    performed_str = " ".join(performed)

    prescribed_weight = d.get("prescribed_weight_kg")
    prescribed_reps = d.get("prescribed_reps")
    if prescribed_weight is None and prescribed_reps is None:
        return performed_str

    prescribed = []
    if prescribed_weight is not None:
        prescribed.append(f"{prescribed_weight}kg")
    if prescribed_reps is not None:
        prescribed.append(f"x{prescribed_reps}")
    prescribed_str = " ".join(prescribed)
    return f"prescribed {prescribed_str}, performed {performed_str or 'nothing'}"


def _format_gym_details(details: list[dict[str, Any]]) -> str:
    """Format gym workout details (sets/reps/weight) for prompt."""
    if not details:
        return "No gym workout details (not a gym session or no data)."
    lines = []
    for exercise, sets in _group_exercise_details(details):
        lines.append(f"\n**{exercise}**")
        for d in sets:
            set_type = d.get("set_type", "normal")
            rpe = d.get("rpe")
            parts = [f"  Set {d.get('set_index', '?')}"]
            if set_type != "normal":
                parts.append(f"({set_type})")
            weight_reps = _format_weight_reps(d)
            if weight_reps:
                parts.append(weight_reps)
            if rpe is not None:
                parts.append(f"RPE {rpe}")
            lines.append(" ".join(parts))
    return "\n".join(lines)


def _format_planned_session(planned: dict[str, Any] | None) -> str:
    """Format a planned session for comparison."""
    if not planned:
        return "No planned session found for this workout."
    lines = [
        f"- Title: {planned.get('title', '?')}",
        f"- Sport: {planned.get('sport', '?')}",
        f"- Duration: {planned.get('duration_minutes', '?')} min",
    ]
    if planned.get("details"):
        lines.append(f"- Planned details: {planned['details']}")
    if planned.get("notes"):
        lines.append(f"- Notes: {planned['notes']}")
    return "\n".join(lines)


def _coach_notes_schema(model: "type[BaseModel]") -> str:
    """A Coach notes model's strict schema as prompt text.

    The same schema OpenRouter is sent as ``response_format``; in the prompt it
    reaches a provider that cannot enforce one (claude_code). A variable value,
    so its braces are never formatted.
    """
    return json.dumps(strict_json_schema(model), indent=2, ensure_ascii=False)


def post_workout_variables(
    *,
    activity: dict[str, Any],
    gym_details: list[dict[str, Any]],
    planned_session: dict[str, Any] | None,
    similar_activities: list[dict[str, Any]],
    health_context: dict[str, Any],
    life_context: LifeContext | None = None,
) -> PromptVariables:
    """Format the Coaching context of a post-workout analysis LLM call.

    The gym-details and planned-session sections are conditional, so their
    variables carry their own heading and are empty when absent.
    """
    has_planned = planned_session is not None

    ts_val = health_context.get("training_status")
    ts_label = _format_training_status(ts_val) if ts_val else "Not available"

    gym_section = (
        f"\n\n## Gym Workout Details\n{_format_gym_details(gym_details)}" if gym_details else ""
    )
    planned_section = (
        f"\n\n## Planned Session\n{_format_planned_session(planned_session)}" if has_planned else ""
    )
    return {
        "training_status_label": ts_label,
        "activity_data": _format_activity_detail(activity),
        "gym_details_section": gym_section,
        "planned_session_section": planned_section,
        "similar_activities": _format_activities(similar_activities),
        "health_context": _format_health(health_context),
        "planned_comparison": (
            " Compare actual performance to the planned session." if has_planned else ""
        ),
        "output_schema": _coach_notes_schema(PostWorkoutEmail),
        "life_context": _format_life_context(life_context),
    }


def build_post_workout_prompt(
    *,
    activity: dict[str, Any],
    gym_details: list[dict[str, Any]],
    planned_session: dict[str, Any] | None,
    similar_activities: list[dict[str, Any]],
    health_context: dict[str, Any],
    version: str = "v1",
) -> str:
    """Build the user message for a post-workout analysis LLM call."""
    variables = post_workout_variables(
        activity=activity,
        gym_details=gym_details,
        planned_session=planned_session,
        similar_activities=similar_activities,
        health_context=health_context,
    )
    return compile_prompt(Operation.POST_WORKOUT, variables, version=version).user


def _fmt_kg(w: float | None) -> str:
    """Format a weight in kg, stripping trailing .0 for whole numbers."""
    if w is None:
        return ""
    return f"{int(w)}kg" if w == int(w) else f"{w}kg"


def _exercise_identity_key(detail: dict[str, Any]) -> str:
    title = detail.get("exercise_title") or "Unknown"
    return detail.get("exercise_id") or f"custom:{title}"


def _group_exercise_details(
    details: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group by stable ID while retaining the first display title."""
    grouped: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for detail in details:
        title = detail.get("exercise_title") or "Unknown"
        key = _exercise_identity_key(detail)
        if key not in grouped:
            grouped[key] = (title, [])
        grouped[key][1].append(detail)
    return list(grouped.values())


def _format_weekly_gym_details(details: list[dict[str, Any]]) -> str:
    """Format this week's gym set/rep/weight data grouped by session then exercise."""
    if not details:
        return "No gym sessions this week."

    # Group by (session_date, session_title)
    from collections import defaultdict

    by_session: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for d in details:
        key = (d.get("session_date") or "?", d.get("session_title") or "Session")
        by_session[key][_exercise_identity_key(d)].append(d)

    parts = []
    for (sess_date, sess_title), exercises in sorted(by_session.items()):
        # Try to compute day name
        try:
            from datetime import date as _date

            d_obj = _date.fromisoformat(sess_date)
            day_name = WEEKDAY_NAMES[d_obj.weekday()]
            header = f"**{day_name} {sess_date} — {sess_title}**"
        except Exception:
            header = f"**{sess_date} — {sess_title}**"

        ex_lines = [header]
        for sets in exercises.values():
            ex_name = sets[0].get("exercise_title") or "Unknown"
            set_strs = []
            for s in sets:
                w = s.get("weight_kg")
                r = s.get("reps")
                rpe = s.get("rpe")
                st = s.get("set_type") or "normal"
                part = ""
                if w is not None and r is not None:
                    part = f"{_fmt_kg(w)}×{r}"
                elif r is not None:
                    part = f"{r} reps"
                if st != "normal":
                    part += f"({st})"
                if rpe is not None:
                    part += f" RPE{rpe}"
                if part:
                    set_strs.append(part)
            ex_lines.append(f"{ex_name}{_exercise_id_tag(sets[0])}: {', '.join(set_strs)}")
        parts.append("\n".join(ex_lines))

    return "\n\n".join(parts)


def _exercise_id_tag(detail: Mapping[str, Any]) -> str:
    """ " [exercise_id: D04AC939]" for a catalogue exercise, the key a recap lift note
    joins on (#242); nothing for a custom exercise, which gets no note."""
    exercise_id = detail.get("exercise_id")
    return f" [exercise_id: {exercise_id}]" if exercise_id else ""


def _format_gym_history(history: list[dict[str, Any]]) -> str:
    """Format gym performance history grouped by exercise, showing week-over-week best set."""
    if not history:
        return "No gym history data (first week or no prior logged gym sessions)."

    # Group by exercise
    from collections import defaultdict

    by_exercise: dict[str, list[dict[str, Any]]] = defaultdict(list)
    display_titles: dict[str, str] = {}
    for row in history:
        title = row.get("exercise_title") or "Unknown"
        key = row.get("exercise_id") or f"custom:{title}"
        display_titles.setdefault(key, title)
        by_exercise[key].append(row)

    parts = []
    for exercise_key in sorted(by_exercise, key=lambda key: display_titles[key].casefold()):
        weeks_data = sorted(by_exercise[exercise_key], key=lambda r: r.get("week_start") or "")
        tag = _exercise_id_tag(by_exercise[exercise_key][0])
        lines = [f"**{display_titles[exercise_key]}**{tag}"]
        for row in weeks_data:
            w_start = row.get("week_start", "?")
            try:
                from datetime import date as _date

                d_obj = _date.fromisoformat(w_start)
                label = d_obj.strftime("Week %b %d")
            except Exception:
                label = f"Week {w_start}"

            best_w = row.get("best_weight_kg")
            best_r = row.get("best_reps")
            total_sets = row.get("total_sets", 0)
            avg_rpe = row.get("avg_rpe")

            best_str = ""
            if best_w is not None and best_r is not None:
                best_str = f"{_fmt_kg(best_w)} × {best_r}"
            elif best_r is not None:
                best_str = f"{best_r} reps"
            else:
                best_str = "no data"

            rpe_str = f", avg RPE {avg_rpe}" if avg_rpe is not None else ""
            sets_str = f", {total_sets} sets" if total_sets else ""
            lines.append(f"- {label}: {best_str}{sets_str}{rpe_str}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _intent_label(intent: str | None) -> str | None:
    return INTENT_LABEL[intent] if intent else None


def _session_tag(s: Mapping[str, Any]) -> str:
    """``[sport]``, or ``[sport, intent]`` when the intent says more than the sport."""
    intent = _intent_label(s.get("intent"))
    if intent is None or intent == "strength":
        return f"[{s['sport']}]"
    return f"[{s['sport']}, {intent}]"


def _activity_metrics(a: Mapping[str, Any]) -> str:
    """Key metrics of an activity, for judging the stimulus it delivered."""
    parts: list[str] = []
    if a.get("duration_minutes"):
        parts.append(f"{a['duration_minutes']} min")
    if a.get("avg_hr"):
        parts.append(f"avg HR {a['avg_hr']}")
    if a.get("training_effect_aerobic") is not None:
        parts.append(f"aerobic TE {a['training_effect_aerobic']:g}")
    return ", ".join(parts)


def _session_id_tag(s: Mapping[str, Any]) -> str:
    """ " [session_id: 3]", the key a recap session note joins on (#242)."""
    return f" [session_id: {s['id']}]" if s.get("id") is not None else ""


def _session_status(s: Mapping[str, Any]) -> str:
    # Pre-#216 dicts carry only ``completed``.
    return str(s.get("status") or ("done" if s["completed"] else "not_done"))


def _heading(status: str) -> str:
    """A status's label as a section heading: "Adapted (travel)"."""
    label = STATUS_LABEL[status]
    return label[0].upper() + label[1:]


def _format_plan_adherence(adherence: Mapping[str, Any] | None) -> str:
    """The weekly recap's completion block (#217): completion first, planned day last.

    Completion is week-scoped (ADR 0005): a session done on another day, or
    filled by a substitution, is done. On planned day is informational only,
    so nothing here reads as a session not done when it was done on another day.
    """
    if not adherence:
        return "No training plan was active this week."
    sessions = list(adherence.get("sessions", []))
    by_status: dict[str, list[Mapping[str, Any]]] = {"done": [], "substituted": [], "not_done": []}
    for s in sessions:
        by_status.setdefault(_session_status(s), []).append(s)

    counts = [f"{len(by_status['done'])} as planned"]
    for status in ("substituted", "adapted_travel", "not_done", "not_done_travelling"):
        if by_status.get(status):
            counts.append(f"{len(by_status[status])} {STATUS_LABEL[status]}")
    excused = excused_summary(
        adherence.get("excused_sessions", 0), adherence.get("excused_days") or {}
    )
    if excused:
        counts.append(excused)
    lines = [
        f"Completion: {adherence['completed_sessions']}/{adherence['total_sessions']} "
        f"planned sessions done ({adherence['adherence_pct']:g}%) — {', '.join(counts)}"
    ]
    dated = [s for s in sessions if s.get("on_planned_day") is not None]
    if dated:
        on_day = sum(1 for s in dated if s["on_planned_day"])
        lines.append(
            f"On planned day: {on_day}/{len(dated)} (informational only — never a penalty)"
        )

    if by_status["done"]:
        lines.append("Done:")
        for s in by_status["done"]:
            when = f"done {s['actual_day']}" if s.get("actual_day") else "ticked off by hand"
            lines.append(
                f"- {s['title']} {_session_tag(s)} — {when} (planned {s['day']})"
                + _session_id_tag(s)
            )
    for status, heading in (
        ("substituted", f"{_heading('substituted')}:"),
        ("adapted_travel", f"{_heading('adapted_travel')}, counted as done:"),
    ):
        if not by_status.get(status):
            continue
        lines.append(heading)
        for s in by_status[status]:
            sub = s.get("substitute") or {}
            detail = ", ".join(
                p
                for p in (
                    _activity_metrics(sub),
                    f"delivered {_intent_label(sub['intent'])}" if sub.get("intent") else "",
                )
                if p
            )
            on = f" on {s['actual_day']}" if s.get("actual_day") else ""
            lines.append(
                f"- {s['title']} {_session_tag(s)} (planned {s['day']})"
                f" ← {sub.get('title', '?')} [{sub.get('sport', '?')}]{on}"
                + (f": {detail}" if detail else "")
                + _session_id_tag(s)
            )
    for status, heading in (
        ("not_done", f"{_heading('not_done')}:"),
        ("not_done_travelling", f"{_heading('not_done_travelling')}, still in the count:"),
    ):
        if by_status.get(status):
            lines.append(heading)
            for s in by_status[status]:
                lines.append(
                    f"- {s['title']} {_session_tag(s)} (planned {s['day']})" + _session_id_tag(s)
                )
    for status in EXCUSED_LABEL:
        if by_status.get(status):
            lines.append(f"{_heading(status)}, out of the count:")
            for s in by_status[status]:
                lines.append(
                    f"- {s['title']} {_session_tag(s)} (planned {s['day']})" + _session_id_tag(s)
                )
    extras = adherence.get("extras") or []
    if extras:
        lines.append("Extras (unplanned, not counted):")
        for a in extras:
            detail = ", ".join(
                p for p in (_activity_metrics(a), _intent_label(a.get("intent"))) if p
            )
            lines.append(
                f"- {a['title']} [{a['sport']}] on {a['day']}" + (f": {detail}" if detail else "")
            )
    if adherence.get("plan_summary"):
        lines.append(f"Plan summary: {adherence['plan_summary']}")
    return "\n".join(lines)


def weekly_recap_variables(
    *,
    week_start: date,
    plan_adherence: Mapping[str, Any] | None,
    weekly_activities: list[dict[str, Any]],
    health_trends: list[dict[str, Any]],
    mesocycle_context: str | None = None,
    routine: dict[str, Any] | None = None,
    availability: list[dict[str, Any]] | None = None,
    weekly_gym_details: list[dict[str, Any]] | None = None,
    gym_history: list[dict[str, Any]] | None = None,
    sport_profiles: list[dict[str, Any]] | None = None,
    life_context: LifeContext | None = None,
) -> PromptVariables:
    """Format the Coaching context of a weekly recap LLM call."""
    from datetime import timedelta

    week_end = week_start + timedelta(days=6)
    return {
        "week_start": str(week_start),
        "week_end": str(week_end),
        "plan_adherence": _format_plan_adherence(plan_adherence),
        "weekly_activities": _format_activities(weekly_activities),
        "weekly_gym_details": _format_weekly_gym_details(weekly_gym_details or []),
        "gym_history": _format_gym_history(gym_history or []),
        "health_trends": _format_health_trends(health_trends),
        "mesocycle_context": mesocycle_context
        or "No mesocycle configured. Use general progressive programming.",
        "routine_summary": _format_routine_summary(routine),
        "availability": _format_availability(availability or []),
        "sport_profiles": _format_sport_profiles(sport_profiles or []),
        "life_context": _format_life_context(life_context),
        "output_schema": _coach_notes_schema(WeeklyRecapEmail),
    }


def build_weekly_recap_prompt(
    *,
    week_start: date,
    plan_adherence: Mapping[str, Any] | None,
    weekly_activities: list[dict[str, Any]],
    health_trends: list[dict[str, Any]],
    mesocycle_context: str | None = None,
    routine: dict[str, Any] | None = None,
    availability: list[dict[str, Any]] | None = None,
    weekly_gym_details: list[dict[str, Any]] | None = None,
    gym_history: list[dict[str, Any]] | None = None,
    sport_profiles: list[dict[str, Any]] | None = None,
    version: str = "v1",
) -> str:
    """Build the user message for a weekly recap LLM call."""
    variables = weekly_recap_variables(
        week_start=week_start,
        plan_adherence=plan_adherence,
        weekly_activities=weekly_activities,
        health_trends=health_trends,
        mesocycle_context=mesocycle_context,
        routine=routine,
        availability=availability,
        weekly_gym_details=weekly_gym_details,
        gym_history=gym_history,
        sport_profiles=sport_profiles,
    )
    return compile_prompt(Operation.WEEKLY_RECAP, variables, version=version).user


def _format_routine_exercises(exercises: list[dict[str, Any]]) -> str:
    """Format routine exercises for the gym adjustment prompt.

    Groups exercises by superset_group. Supersetted exercises are labelled
    with letter suffixes (1a, 1b) and a [Superset X] tag.
    """
    if not exercises:
        return "No exercises defined."

    # Separate supersetted and standalone exercises while preserving order
    superset_groups: dict[int, list[dict[str, Any]]] = {}
    for ex in exercises:
        sg = ex.get("superset_group")
        if sg is not None:
            superset_groups.setdefault(sg, []).append(ex)

    lines = []
    num = 0
    seen_groups: set[int] = set()
    group_labels: dict[int, str] = {}
    label_counter = 0

    for ex in exercises:
        sg = ex.get("superset_group")
        if sg is not None and sg in superset_groups and len(superset_groups[sg]) > 1:
            if sg in seen_groups:
                continue  # already rendered this group
            seen_groups.add(sg)
            if sg not in group_labels:
                group_labels[sg] = chr(ord("A") + label_counter)
                label_counter += 1
            num += 1
            label = group_labels[sg]
            for j, gex in enumerate(superset_groups[sg]):
                suffix = chr(ord("a") + j)
                exercise_id = gex.get("exercise_id") or "custom"
                line = (
                    f"{num}{suffix}. {gex['exercise_name']} — "
                    f"{gex['sets']} sets x {gex['rep_range']} "
                    f"[exercise_id: {exercise_id}] [Superset {label}]"
                )
                if gex.get("notes"):
                    line += f" ({gex['notes']})"
                lines.append(line)
        else:
            num += 1
            exercise_id = ex.get("exercise_id") or "custom"
            line = (
                f"{num}. {ex['exercise_name']} — {ex['sets']} sets x {ex['rep_range']} "
                f"[exercise_id: {exercise_id}]"
            )
            if ex.get("notes"):
                line += f" ({ex['notes']})"
            lines.append(line)

    return "\n".join(lines)


def _format_gym_performance(performance: list[dict[str, Any]]) -> str:
    """Format last week's gym performance data."""
    if not performance:
        return "No data from last week (first week or no logged gym sessions)."
    lines = []
    for exercise, sets in _group_exercise_details(performance):
        lines.append(f"\n**{exercise}**")
        for d in sets:
            weight = d.get("weight_kg")
            reps = d.get("reps")
            rpe = d.get("rpe")
            parts = [f"  Set {d.get('set_index', '?')}:"]
            if weight is not None:
                parts.append(f"{weight}kg")
            if reps is not None:
                parts.append(f"x{reps}")
            if rpe is not None:
                parts.append(f"RPE {rpe}")
            lines.append(" ".join(parts))
    return "\n".join(lines)


def _format_pace(distance_meters: float, duration_minutes: int, sport: str) -> str:
    """Format pace from distance and duration based on sport type."""
    if sport == "swimming":
        # Pace per 100m
        pace_seconds = (duration_minutes * 60) / (distance_meters / 100)
        mins = int(pace_seconds // 60)
        secs = int(pace_seconds % 60)
        return f"{mins}:{secs:02d}/100m"
    else:
        # Pace per km for running/cardio
        pace_seconds = (duration_minutes * 60) / (distance_meters / 1000)
        mins = int(pace_seconds // 60)
        secs = int(pace_seconds % 60)
        return f"{mins}:{secs:02d}/km"


def _format_cardio_activities(activities: list[dict[str, Any]]) -> str:
    """Format cardio activities with distance, pace, and HR data."""
    if not activities:
        return "No recent cardio activities."
    lines = []
    for a in activities:
        sport = a.get("sport", "unknown")
        title = a.get("title", "Untitled")
        start = a.get("start_time", "?")
        duration = a.get("duration_minutes")
        distance = a.get("distance_meters")

        parts = []
        if duration:
            parts.append(f"{duration} min")
        if distance:
            if distance >= 1000:
                parts.append(f"{distance / 1000:.1f}km")
            else:
                parts.append(f"{distance:.0f}m")
            if duration and distance > 0:
                parts.append(f"pace {_format_pace(distance, duration, sport)}")
        avg_hr = a.get("avg_hr")
        if avg_hr:
            parts.append(f"avg HR {avg_hr}")
        max_hr = a.get("max_hr")
        if max_hr:
            parts.append(f"max HR {max_hr}")
        te = a.get("training_effect_aerobic")
        if te:
            parts.append(f"TE {te}")

        detail = f" ({', '.join(parts)})" if parts else ""
        lines.append(f"- {start}: {title} [{sport}]{detail}")
    return "\n".join(lines)


def _format_cardio_slots(slots: list[dict[str, Any]]) -> str:
    """Format available cardio slots (sport pre-assigned per slot)."""
    if not slots:
        return "No cardio slots available."
    lines = []
    for s in slots:
        day = s.get("day_name", f"Day {s.get('day_of_week', '?')}")
        sport = s.get("sport", "cardio")
        lines.append(f"- {day} — {sport.capitalize()}")
    return "\n".join(lines)


def _format_health_trends_averaged(averaged: dict[str, Any]) -> str:
    """Format averaged health trends as a concise single block."""
    if not averaged:
        return "No recent health data."
    days = averaged.get("days_with_data", "?")
    lines = [f"Based on {days} days of data:"]
    metric_labels = {
        "resting_hr": "Avg Resting HR",
        "avg_hr": "Avg HR",
        "hrv_status": "Avg HRV (ms)",
        "sleep_duration_minutes": "Avg Sleep (min)",
        "sleep_score": "Avg Sleep Score",
        "avg_stress": "Avg Stress",
        "training_readiness": "Avg Training Readiness",
        "training_load": "Avg Training Load",
        "body_battery_morning": "Avg Body Battery (morning)",
        "vo2_max": "VO2 Max",
        "training_status": "Training Status",
        "hrv_status_text": "HRV Status",
        "load_focus": "Load Focus",
    }
    for key, label in metric_labels.items():
        val = averaged.get(key)
        if val is not None:
            if key == "training_status":
                val = _format_training_status(val)
            elif key == "load_focus":
                val = _format_load_focus(val)
            lines.append(f"- {label}: {val}")
    return "\n".join(lines)


def _format_last_week_training_log(activities: list[dict[str, Any]]) -> str:
    """Format all last-week activities for the gym adjustment prompt.

    Shows all sports. For gym activities with nested gym_details, shows set-level data.
    """
    if not activities:
        return "No training logged last week."
    parts = []
    for a in activities:
        sport = a.get("sport", "unknown")
        title = a.get("title", "Untitled")
        start = a.get("start_time", "?")
        duration = a.get("duration_minutes")
        extras: list[str] = []
        if duration:
            extras.append(f"{duration} min")
        distance = a.get("distance_meters")
        if distance:
            extras.append(f"{distance:.0f}m")
            if duration and distance > 0 and sport != "gym":
                extras.append(f"pace {_format_pace(distance, duration, sport)}")
        avg_hr = a.get("avg_hr")
        if avg_hr:
            extras.append(f"avg HR {avg_hr}")
        detail = f" ({', '.join(extras)})" if extras else ""
        parts.append(f"- {start}: {title} [{sport}]{detail}")

        # Append gym set details if present
        gym_details = a.get("gym_details")
        if gym_details:
            for ex, sets in _group_exercise_details(gym_details):
                parts.append(f"  **{ex}**")
                for d in sets:
                    rpe = d.get("rpe")
                    set_parts = [f"    Set {d.get('set_index', '?')}:"]
                    weight_reps = _format_weight_reps(d)
                    if weight_reps:
                        set_parts.append(weight_reps)
                    if rpe is not None:
                        set_parts.append(f"RPE {rpe}")
                    parts.append(" ".join(set_parts))
    return "\n".join(parts)


def gym_adjustment_variables(
    *,
    routine_day_name: str,
    routine_exercises: list[dict[str, Any]],
    health_trends: dict[str, Any],
    last_week_activities: list[dict[str, Any]] | None = None,
    mesocycle_context: str | None = None,
    sport_profiles: list[dict[str, Any]] | None = None,
    life_context: LifeContext | None = None,
) -> PromptVariables:
    """Format the Coaching context of a gym adjustment LLM call."""
    return {
        "routine_day_name": routine_day_name,
        "routine_exercises": _format_routine_exercises(routine_exercises),
        "last_week_activities": _format_last_week_training_log(last_week_activities or []),
        "health_trends": _format_health_trends_averaged(health_trends),
        "mesocycle_context": mesocycle_context
        or "No mesocycle configured. Use general progressive programming.",
        "sport_profiles": _format_sport_profiles(sport_profiles or []),
        "life_context": _format_life_context(life_context),
    }


def build_gym_adjustment_prompt(
    *,
    routine_day_name: str,
    routine_exercises: list[dict[str, Any]],
    health_trends: dict[str, Any],
    last_week_activities: list[dict[str, Any]] | None = None,
    mesocycle_context: str | None = None,
    sport_profiles: list[dict[str, Any]] | None = None,
    version: str = BUNDLED_VERSION,
) -> str:
    """Build the user message for a gym adjustment LLM call."""
    variables = gym_adjustment_variables(
        routine_day_name=routine_day_name,
        routine_exercises=routine_exercises,
        health_trends=health_trends,
        last_week_activities=last_week_activities,
        mesocycle_context=mesocycle_context,
        sport_profiles=sport_profiles,
    )
    return compile_prompt(Operation.GYM_ADJUSTMENT, variables, version=version).user


def cardio_plan_variables(
    *,
    cardio_slots: list[dict[str, Any]],
    last_week_cardio: list[dict[str, Any]],
    health_trends: list[dict[str, Any]],
    mesocycle_context: str | None = None,
    sport_profiles: list[dict[str, Any]] | None = None,
    life_context: LifeContext | None = None,
) -> PromptVariables:
    """Format the Coaching context of a cardio plan LLM call."""
    return {
        "cardio_slots": _format_cardio_slots(cardio_slots),
        "last_week_cardio": _format_cardio_activities(last_week_cardio),
        "health_trends": _format_health_trends(health_trends),
        "mesocycle_context": mesocycle_context
        or "No mesocycle configured. Use general progressive programming.",
        "sport_profiles": _format_sport_profiles(sport_profiles or []),
        "life_context": _format_life_context(life_context),
        "output_schema": _coach_notes_schema(CardioPlanV5),
    }


def build_cardio_plan_prompt(
    *,
    cardio_slots: list[dict[str, Any]],
    last_week_cardio: list[dict[str, Any]],
    health_trends: list[dict[str, Any]],
    mesocycle_context: str | None = None,
    sport_profiles: list[dict[str, Any]] | None = None,
    version: str = BUNDLED_VERSION,
) -> str:
    """Build the user message for a cardio plan LLM call."""
    variables = cardio_plan_variables(
        cardio_slots=cardio_slots,
        last_week_cardio=last_week_cardio,
        health_trends=health_trends,
        mesocycle_context=mesocycle_context,
        sport_profiles=sport_profiles,
    )
    return compile_prompt(Operation.CARDIO_PLAN, variables, version=version).user


PROMPT_VARIABLES: Mapping[Operation, Callable[..., PromptVariables]] = {
    Operation.DAILY_BRIEFING: daily_briefing_variables,
    Operation.GYM_ADJUSTMENT: gym_adjustment_variables,
    Operation.CARDIO_PLAN: cardio_plan_variables,
    Operation.POST_WORKOUT: post_workout_variables,
    Operation.WEEKLY_RECAP: weekly_recap_variables,
}
"""Each operation's variable-map builder, keyed by operation."""
