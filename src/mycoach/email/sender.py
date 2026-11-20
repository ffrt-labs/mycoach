"""Email sender with SMTP and Resend API backends.

Sends coaching emails (daily briefing, weekly plan, post-workout, weekly recap).
Backend is selected based on configuration: Resend API key takes precedence over SMTP.
"""

import logging
import smtplib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape
from jinja2.utils import Namespace

from mycoach.coaching.body import BodySummary
from mycoach.coaching.context import OpenThisWeek
from mycoach.coaching.hrv import format_ms, hrv_band_text, hrv_status_word
from mycoach.coaching.life_context import (
    STATUS_LABEL,
    DayEffect,
    Episode,
    LifeContext,
    excused_reasons,
    excused_summary,
    ramp_back_length,
)
from mycoach.coaching.steps import StepsSummary
from mycoach.config import Settings, get_settings
from mycoach.email.life_views import life_banner, open_week_view
from mycoach.email.tones import (
    JINJA_GLOBALS,
    LIFE_CONTEXT_STYLE,
    QUALITY_INTENTS,
    SESSION_STATUS_CHIP,
    Chip,
)
from mycoach.localtime import WEEKDAY_NAMES
from mycoach.models.life_context import EpisodeKind, Equipment, IllnessSeverity
from mycoach.models.plan import INTENT_LABEL

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)
_jinja_env.globals.update(JINJA_GLOBALS)
# A fresh layout state per email: tracks the first section after the hero (#231).
_jinja_env.globals["ui_layout"] = lambda: Namespace(after_hero=True)


class EmailSendError(RuntimeError):
    """A backend was asked to send a message and refused, with its reason.

    Raised instead of returning False so the reason the backend gave survives to
    whoever reads the failure. A scheduled job's ``JobRun.error`` is the only
    durable account of why a run failed; collapsing "Resend says the sender
    domain is unverified" into a bare False forced that reader back into the
    logs to re-derive it. A False return now means only that no backend was
    available to attempt the send at all.
    """


def _render_template(template_name: str, context: dict) -> str:  # type: ignore[type-arg]
    """Render a Jinja2 email template with the given context."""
    template = _jinja_env.get_template(template_name)
    return template.render(**context)


# Display label/unit for each DailyBriefingKeyMetrics field, in the order
# emails should show them. Keys absent or None are skipped.
_KEY_METRICS_DISPLAY: dict[str, tuple[str, str]] = {
    "body_battery": ("Body Battery", ""),
    "hrv_last_night_avg": ("Avg HRV", "ms"),
    # Briefings stored before the hrv_status rename keep the number under the
    # old key; both never appear together, so they can share a label.
    "hrv_status": ("Avg HRV", "ms"),
    "hrv_status_text": ("HRV Status", ""),
    "sleep_score": ("Sleep Score", ""),
    "training_readiness": ("Training Readiness", ""),
    "resting_hr": ("Resting HR", "bpm"),
}


def _format_key_metrics(key_metrics: dict) -> dict[str, str]:  # type: ignore[type-arg]
    """Turn the raw DailyBriefingKeyMetrics fields into label -> display value.

    Insertion order follows _KEY_METRICS_DISPLAY, which is the order emails show.
    """
    rows: dict[str, str] = {}
    for key, (label, unit) in _KEY_METRICS_DISPLAY.items():
        value = key_metrics.get(key)
        if value is None:
            continue
        rows[label] = f"{value} {unit}" if unit else str(value)
    return rows


ROUTINE_MAX_SETS = 10
"""More working sets of one exercise than this is a routine typo, not a prescription (#187)."""

ROUTINE_ERROR_PLANNED_SETS = 3
"""What the plan email shows in place of a set count over ``ROUTINE_MAX_SETS`` (#234)."""


def _is_routine_error(sets: object) -> bool:
    return isinstance(sets, int) and sets > ROUTINE_MAX_SETS


def _format_exercise(exercise: dict) -> tuple[str, str]:  # type: ignore[type-arg]
    """Turn one planned gym exercise into a (name, "4x8 @ 60kg - RPE 8") pair.

    A routine-error set count shows as the planned "3*", the asterisk pointing at
    the Routine error notice.
    """
    sets = exercise.get("sets", "?")
    if _is_routine_error(sets):
        sets = f"{ROUTINE_ERROR_PLANNED_SETS}*"
    detail = f"{sets}×{exercise.get('reps', '?')}"
    if exercise.get("target_weight_kg"):
        detail += f" @ {exercise['target_weight_kg']}kg"
    if exercise.get("rpe"):
        detail += f" · RPE {exercise['rpe']}"
    return str(exercise.get("name", "Exercise")), detail


