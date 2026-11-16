"""Email sender with SMTP and Resend API backends.

Sends coaching emails (daily briefing, weekly plan, post-workout, weekly recap).
Backend is selected based on configuration: Resend API key takes precedence over SMTP.
"""

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

from mycoach.coaching.body import BodySummary
from mycoach.coaching.hrv import format_ms, hrv_band_text, hrv_status_word
from mycoach.coaching.steps import StepsSummary
from mycoach.config import Settings, get_settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


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
) -> bool:
    """Send the daily coaching briefing email.

    ``steps`` is the code-rendered 7-day steps strip (#196), from the DB, not the LLM.
    ``hrv`` is today's health snapshot; its HRV line is rendered by code, not the LLM.
    ``body`` is the code-rendered Body block (#198); None leaves it out.
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
        },
    )
    return send_email(settings.email_to, "MyCoach — Daily Briefing", html, settings)


def send_weekly_plan(
    summary: str,
    sessions: list[dict],
    week_start: str,
    availability_source: str | None = None,
    settings: Settings | None = None,  # type: ignore[type-arg]
) -> bool:
    """Send the weekly training plan email.

    ``availability_source`` is ``"declared"`` or ``"default"`` (or None for plans
    generated before the standing-schedule feature existed). When ``"default"``,
    the email tells the reader the week was planned against their default
    schedule rather than something they set.
    """
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


def send_weekly_recap(content: dict, week_start: str, settings: Settings | None = None) -> bool:  # type: ignore[type-arg]
    """Send weekly recap email."""
    if settings is None:
        settings = get_settings()
    html = _render_template(
        "weekly_recap.html",
        {"recap": content, "week_start": week_start, "dashboard_url": _dashboard_url(settings)},
    )
    return send_email(settings.email_to, "MyCoach — Weekly Recap", html, settings)
