"""Loads prompt templates and formats them with context data."""

import json
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


def _format_health(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "No health data available for today."
    lines = []
    field_labels = {
        "resting_hr": "Resting Heart Rate",
        "avg_hr": "Avg HR",
        "max_hr": "Max HR (all day)",
        "hrv_status": "HRV",
        "hrv_7day_avg": "HRV 7-day avg",
        "sleep_duration_minutes": "Sleep duration (min)",
        "sleep_score": "Sleep score",
        "sleep_deep_minutes": "Deep sleep (min)",
        "sleep_light_minutes": "Light sleep (min)",
        "sleep_rem_minutes": "REM sleep (min)",
        "sleep_awake_minutes": "Awake time (min)",
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
        if s.get("notes"):
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
        day_names = [
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
        ]
        day_name = day_names[day["day_of_week"]] if day.get("day_of_week") is not None else "?"
        ex_count = len(day.get("exercises", []))
        lines.append(f"- {day_name}: {day['name']} ({ex_count} exercises)")
    return "\n".join(lines)


def build_daily_briefing_prompt(
    *,
    health_today: dict[str, Any],
    health_trends: list[dict[str, Any]],
    recent_activities: list[dict[str, Any]],
    planned_workout: str | list[dict[str, Any]] | None = None,
    version: str = "v1",
) -> str:
    """Build the user message for a daily briefing LLM call."""
    template = _load_template("daily_briefing.txt", version)
    if isinstance(planned_workout, list):
        formatted_workout = _format_planned_sessions(planned_workout)
    else:
        formatted_workout = planned_workout or "No planned workout for today."
    return template.format(
        health_data=_format_health(health_today),
        health_trends=_format_health_trends(health_trends),
        recent_activities=_format_activities(recent_activities),
        planned_workout=formatted_workout,
    )


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
        "data_source": "Data source",
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


def _format_gym_details(details: list[dict[str, Any]]) -> str:
    """Format gym workout details (sets/reps/weight) for prompt."""
    if not details:
        return "No gym workout details (not a gym session or no data)."
    lines = []
    current_exercise = ""
    for d in details:
        exercise = d.get("exercise_title", "Unknown")
        if exercise != current_exercise:
            current_exercise = exercise
            lines.append(f"\n**{exercise}**")
        set_type = d.get("set_type", "normal")
        weight = d.get("weight_kg")
        reps = d.get("reps")
        rpe = d.get("rpe")
        parts = [f"  Set {d.get('set_index', '?')}"]
        if set_type != "normal":
            parts.append(f"({set_type})")
        if weight is not None:
            parts.append(f"{weight}kg")
        if reps is not None:
            parts.append(f"x{reps}")
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


def _build_post_workout_schema(sport: str, has_planned: bool) -> str:
    """Build the JSON output schema for post-workout analysis, sport-aware."""
    sport_rec = {
        "swimming": "sets, distance, intensity, stroke technique focus",
        "gym": "weights, volume, intensity adjustments",
        "running": "distance, pace targets, interval structure",
    }
    rec_label = sport_rec.get(sport, "volume, intensity, technique adjustments")

    # Schema fields as (key, description) pairs
    schema: list[tuple[str, str]] = [
        ("performance_summary", "Brief overall assessment of the workout quality and effort"),
    ]
    if has_planned:
        schema.append((
            "planned_vs_actual",
            "Comparison to planned session — adherence, deviations, and why",
        ))
    schema.extend([
        (
            "performance_trends",
            "How this session compares to recent similar workouts"
            " — improvements, regressions, plateaus",
        ),
        (
            "hr_analysis",
            "Heart rate zone analysis and cardiovascular response"
            " (or 'No HR data available')",
        ),
        (
            "training_effect_assessment",
            "Assessment of aerobic/anaerobic training effect"
            " and what it means for adaptation",
        ),
        ("key_highlights", '["List of 2-4 specific positive highlights"]'),
        ("areas_for_improvement", '["List of 1-3 areas to work on next"]'),
        (
            "next_session_recommendations",
            f"Specific recommendations for next workout ({rec_label})",
        ),
        (
            "recovery_notes",
            "Post-workout recovery recommendations"
            " based on effort level and health context",
        ),
    ])
    lines = []
    for key, desc in schema:
        if desc.startswith("["):
            lines.append(f'  "{key}": {desc}')
        else:
            lines.append(f'  "{key}": "{desc}"')
    return "{{\n" + ",\n".join(lines) + "\n}}"


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
    sport = activity.get("sport", "unknown")
    has_planned = planned_session is not None

    # Build training status label for glossary
    ts_val = health_context.get("training_status")
    ts_label = _format_training_status(ts_val) if ts_val else "Not available"

    # Build sections conditionally
    sections = [
        "Analyze the following completed workout and provide a post-workout coaching analysis.",
        "",
        "## Glossary",
        "- SWOLF: swim efficiency = seconds + strokes per length. Lower = better.",
        "- EPOC: excess post-exercise oxygen consumption."
        " Indicates training load and recovery need.",
        "- Training Effect: 1-2 minor, 2-3 maintaining,"
        " 3-4 improving, 4-5 highly improving.",
        f"- Training Status: {ts_label}",
        "",
        "## Completed Activity",
        _format_activity_detail(activity),
    ]

    if gym_details:
        sections.extend(["", "## Gym Workout Details", _format_gym_details(gym_details)])

    if has_planned:
        sections.extend(["", "## Planned Session", _format_planned_session(planned_session)])

    sections.extend([
        "",
        "## Recent Similar Activities (last 5 of same sport)",
        _format_activities(similar_activities),
        "",
        "## Today's Health Context",
        _format_health(health_context),
        "",
        "Provide a detailed post-workout analysis."
        + (" Compare actual performance to the planned session." if has_planned else "")
        + " Identify performance trends by comparing to recent similar workouts."
        + " Be specific about what went well and what to improve.",
        "",
        "Respond with a JSON object matching this exact schema:",
        _build_post_workout_schema(sport, has_planned),
    ])

    return "\n".join(sections)



def _fmt_kg(w: float | None) -> str:
    """Format a weight in kg, stripping trailing .0 for whole numbers."""
    if w is None:
        return ""
    return f"{int(w)}kg" if w == int(w) else f"{w}kg"


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
        by_session[key][d.get("exercise_title") or "Unknown"].append(d)

    parts = []
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for (sess_date, sess_title), exercises in sorted(by_session.items()):
        # Try to compute day name
        try:
            from datetime import date as _date

            d_obj = _date.fromisoformat(sess_date)
            day_name = day_names[d_obj.weekday()]
            header = f"**{day_name} {sess_date} — {sess_title}**"
        except Exception:
            header = f"**{sess_date} — {sess_title}**"

        ex_lines = [header]
        for ex_name, sets in exercises.items():
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
            ex_lines.append(f"{ex_name}: {', '.join(set_strs)}")
        parts.append("\n".join(ex_lines))

    return "\n\n".join(parts)


def _format_gym_history(history: list[dict[str, Any]]) -> str:
    """Format gym performance history grouped by exercise, showing week-over-week best set."""
    if not history:
        return "No gym history data (first week or no prior logged gym sessions)."

    # Group by exercise
    from collections import defaultdict

    by_exercise: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        by_exercise[row.get("exercise_title") or "Unknown"].append(row)

    parts = []
    for ex_name in sorted(by_exercise.keys()):
        weeks_data = sorted(by_exercise[ex_name], key=lambda r: r.get("week_start") or "")
        lines = [f"**{ex_name}**"]
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


def _format_plan_adherence(adherence: dict[str, Any] | None) -> str:
    """Format plan adherence data for the weekly recap prompt."""
    if not adherence:
        return "No training plan was active this week."
    lines = [
        f"Plan summary: {adherence.get('plan_summary', 'N/A')}",
        f"Adherence: {adherence['completed_sessions']}/{adherence['total_sessions']} "
        f"sessions completed ({adherence['adherence_pct']}%)",
        "",
        "Session breakdown:",
    ]
    for s in adherence.get("sessions", []):
        status = "DONE" if s["completed"] else "MISSED"
        lines.append(f"- {s['day']}: {s['title']} [{s['sport']}] — {status}")
    return "\n".join(lines)


def build_weekly_recap_prompt(
    *,
    week_start: date,
    plan_adherence: dict[str, Any] | None,
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
    from datetime import timedelta

    week_end = week_start + timedelta(days=6)
    template = _load_template("weekly_recap.txt", version)
    return template.format(
        week_start=str(week_start),
        week_end=str(week_end),
        plan_adherence=_format_plan_adherence(plan_adherence),
        weekly_activities=_format_activities(weekly_activities),
        weekly_gym_details=_format_weekly_gym_details(weekly_gym_details or []),
        gym_history=_format_gym_history(gym_history or []),
        health_trends=_format_health_trends(health_trends),
        mesocycle_context=mesocycle_context
        or "No mesocycle configured. Use general progressive programming.",
        routine_summary=_format_routine_summary(routine),
        availability=_format_availability(availability or []),
        sport_profiles=_format_sport_profiles(sport_profiles or []),
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
        "data_source": activity.data_source,
    }


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
                line = (
                    f"{num}{suffix}. {gex['exercise_name']} — "
                    f"{gex['sets']} sets x {gex['rep_range']} [Superset {label}]"
                )
                if gex.get("notes"):
                    line += f" ({gex['notes']})"
                lines.append(line)
        else:
            num += 1
            line = f"{num}. {ex['exercise_name']} — {ex['sets']} sets x {ex['rep_range']}"
            if ex.get("notes"):
                line += f" ({ex['notes']})"
            lines.append(line)

    return "\n".join(lines)


def _format_gym_performance(performance: list[dict[str, Any]]) -> str:
    """Format last week's gym performance data."""
    if not performance:
        return "No data from last week (first week or no logged gym sessions)."
    lines = []
    current_exercise = ""
    for d in performance:
        exercise = d.get("exercise_title", "Unknown")
        if exercise != current_exercise:
            current_exercise = exercise
            lines.append(f"\n**{exercise}**")
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
        "hrv_status": "Avg HRV",
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
            current_ex = ""
            for d in gym_details:
                ex = d.get("exercise_title", "Unknown")
                if ex != current_ex:
                    current_ex = ex
                    parts.append(f"  **{ex}**")
                w = d.get("weight_kg")
                r = d.get("reps")
                rpe = d.get("rpe")
                set_parts = [f"    Set {d.get('set_index', '?')}:"]
                if w is not None:
                    set_parts.append(f"{w}kg")
                if r is not None:
                    set_parts.append(f"x{r}")
                if rpe is not None:
                    set_parts.append(f"RPE {rpe}")
                parts.append(" ".join(set_parts))
    return "\n".join(parts)


def build_gym_adjustment_prompt(
    *,
    routine_day_name: str,
    routine_exercises: list[dict[str, Any]],
    health_trends: dict[str, Any],
    last_week_activities: list[dict[str, Any]] | None = None,
    mesocycle_context: str | None = None,
    sport_profiles: list[dict[str, Any]] | None = None,
    version: str = "v2",
) -> str:
    """Build the user message for a gym adjustment LLM call."""
    template = _load_template("gym_adjustment.txt", version)
    return template.format(
        routine_day_name=routine_day_name,
        routine_exercises=_format_routine_exercises(routine_exercises),
        last_week_activities=_format_last_week_training_log(last_week_activities or []),
        health_trends=_format_health_trends_averaged(health_trends),
        mesocycle_context=mesocycle_context
        or "No mesocycle configured. Use general progressive programming.",
        sport_profiles=_format_sport_profiles(sport_profiles or []),
    )


def build_cardio_plan_prompt(
    *,
    cardio_slots: list[dict[str, Any]],
    last_week_cardio: list[dict[str, Any]],
    health_trends: list[dict[str, Any]],
    mesocycle_context: str | None = None,
    sport_profiles: list[dict[str, Any]] | None = None,
    version: str = "v2",
) -> str:
    """Build the user message for a cardio plan LLM call."""
    template = _load_template("cardio_plan.txt", version)
    return template.format(
        cardio_slots=_format_cardio_slots(cardio_slots),
        last_week_cardio=_format_cardio_activities(last_week_cardio),
        health_trends=_format_health_trends(health_trends),
        mesocycle_context=mesocycle_context
        or "No mesocycle configured. Use general progressive programming.",
        sport_profiles=_format_sport_profiles(sport_profiles or []),
    )