# Label and unit suffix for cardio detail keys the planner commonly emits. Any
# key not listed falls back to a title-cased version of itself with no unit.
_CARDIO_DETAIL_DISPLAY: dict[str, tuple[str, str]] = {
    "target_pace_min_per_km": ("Target Pace", "min/km"),
    "target_pace": ("Target Pace", ""),
    "hr_zone": ("HR Zone", ""),
    "target_hr_zone": ("HR Zone", ""),
    "distance_km": ("Distance", "km"),
    "target_distance_km": ("Distance", "km"),
    "intensity": ("Intensity", ""),
    "rpe": ("RPE", ""),
}


def _format_cardio_detail(key: str, value: object) -> tuple[str, str] | None:
    """Turn one raw cardio detail entry into a (label, display value) pair.

    Returns None for entries that should not be shown in the email.
    """
    text = str(value).strip()
    if not text:
        return None
    if isinstance(value, bool):
        text = "Yes" if value else "No"

    label, unit = _CARDIO_DETAIL_DISPLAY.get(key, (key.replace("_", " ").title(), ""))
    return label, f"{text} {unit}" if unit else text


def _format_session_details(details: dict | None) -> dict[str, str]:  # type: ignore[type-arg]
    """Flatten a planned session's details JSON into label -> display value.

    Gym sessions store {"exercises": [...]}; cardio sessions store flat scalars
    such as target_pace_min_per_km. Returns {} when there is nothing to show, so
    the template's `{% if session.details %}` guard hides the table.
    """
    if not isinstance(details, dict):
        return {}

    exercises = details.get("exercises")
    if isinstance(exercises, list):
        return dict(_format_exercise(ex) for ex in exercises if isinstance(ex, dict))

    rows: dict[str, str] = {}
    for key, value in details.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        row = _format_cardio_detail(key, value)
        if row is not None:
            rows[row[0]] = row[1]
    return rows


def _dashboard_url(settings: Settings) -> str:
    """Build the dashboard link used for every email's CTA button."""
    return f"{settings.app_base_url}/dashboard"


def _life_context_url(settings: Settings) -> str:
    """The dashboard's Life context section, where illness and travel are logged."""
    return f"{settings.app_base_url}/#life-context"


def _default_schedule_url(settings: Settings) -> str:
    """Build the link straight to the standing-schedule tab of the availability page."""
    return f"{settings.app_base_url}/availability?week=default"


def _send_via_resend(settings: Settings, to: str, subject: str, html: str) -> None:
    """Send email using Resend API.

    Returns only once Resend has accepted the message; raises ``EmailSendError``
    carrying Resend's own words if it refuses.
    """
    resend.api_key = settings.email_resend_api_key
    try:
        resend.Emails.send(
            {
                "from": settings.email_from,
                "to": [to],
                "subject": subject,
                "html": html,
            }
        )
    except Exception as e:
        logger.exception("Resend send failed")
        raise EmailSendError(f"Resend rejected the message: {e}") from e


