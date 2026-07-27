"""Email sender with SMTP and Resend API backends.

Sends coaching emails (daily briefing, weekly plan, post-workout, weekly recap).
Backend is selected based on configuration: Resend API key takes precedence over SMTP.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

from mycoach.config import Settings, get_settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def _render_template(template_name: str, context: dict) -> str:  # type: ignore[type-arg]
    """Render a Jinja2 email template with the given context."""
    template = _jinja_env.get_template(template_name)
    return template.render(**context)


# Display label/unit for each DailyBriefingKeyMetrics field, in the order
# emails should show them. Keys absent or None are skipped.
_KEY_METRICS_DISPLAY: dict[str, tuple[str, str]] = {
    "body_battery": ("Body Battery", ""),
    "hrv_status": ("HRV Status", ""),
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


def _format_exercise(exercise: dict) -> tuple[str, str]:  # type: ignore[type-arg]
    """Turn one planned gym exercise into a (name, "4x8 @ 60kg - RPE 8") pair."""
    detail = f"{exercise.get('sets', '?')}×{exercise.get('reps', '?')}"
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


def _send_via_resend(settings: Settings, to: str, subject: str, html: str) -> bool:
    """Send email using Resend API."""
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
        return True
    except Exception:
        logger.exception("Resend send failed")
        return False


def _send_via_smtp(settings: Settings, to: str, subject: str, html: str) -> bool:
    """Send email using SMTP."""
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
        return True
    except Exception:
        logger.exception("SMTP send failed")
        return False


def send_email(to: str, subject: str, html: str, settings: Settings | None = None) -> bool:
    """Send an email using the configured backend.

    Returns True on success, False on failure.
    """
    if settings is None:
        settings = get_settings()

    if not settings.email_enabled:
        logger.debug("Email disabled, skipping send to %s", to)
        return False

    if settings.email_resend_api_key:
        return _send_via_resend(settings, to, subject, html)
    elif settings.email_smtp_host:
        return _send_via_smtp(settings, to, subject, html)
    else:
        logger.warning("No email backend configured (no Resend API key or SMTP host)")
        return False


def send_daily_briefing(content: dict, settings: Settings | None = None) -> bool:  # type: ignore[type-arg]
    """Send the daily coaching briefing email."""
    if settings is None:
        settings = get_settings()
    briefing = dict(content)
    if briefing.get("key_metrics"):
        briefing["key_metrics"] = _format_key_metrics(briefing["key_metrics"])
    html = _render_template(
        "daily_briefing.html",
        {"briefing": briefing, "dashboard_url": _dashboard_url(settings)},
    )
    return send_email(settings.email_to, "MyCoach — Daily Briefing", html, settings)


def send_weekly_plan(
    summary: str,
    sessions: list[dict],
    week_start: str,
    settings: Settings | None = None,  # type: ignore[type-arg]
) -> bool:
    """Send the weekly training plan email."""
    if settings is None:
        settings = get_settings()
    display_sessions = [
        {**s, "details": _format_session_details(s.get("details"))} for s in sessions
    ]
    html = _render_template(
        "weekly_plan.html",
        {
            "summary": summary,
            "sessions": display_sessions,
            "week_start": week_start,
            "dashboard_url": _dashboard_url(settings),
        },
    )
    return send_email(settings.email_to, "MyCoach — Weekly Plan", html, settings)


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


def send_weekly_recap(content: dict, week_start: str, settings: Settings | None = None) -> bool:  # type: ignore[type-arg]
    """Send weekly recap email."""
    if settings is None:
        settings = get_settings()
    html = _render_template(
        "weekly_recap.html",
        {"recap": content, "week_start": week_start, "dashboard_url": _dashboard_url(settings)},
    )
    return send_email(settings.email_to, "MyCoach — Weekly Recap", html, settings)
