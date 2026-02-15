"""Email sender with SMTP and Resend API backends.

Sends coaching emails (daily briefing, weekly plan, post-workout, sleep, weekly recap).
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
    html = _render_template("daily_briefing.html", {"briefing": content})
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
    html = _render_template(
        "weekly_plan.html",
        {"summary": summary, "sessions": sessions, "week_start": week_start},
    )
    return send_email(settings.email_to, "MyCoach — Weekly Plan", html, settings)


def send_post_workout(content: dict, activity_title: str, settings: Settings | None = None) -> bool:  # type: ignore[type-arg]
    """Send post-workout analysis email."""
    if settings is None:
        settings = get_settings()
    html = _render_template(
        "post_workout.html", {"analysis": content, "activity_title": activity_title}
    )
    return send_email(
        settings.email_to, f"MyCoach — Post-Workout: {activity_title}", html, settings
    )


def send_sleep_coaching(content: dict, settings: Settings | None = None) -> bool:  # type: ignore[type-arg]
    """Send sleep coaching email."""
    if settings is None:
        settings = get_settings()
    html = _render_template("sleep_coaching.html", {"coaching": content})
    return send_email(settings.email_to, "MyCoach — Sleep Coaching", html, settings)


def send_weekly_recap(content: dict, week_start: str, settings: Settings | None = None) -> bool:  # type: ignore[type-arg]
    """Send weekly recap email."""
    if settings is None:
        settings = get_settings()
    html = _render_template("weekly_recap.html", {"recap": content, "week_start": week_start})
    return send_email(settings.email_to, "MyCoach — Weekly Recap", html, settings)