def _send_via_smtp(settings: Settings, to: str, subject: str, html: str) -> None:
    """Send email using SMTP.

    Returns only once the server has accepted the message; raises
    ``EmailSendError`` carrying its own words if it refuses.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(settings.email_smtp_host, settings.email_smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if settings.email_smtp_user:
                server.login(settings.email_smtp_user, settings.email_smtp_password)
            server.sendmail(settings.email_from, to, msg.as_string())
    except Exception as e:
        logger.exception("SMTP send failed")
        raise EmailSendError(
            f"SMTP host {settings.email_smtp_host}:{settings.email_smtp_port} "
            f"rejected the message: {e}"
        ) from e


def send_email(to: str, subject: str, html: str, settings: Settings | None = None) -> bool:
    """Send an email using the configured backend.

    Returns True once a backend has accepted the message, and False when there
    was no backend to attempt it — email switched off, or none configured. A
    backend that attempts the send and refuses raises ``EmailSendError`` so its
    reason is not lost.
    """
    if settings is None:
        settings = get_settings()

    if not settings.email_enabled:
        logger.debug("Email disabled, skipping send to %s", to)
        return False

    if settings.email_resend_api_key:
        _send_via_resend(settings, to, subject, html)
    elif settings.email_smtp_host:
        _send_via_smtp(settings, to, subject, html)
    else:
        logger.warning("No email backend configured (no Resend API key or SMTP host)")
        return False
    return True


def _format_hrv_line(snapshot: dict | None) -> str | None:  # type: ignore[type-arg]
    """Today's HRV line, rendered from the DB snapshot (#197), or None without HRV data.

    Leads with the 7-day avg against Garmin's baseline band, last night as context:
    ``HRV 52 ms 7-day avg · your range 47–58 · Balanced · last night 48 ms``. Before
    Garmin has a band the range and status give way to "Building baseline".
    """
    if not snapshot:
        return None
    weekly = snapshot.get("hrv_7day_avg")
    last_night = snapshot.get("hrv_status")  # last night's ms (rename is #205)
    if weekly is None and last_night is None:
        return None
    parts = [f"HRV {format_ms(weekly)} ms 7-day avg" if weekly is not None else "HRV"]
    band = hrv_band_text(snapshot)
    if band is not None:
        parts.append(f"your range {band}")
        status = snapshot.get("hrv_status_text")
        if status:
            parts.append(hrv_status_word(status))
    else:
        parts.append("Building baseline")
    if last_night is not None:
        parts.append(f"last night {format_ms(last_night)} ms")
    return " · ".join(parts)


def _signed(value: float, places: int) -> str:
    """``+0.20`` / ``−0.70`` with a real minus sign."""
    return f"{value:+.{places}f}".replace("-", "−")


def _body_view(body: BodySummary | None, settings: Settings) -> dict[str, Any] | None:
    """Display strings for the Body block (_body_block.html); None renders no block."""
    if body is None:
        return None
    rows: list[dict[str, str | None]] = []
    if body.weight is not None:
        w = body.weight
        rows.append(
            {
                "label": "Weight",
                "badge": "stale" if w.stale else None,
                "sub": w.when,
                "value": f"{w.latest_kg:.1f} kg",
            }
        )
        if w.trend_kg is not None and w.rate_kg_per_week is not None:
            rows.append(
                {
                    "label": "Trend",
                    "sub": "28-day rate",
                    "value": f"{w.trend_kg:.1f} kg · {_signed(w.rate_kg_per_week, 2)} kg/wk",
                }
            )
    if body.waist is not None:
        waist = body.waist
        rows.append({"label": "Waist", "sub": waist.when, "value": f"{waist.latest_cm:.1f} cm"})
        if waist.ratio is not None:
            rows.append(
                {"label": "Waist/height", "sub": "target < 0.50", "value": f"{waist.ratio:.2f}"}
            )
        if waist.change_cm is not None:
            change = (
                f"{_signed(waist.change_cm, 1)} cm in 4 weeks"
                if waist.change_meaningful
                else "no change"
            )
            rows.append({"label": "4-week change", "value": change})
    nudges = []
    if body.weigh_in_due:
        nudges.append("⏰ Time to weigh in")
    if body.waist_check_due:
        nudges.append("📏 Waist check due")
    return {
        "empty": body.empty,
        "rows": rows,
        "nudges": nudges,
        "url": f"{settings.app_base_url}/body",
    }


def send_daily_briefing(
    content: dict,  # type: ignore[type-arg]
    settings: Settings | None = None,
    *,
    steps: StepsSummary | None = None,
    hrv: dict | None = None,  # type: ignore[type-arg]
    body: BodySummary | None = None,
    open_this_week: OpenThisWeek | None = None,
    life: LifeContext | None = None,
) -> bool:
    """Send the daily coaching briefing email.

    ``steps`` is the code-rendered 7-day steps strip (#196), from the DB, not the LLM.
    ``hrv`` is today's health snapshot; its HRV line is rendered by code, not the LLM.
    ``body`` is the code-rendered Body block (#198); None leaves it out.
    ``open_this_week`` is the week's work still owed (#233); None leaves it out.
    ``life`` is today's Life context: its banner tops the email (#233). None, or an
    ordinary day, leaves it out.
    """
    if settings is None:
        settings = get_settings()
    briefing = dict(content)
    if briefing.get("key_metrics"):
        briefing["key_metrics"] = _format_key_metrics(briefing["key_metrics"])
    html = _render_template(
        "daily_briefing.html",
        {
            "briefing": briefing,
            "steps": steps,
            "dashboard_url": _dashboard_url(settings),
            "hrv_line": _format_hrv_line(hrv),
            "body": _body_view(body, settings),
            "open_week": open_week_view(open_this_week),
            "banner": life_banner(life),
            "life_context_url": _life_context_url(settings),
        },
    )
    return send_email(settings.email_to, "MyCoach — Daily Briefing", html, settings)


def _plan_duration(minutes: int) -> str:
    """ "4 h 30 min", "2 h" or "45 min"."""
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _plan_totals(sessions: list[dict]) -> dict[str, str]:  # type: ignore[type-arg]
    """The hero's count and total time, computed from the planned sessions."""
    n = len(sessions)
    minutes = sum(s.get("duration_minutes") or 0 for s in sessions)
    return {
        "count": f"{n} session{'' if n == 1 else 's'}",
        "total": _plan_duration(minutes) if minutes else "",
    }


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Sport -> (chip style, week-strip short label). Unknown sports render neutral, upper-cased.
_PLAN_SPORT: dict[str, tuple[str, str]] = {
    "gym": ("warm", "GYM"),
    "strength": ("warm", "GYM"),
    "swimming": ("info", "SWIM"),
    "swim": ("info", "SWIM"),
    "cycling": ("info", "BIKE"),
    "running": ("good", "RUN"),
    "run": ("good", "RUN"),
    "padel": ("good", "PADEL"),
    "rest": ("neutral", "REST"),
}


def _plan_sport(sport: str | None) -> tuple[str, str]:
    key = (sport or "").lower()
    return _PLAN_SPORT.get(key, ("neutral", key.upper()))


def _plan_day_index(session: dict) -> int | None:  # type: ignore[type-arg]
    """The session's weekday (0 = Monday), from ``day_of_week`` or else its ``day_name``."""
    day = session.get("day_of_week")
    if isinstance(day, int) and 0 <= day <= 6:
        return day
    name = str(session.get("day_name") or "").strip().lower()
    return next((i for i, d in enumerate(_WEEKDAYS) if d.lower() == name), None)


def _week_strip(
    sessions: list[dict],  # type: ignore[type-arg]
    travel_days: set[int],
) -> list[dict]:  # type: ignore[type-arg]
    """Seven cells, Monday first: each day's sessions (sport, minutes, intent label), whether
    a quality intent is planned that day, and whether a trip covers it."""
    days: list[dict] = [  # type: ignore[type-arg]
        {"abbr": name[:3].upper(), "sessions": [], "quality": False, "travel": i in travel_days}
        for i, name in enumerate(_WEEKDAYS)
    ]
    for s in sessions:
        i = _plan_day_index(s)
        if i is None:
            continue
        style, label = _plan_sport(s.get("sport"))
        intent = s.get("intent")
        days[i]["sessions"].append(
            {
                "sport": label,
                "style": style,
                "minutes": s.get("duration_minutes"),
                "intent": INTENT_LABEL.get(intent) if intent else None,
            }
        )
        if intent in QUALITY_INTENTS:
            days[i]["quality"] = True
    return days


_EQUIPMENT_TEXT: dict[str, str] = {
    Equipment.NONE: "no equipment",
    Equipment.HOTEL_GYM: "hotel gym",
    Equipment.FULL_GYM: "full gym",
}


def _trip_days(trip: Episode, week_start: date) -> list[int]:
    """The weekdays (0 = Monday) of ``week_start``'s week the trip covers; an open trip
    runs to the end of the week."""
    week_end = week_start + timedelta(days=6)
    first = max(trip.started_on, week_start)
    last = min(trip.ended_on or week_end, week_end)
    return [(first - week_start).days + i for i in range((last - first).days + 1)]


def _trip_notice(trip: Episode, week_start: date) -> str:
    """ "Trip Wed–Fri · hotel gym · 6 h ahead": what the athlete logged, nothing inferred."""
    days = _trip_days(trip, week_start)
    starts_in_week = trip.started_on >= week_start
    ends_in_week = trip.ended_on is not None and trip.ended_on <= week_start + timedelta(days=6)
    first, last = _WEEKDAYS[days[0]][:3], _WEEKDAYS[days[-1]][:3]
    if starts_in_week and ends_in_week:
        span = first if first == last else f"{first}–{last}"
    elif starts_in_week:
        span = f"from {first}"
    elif ends_in_week:
        span = f"until {last}"
    else:
        span = "all week"
    parts = [f"Trip {span}"]
    if trip.equipment is not None:
        parts.append(_EQUIPMENT_TEXT[trip.equipment])
    if trip.tz_shift_hours:
        side = "behind" if trip.tz_shift_hours < 0 else "ahead"
        parts.append(f"{abs(trip.tz_shift_hours):g} h {side}")
    if trip.no_training:
        parts.append("no training possible")
    if trip.ended_on is None:
        parts.append("return date not set")
    return " · ".join(parts)


def _illness_notice(illness: Episode) -> str:
    """ "Ill since Fri 13 Nov · mild"."""
    text = f"Ill since {illness.started_on.strftime('%a %-d %b')}"
    return f"{text} · {illness.severity}" if illness.severity else text


def _life_context_notices(life: LifeContext | None, week_start: date) -> list[dict[str, str]]:
    """A notice per trip touching the planned week, and per open illness. Facts only: how
    the plan handles them is the coach's to say, not the notice's."""
    if life is None:
        return []
    week_end = week_start + timedelta(days=6)
    notices = []
    for e in life.episodes:
        if e.kind == EpisodeKind.TRAVEL and e.started_on <= week_end:
            if e.ended_on is None or e.ended_on >= week_start:
                effect = DayEffect.TRAVEL_NO_TRAINING if e.no_training else DayEffect.TRAVEL
                text = _trip_notice(e, week_start)
            else:
                continue
        elif e.kind == EpisodeKind.ILLNESS and e.is_open:
            effect = DayEffect.MILD if e.severity == IllnessSeverity.MILD else DayEffect.SICK
            text = _illness_notice(e)
        else:
            continue
        notices.append({"label": "Life context", "tone": LIFE_CONTEXT_STYLE[effect], "text": text})
    return notices


def _travel_days(life: LifeContext | None, week_start: date) -> set[int]:
    if life is None:
        return set()
    week_end = week_start + timedelta(days=6)
    return {
        day
        for e in life.episodes
        if e.kind == EpisodeKind.TRAVEL
        and e.started_on <= week_end
        and (e.ended_on is None or e.ended_on >= week_start)
        for day in _trip_days(e, week_start)
    }


def _default_schedule_notices(
    availability_source: str | None, settings: Settings
) -> list[dict[str, str]]:
    """The week was planned against the standing schedule, not something the athlete set."""
    if availability_source != "default":
        return []
    return [
        {
            "label": "Default schedule",
            "tone": "neutral",
            "text": "Planned against your standing schedule — you didn't set this week, "
            "so it was filled in from your default.",
            "link_url": _default_schedule_url(settings),
            "link_text": "Change it →",
        }
    ]


def _and_join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _routine_error_notices(
    sessions: list[dict],  # type: ignore[type-arg]
    settings: Settings,
) -> list[dict[str, str]]:
    """A Routine error notice per day whose routine lists more than ``ROUTINE_MAX_SETS``
    sets of an exercise: "Friday lists Deadlift and Pull Up at 36 sets, Split Squat at
    32. Planned as 3." It is an app problem, so it links to the routine to fix it."""
    notices = []
    for s in sessions:
        details = s.get("details")
        exercises = details.get("exercises") if isinstance(details, dict) else None
        if not isinstance(exercises, list):
            continue
        by_sets: dict[int, list[str]] = {}
        for ex in exercises:
            if isinstance(ex, dict) and _is_routine_error(ex.get("sets")):
                by_sets.setdefault(ex["sets"], []).append(str(ex.get("name", "Exercise")))
        if not by_sets:
            continue
        groups = [f"{_and_join(names)} at {n}" for n, names in by_sets.items()]
        groups[0] += " sets"
        notices.append(
            {
                "label": "Routine error",
                "tone": "watch",
                "text": f"{s.get('day_name')} lists {', '.join(groups)}. "
                f"Planned as {ROUTINE_ERROR_PLANNED_SETS}.",
                "link_url": f"{settings.app_base_url}/routine",
                "link_text": "Fix routine →",
            }
        )
    return notices


def send_weekly_plan(
    summary: str,
    sessions: list[dict],
    week_start: str,
    availability_source: str | None = None,
    settings: Settings | None = None,  # type: ignore[type-arg]
    life: LifeContext | None = None,
) -> bool:
    """Send the weekly training plan email.

    ``availability_source`` is ``"declared"`` or ``"default"`` (or None for plans
    generated before the standing-schedule feature existed). When ``"default"``,
    the email tells the reader the week was planned against their default
    schedule rather than something they set.

    ``life`` is the planned week's Life context: a trip touching the week marks its
    days on the week strip, and each trip or open illness gets a factual notice.
    """
    if settings is None:
        settings = get_settings()
    monday = date.fromisoformat(week_start)
    display_sessions = [
        {
            **s,
            "sport_style": _plan_sport(s.get("sport"))[0],
            "details": _format_session_details(s.get("details")),
        }
        for s in sessions
    ]
    html = _render_template(
        "weekly_plan.html",
        {
            "summary": summary,
            "totals": _plan_totals(sessions),
            "week_strip": _week_strip(sessions, _travel_days(life, monday)),
            "notices": [
                *_life_context_notices(life, monday),
                *_default_schedule_notices(availability_source, settings),
                *_routine_error_notices(sessions, settings),
            ],
            "sessions": display_sessions,
            "week_start": week_start,
            "availability_source": availability_source,
            "dashboard_url": _dashboard_url(settings),
            "default_schedule_url": _default_schedule_url(settings),
        },
    )
    return send_email(settings.email_to, "MyCoach — Weekly Plan", html, settings)


def send_no_availability(week_start: str, settings: Settings | None = None) -> bool:  # type: ignore[type-arg]
    """Send the "no plan generated" email for a week with no declared or default availability."""
    if settings is None:
        settings = get_settings()
    html = _render_template(
        "no_availability.html",
        {
            "week_start": week_start,
            "default_schedule_url": _default_schedule_url(settings),
        },
    )
    return send_email(settings.email_to, "MyCoach — No Plan Generated", html, settings)


def send_garmin_auth_failed(reason: str, settings: Settings | None = None) -> bool:
    """Send the "MyCoach can't log in to Garmin" alert, with the client's reason."""
    if settings is None:
        settings = get_settings()
    html = _render_template(
        "garmin_auth_failed.html",
        {"reason": reason, "dashboard_url": _dashboard_url(settings)},
    )
    return send_email(settings.email_to, "MyCoach — Garmin login failed", html, settings)


def _format_last_upload(uploaded_at: datetime, settings: Settings) -> str:
    """Render a last-upload timestamp in the user's timezone, e.g. "Aug 19 at 05:20"."""
    local = uploaded_at.astimezone(ZoneInfo(settings.timezone))
    return local.strftime("%b %-d at %H:%M")


def send_briefing_unavailable(
    day: str,
    failures: int,
    is_broken: bool,
    can_still_retry: bool,
    detail: str | None = None,
    last_upload: datetime | None = None,
    device_name: str | None = None,
    settings: Settings | None = None,
    *,
    steps: StepsSummary | None = None,
    hrv: dict | None = None,  # type: ignore[type-arg]
    body: BodySummary | None = None,
) -> bool:
    """Tell the user why there is no briefing this morning.

    ``steps`` is the same code-rendered steps strip the briefing carries, so the
    numbers from the DB still reach the reader when the LLM could not run (#196).
    ``hrv`` is today's health snapshot, if any: its code-rendered HRV line still
    reaches the athlete when the LLM could not write a briefing (#197).
    ``body`` is the Body block (#198), carried for the same reason.

    The wording turns entirely on ``is_broken``, because the two causes need
    opposite things from the reader. Absent data is theirs to fix — the watch
    uploads over Bluetooth only, so the actionable fact is when it last reached
    Garmin at all. A broken pipeline is ours; telling someone to check their
    Bluetooth when the real fault was a validation error in our own code is
    worse than sending nothing, so that branch says so plainly and names the
    error instead.

    ``failures`` counts errors only, never skips. A morning of four data-less
    skips followed by one real error is one error, and telling the reader we
    hit five would send them looking for a fault that isn't there. The
    data-absent branches quote no count at all: how many times we asked is our
    business, and the reader can only act on when their watch last synced.
    """
    if settings is None:
        settings = get_settings()

    if is_broken:
        attempt_word = "attempt" if failures == 1 else "attempts"
        headline = (
            f"MyCoach could not generate your briefing this morning — "
            f"{failures} {attempt_word} hit an error. This is a fault on our "
            f"side, not with your watch."
        )
    elif last_upload is not None:
        headline = (
            f"Your watch hasn't uploaded to Garmin since "
            f"{_format_last_upload(last_upload, settings)}. Check that Bluetooth is "
            f"on and that the Garmin Connect app has synced — MyCoach has nothing "
            f"to read your recovery from until it does."
        )
    else:
        headline = (
            "Garmin still has no recovery data for today. Check that Bluetooth "
            "is on and that the Garmin Connect app has synced."
        )

    if can_still_retry:
        next_step = (
            "MyCoach will keep checking every 15 minutes and will send your briefing "
            "as soon as the data lands. It stops generating at 14:00 — a briefing "
            "about this morning's recovery isn't worth much by the evening."
        )
    elif is_broken:
        next_step = (
            "Retries have stopped for today, so no further attempt will be made "
            "automatically. The dashboard's regenerate button still works once "
            "the cause is fixed."
        )
    else:
        next_step = (
            "The 14:00 cutoff has passed, so no briefing will be generated for "
            "today. Tomorrow's runs as normal once your watch has synced."
        )

    html = _render_template(
        "briefing_unavailable.html",
        {
            "day": day,
            "headline": headline,
            "next_step": next_step,
            "detail": detail,
            "last_upload": (_format_last_upload(last_upload, settings) if last_upload else None),
            "device_name": device_name,
            "steps": steps,
            "dashboard_url": _dashboard_url(settings),
            "hrv_line": _format_hrv_line(hrv),
            "body": _body_view(body, settings),
        },
    )
    return send_email(settings.email_to, "MyCoach — No Briefing Today", html, settings)


def send_post_workout(content: dict, activity_title: str, settings: Settings | None = None) -> bool:  # type: ignore[type-arg]
    """Send post-workout analysis email."""
    if settings is None:
        settings = get_settings()
    html = _render_template(
        "post_workout.html",
        {
            "analysis": content,
            "activity_title": activity_title,
            "dashboard_url": _dashboard_url(settings),
        },
    )
    return send_email(
        settings.email_to, f"MyCoach — Post-Workout: {activity_title}", html, settings
    )


def completion_line(adherence: Mapping[str, Any] | None) -> str | None:
    """The week's completion as the recap email shows it: "4/5 done · 1 substituted".

    Substitutions are already in the done count (#216); excused sessions (#220) are
    out of the denominator: "2/2 done · 3 excused (ill 3 days)". Sessions adapted on
    a trip (#221) are done too: "2/3 done · 1 adapted (travel)". None without a plan.
    """
    if not adherence or not (adherence.get("total_sessions") or adherence.get("excused_sessions")):
        return None
    line = f"{adherence['completed_sessions']}/{adherence['total_sessions']} done"
    for key, status in (
        ("substituted_sessions", "substituted"),
        ("adapted_sessions", "adapted_travel"),
    ):
        if adherence.get(key):
            line += f" · {adherence[key]} {STATUS_LABEL[status]}"
    excused = excused_summary(
        adherence.get("excused_sessions", 0), adherence.get("excused_days") or {}
    )
    if excused:
        line += f" · {excused}"
    return line


@dataclass(frozen=True)
class CompletionHero:
    """The recap hero (#232): "4/5 done" large, "· 1 substituted" beside it."""

    h1: str
    rest: str | None = None
    on_planned_day: str | None = None


def completion_hero(adherence: Mapping[str, Any] | None) -> CompletionHero:
    """Split the week's Completion line for the recap hero.

    The H1 is the line's first segment. A week whose every session was excused
    shows "4 excused" and the reason, never "0/0 done"; no plan, "No plan this
    week". On planned day counts only sessions whose day is known, and is
    informational, never a score.
    """
    line = completion_line(adherence)
    if adherence is None or line is None:
        return CompletionHero("No plan this week")
    if not adherence.get("total_sessions"):
        reasons = excused_reasons(adherence.get("excused_days") or {})
        h1, rest = f"{adherence['excused_sessions']} excused", reasons or None
    else:
        h1, _, rest_line = line.partition(" · ")
        rest = rest_line or None
    known = [
        s["on_planned_day"]
        for s in adherence.get("sessions") or []
        if s.get("on_planned_day") is not None
    ]
    return CompletionHero(
        h1,
        None if rest is None else f"· {rest}",
        f"On planned day {sum(known)}/{len(known)} · informational" if known else None,
    )


def _short_date(iso_day: str) -> str:
    """ "2026-11-02" → "2 Nov"."""
    day = date.fromisoformat(iso_day)
    return f"{day.day} {day:%b}"


_EXCUSED_STATUSES = frozenset({"excused_ill", "excused_travel"})


@dataclass(frozen=True)
class SessionRow:
    """One planned session in the recap's Sessions list (#232), all from the Completion."""

    day: str  # "MON"
    title: str
    meta: str  # "Gym · strength"
    chip: Chip | None
    detail: str | None  # "Done Wednesday", or what filled it
    struck: bool  # excused: out of the count, no action asked


def _weekday_index(day: str) -> int:
    return WEEKDAY_NAMES.index(day) if day in WEEKDAY_NAMES else len(WEEKDAY_NAMES)


def _sport_meta(sport: str, intent: str | None) -> str:
    """ "Gym · strength": the sport, then the Session intent's label when it has one."""
    label = INTENT_LABEL.get(intent) if intent else None
    return sport.capitalize() + (f" · {label}" if label else "")


def _substitute_line(sub: Mapping[str, Any]) -> str:
    """ "← Padel Sun · 101 min · avg HR 118 · aerobic TE 2.7"; what Garmin didn't record
    is left out."""
    head = f"← {sub['title']}"
    if sub.get("day"):
        head += f" {str(sub['day'])[:3]}"
    parts = [head]
    if sub.get("duration_minutes") is not None:
        parts.append(f"{sub['duration_minutes']} min")
    if sub.get("avg_hr") is not None:
        parts.append(f"avg HR {sub['avg_hr']}")
    if sub.get("training_effect_aerobic") is not None:
        parts.append(f"aerobic TE {sub['training_effect_aerobic']:.1f}")
    return " · ".join(parts)


def session_rows(adherence: Mapping[str, Any] | None) -> list[SessionRow]:
    """The week's planned sessions in planned-day order, each with its status chip.

    A session done on another day says which ("Done Wednesday"); a substituted or
    adapted one shows what filled it; an excused one is struck through.
    """
    rows = []
    for s in sorted(
        (adherence or {}).get("sessions") or [], key=lambda s: _weekday_index(s["day"])
    ):
        status = s["status"]
        detail = None
        if s.get("substitute"):
            detail = _substitute_line(s["substitute"])
        elif status == "done" and s.get("on_planned_day") is False and s.get("actual_day"):
            detail = f"Done {s['actual_day']}"
        rows.append(
            SessionRow(
                day=str(s["day"])[:3].upper(),
                title=s["title"],
                meta=_sport_meta(s["sport"], s.get("intent")),
                chip=SESSION_STATUS_CHIP.get(status),
                detail=detail,
                struck=status in _EXCUSED_STATUSES,
            )
        )
    return rows


@dataclass(frozen=True)
class ExtraRow:
    """An Extra activity in the recap: training load only, never counted."""

    day: str  # "SAT"
    text: str  # "Walk · 45 min · recovery"


def extra_rows(adherence: Mapping[str, Any] | None) -> list[ExtraRow]:
    """The week's Extra activities in the order they were done."""
    rows = []
    for a in sorted((adherence or {}).get("extras") or [], key=lambda a: _weekday_index(a["day"])):
        parts = [str(a["title"])]
        if a.get("duration_minutes") is not None:
            parts.append(f"{a['duration_minutes']} min")
        if a.get("intent") and a["intent"] in INTENT_LABEL:
            parts.append(INTENT_LABEL[a["intent"]])
        rows.append(ExtraRow(str(a["day"])[:3].upper(), " · ".join(parts)))
    return rows


_EQUIPMENT_LABEL: dict[str, str] = {
    Equipment.NONE: "no equipment",
    Equipment.HOTEL_GYM: "hotel gym",
    Equipment.FULL_GYM: "full gym",
}
"""A trip's equipment as the dashboard names it (``templates/_life_context.html``)."""


def _day_span(first: date, last: date) -> str:
    """ "11–15 Nov", "30 Oct–3 Nov", or "7 Nov" for one day."""
    if first == last:
        return f"{first.day} {first:%b}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day}–{last.day} {last:%b}"
    return f"{first.day} {first:%b}–{last.day} {last:%b}"


def episode_line(episode: Episode) -> str:
    """One Life context episode touching the recap week.

    "Ill 11–15 Nov · sick · ramp-back to 20 Nov", "Ill since 6 Nov · sick",
    "Trip 9–12 Nov · hotel gym · no training possible".
    """
    head = "Ill" if episode.kind == EpisodeKind.ILLNESS else "Trip"
    if episode.ended_on is None:
        when = f"since {episode.started_on.day} {episode.started_on:%b}"
    else:
        when = _day_span(episode.started_on, episode.ended_on)
    parts = [f"{head} {when}"]
    if episode.kind == EpisodeKind.ILLNESS:
        severity = episode.severity or IllnessSeverity.SICK
        parts.append(severity.value)
        if episode.ended_on is not None:
            length = ramp_back_length(severity, (episode.ended_on - episode.started_on).days + 1)
            ends = episode.ended_on + timedelta(days=length)
            parts.append(f"ramp-back to {ends.day} {ends:%b}")
    else:
        if episode.equipment is not None:
            parts.append(_EQUIPMENT_LABEL[episode.equipment])
        if episode.no_training:
            parts.append("no training possible")
    return " · ".join(parts)


def _log_episode_url(settings: Settings, week_start: str) -> str:
    """The dashboard's Life context form, prefilled for the recapped week (#236)."""
    start = date.fromisoformat(week_start)
    end = start + timedelta(days=6)
    return (
        f"{_dashboard_url(settings)}?log=illness&from={start.isoformat()}"
        f"&to={end.isoformat()}#life-context"
    )


def send_weekly_recap(
    content: dict,  # type: ignore[type-arg]
    week_start: str,
    settings: Settings | None = None,
    adherence: Mapping[str, Any] | None = None,
    episodes: Sequence[Episode] | None = None,
) -> bool:
    """Send weekly recap email.

    ``adherence`` is the recapped week's Completion, from the DB, not the LLM:
    it leads the email as the hero (#232), above the coach's summary.
    ``episodes`` are the Life context episodes touching the week; None (not
    read) leaves the Life context section out rather than claim there were none.
    """
    if settings is None:
        settings = get_settings()
    html = _render_template(
        "weekly_recap.html",
        {
            "recap": content,
            "week_label": _short_date(week_start),
            "dashboard_url": _dashboard_url(settings),
            "hero": completion_hero(adherence),
            "sessions": session_rows(adherence),
            "extras": extra_rows(adherence),
            "episodes": None if episodes is None else [episode_line(e) for e in episodes],
            "log_episode_url": _log_episode_url(settings, week_start),
        },
    )
    return send_email(settings.email_to, "MyCoach — Weekly Recap", html, settings)
