"""Email sender with SMTP and Resend API backends.

Sends coaching emails (daily briefing, weekly plan, post-workout, weekly recap).
Backend is selected based on configuration: Resend API key takes precedence over SMTP.
"""

import logging
import smtplib
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape
from jinja2.utils import Namespace

from mycoach.coaching.body import BodySummary
from mycoach.coaching.context import OpenThisWeek, PlanAdherence
from mycoach.coaching.email_schemas import (
    DailyBriefingEmail,
    PlanNotes,
    PostWorkoutEmail,
    WeeklyRecapEmail,
)
from mycoach.coaching.hrv import format_ms, hrv_band_text, hrv_status_word
from mycoach.coaching.life_context import Episode, LifeContext
from mycoach.coaching.steps import StepsSummary
from mycoach.config import Settings, get_settings
from mycoach.email.briefing_views import (
    RECOMMENDED_CHIP,
    headline_view,
    last_night_view,
    options_view,
    tonight_view,
)
from mycoach.email.coach_notes import (
    NOTES_UNAVAILABLE,
    block_rows,
    coach_notes,
    ready_chip,
    tip_chip,
)
from mycoach.email.life_views import (
    life_banner,
    life_chip,
    open_week_view,
    week_status,
)
from mycoach.email.links import (
    dashboard_url,
    default_schedule_url,
    insight_url,
    life_context_url,
    log_episode_url,
)
from mycoach.email.plan_views import (
    default_schedule_notices,
    life_context_notices,
    notes_unavailable_notices,
    plan_card,
    plan_totals,
    routine_error_notices,
    travel_days,
    week_strip,
)
from mycoach.email.recap_views import (
    completion_hero,
    episode_line,
    extra_rows,
    lift_rows,
    load_rows,
    quick_replies,
    recovery_tiles,
    session_rows,
    setup_notices,
)
from mycoach.email.recap_views import (
    session_notes as recap_session_notes,
)
from mycoach.email.tones import JINJA_GLOBALS
from mycoach.email.workout_stats import (
    session_intent,
    stat_grid,
    stat_tiles,
    trend_table,
    zone_bar,
)
from mycoach.localtime import local_today
from mycoach.sport_display import sport_display

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
    content: Mapping[str, Any] | DailyBriefingEmail | None,
    settings: Settings | None = None,
    *,
    steps: StepsSummary | None = None,
    hrv: dict | None = None,  # type: ignore[type-arg]
    body: BodySummary | None = None,
    open_this_week: OpenThisWeek | None = None,
    life: LifeContext | None = None,
    insight_id: int | None = None,
) -> bool:
    """Send the daily briefing: Coach notes in their slots, the rest from code (#241).

    ``content`` is the insight's Coach notes (``DailyBriefingEmail``, prompt v5), or
    ``None`` after a ``notes_fallback``: the email then carries its code blocks only,
    under a "Coach notes unavailable today" Notice. Content that does not validate is
    treated as no notes.

    ``steps`` is the code-rendered 7-day steps strip (#196), from the DB, not the LLM.
    ``hrv`` is today's health snapshot: the HRV line (#197) and last night's tiles are
    rendered from it by code.
    ``body`` is the code-rendered Body block (#198); None leaves it out.
    ``open_this_week`` is the week's work still owed (#233); None leaves it out. The
    options link to its sessions by id.
    ``life`` is today's Life context: its banner tops the email (#233). None, or an
    ordinary day, leaves it out.
    ``insight_id`` is the Insight this email renders: with Coach notes, the footer
    links its full analysis (#240).
    """
    if settings is None:
        settings = get_settings()
    notes = coach_notes(content, DailyBriefingEmail)
    options = options_view(notes.options, open_this_week) if notes else None
    html = _render_template(
        "daily_briefing.html",
        {
            "notes": notes,
            "notes_unavailable": NOTES_UNAVAILABLE,
            "headline": headline_view(notes.readiness, notes.headline) if notes else None,
            "options_view": options,
            "recommended_chip": RECOMMENDED_CHIP,
            "last_night_view": last_night_view(hrv),
            "tonight_tips": tonight_view(notes.tonight) if notes else [],
            "steps": steps,
            "dashboard_url": dashboard_url(settings),
            "hrv_line": _format_hrv_line(hrv),
            "body": _body_view(body, settings),
            "open_week": open_week_view(open_this_week, pick=options.pick if options else None),
            "banner": life_banner(life),
            "life_context_url": life_context_url(settings),
            "full_analysis_url": (
                insight_url(settings, insight_id) if notes and insight_id is not None else None
            ),
        },
    )
    return send_email(settings.email_to, "MyCoach — Daily Briefing", html, settings)


def send_weekly_plan(
    summary: str,
    sessions: list[dict],
    week_start: str,
    availability_source: str | None = None,
    settings: Settings | None = None,  # type: ignore[type-arg]
    life: LifeContext | None = None,
    *,
    notes: Mapping[str, Any] | PlanNotes | None = None,
    notes_unavailable: bool = False,
) -> bool:
    """Send the weekly training plan email.

    ``availability_source`` is ``"declared"`` or ``"default"`` (or None for plans
    generated before the standing-schedule feature existed). When ``"default"``,
    the email tells the reader the week was planned against their default
    schedule rather than something they set.

    ``life`` is the planned week's Life context: a trip touching the week marks its
    days on the week strip, and each trip or open illness gets a factual notice.

    ``notes`` are the plan's Coach notes (``PlanNotes``, prompt v5, #243): their focus
    is the lead (else ``summary``) and their Guardrails the week's rules. Each day card
    shows its focus (the session's ``notes``), then an exercise table (gym) or the
    block table (cardio ``details["blocks"]``), then its foot. ``notes_unavailable``
    says the cardio plan fell back: the email carries a "Coach notes unavailable"
    Notice.
    """
    if settings is None:
        settings = get_settings()
    monday = date.fromisoformat(week_start)
    plan_notes = coach_notes(notes, PlanNotes)
    html = _render_template(
        "weekly_plan.html",
        {
            "summary": plan_notes.focus if plan_notes else summary,
            "guardrails": plan_notes.guardrails if plan_notes else [],
            "totals": plan_totals(sessions),
            "week_strip": week_strip(sessions, travel_days(life, monday)),
            "notices": [
                *life_context_notices(life, monday),
                *default_schedule_notices(availability_source, settings),
                *routine_error_notices(sessions, settings),
                *notes_unavailable_notices(notes_unavailable and plan_notes is None),
            ],
            "sessions": [plan_card(s, settings) for s in sessions],
            "week_label": _short_date(week_start),
            "availability_source": availability_source,
            "dashboard_url": dashboard_url(settings),
            "default_schedule_url": default_schedule_url(settings),
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
            "default_schedule_url": default_schedule_url(settings),
        },
    )
    return send_email(settings.email_to, "MyCoach — No Plan Generated", html, settings)


def send_garmin_auth_failed(reason: str, settings: Settings | None = None) -> bool:
    """Send the "MyCoach can't log in to Garmin" alert, with the client's reason."""
    if settings is None:
        settings = get_settings()
    html = _render_template(
        "garmin_auth_failed.html",
        {"reason": reason, "dashboard_url": dashboard_url(settings)},
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
            "dashboard_url": dashboard_url(settings),
            "hrv_line": _format_hrv_line(hrv),
            "body": _body_view(body, settings),
        },
    )
    return send_email(settings.email_to, "MyCoach — No Briefing Today", html, settings)


def _post_workout_eyebrow(activity: Mapping[str, Any] | None, activity_title: str) -> str:
    """ "Swim · Pool swim": the activity's sport, then its title."""
    sport = (activity or {}).get("sport")
    if not sport:
        return activity_title
    return f"{sport_display(str(sport)).label} · {activity_title}"


def send_post_workout(
    content: Mapping[str, Any] | PostWorkoutEmail | None,
    activity_title: str,
    settings: Settings | None = None,
    *,
    activity: Mapping[str, Any] | None = None,
    week: PlanAdherence | None = None,
    life: LifeContext | None = None,
    open_this_week: OpenThisWeek | None = None,
    insight_id: int | None = None,
    planned: Mapping[str, Any] | None = None,
    sets: Sequence[Mapping[str, Any]] | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    """Send the post-workout email: Coach notes in their slots, the rest from code (#238).

    ``content`` is the insight's Coach notes (``PostWorkoutEmail``, prompt v5), or
    ``None`` after a ``notes_fallback``: the email then carries its code blocks only,
    under a "Coach notes unavailable today" Notice. Content that does not validate is
    treated as no notes.

    ``activity`` is the analysed activity; ``week`` is its week's Completion, computed
    at send time: together they say what the activity did for its week (#235). Either
    None leaves that line out. ``life`` is the Life context of the activity's day: a
    ramp-back or travel day chips the verdict. ``open_this_week`` closes the email as
    a compact list; None (no plan this week) leaves it out.

    ``insight_id`` is the Insight revision this email renders: with Coach notes, the
    CTA opens its full analysis (#240). Without either, the CTA opens the dashboard.

    ``planned`` (the planned session the activity answers), ``sets`` (its performed
    sets, with their prescriptions) and ``history`` (the similar activities, newest
    first) feed the code-rendered stat grid, zone bar and trend (#239).
    """
    if settings is None:
        settings = get_settings()
    notes = coach_notes(content, PostWorkoutEmail)
    full_analysis = (
        insight_url(settings, insight_id) if notes is not None and insight_id is not None else None
    )
    html = _render_template(
        "post_workout.html",
        {
            "notes": notes,
            "notes_unavailable": NOTES_UNAVAILABLE,
            "eyebrow": _post_workout_eyebrow(activity, activity_title),
            "blocks": block_rows(notes.next_session) if notes else [],
            "zone_chip": tip_chip(notes.zone_note) if notes and notes.zone_note else None,
            "ready_chip": ready_chip(notes.ready_in_hours) if notes else None,
            "dashboard_url": dashboard_url(settings),
            "full_analysis_url": full_analysis,
            "week_status": week_status(week, activity),
            "life_chip": life_chip(life),
            "open_week": open_week_view(open_this_week),
            "grid": stat_grid(stat_tiles(activity, planned=planned, sets=sets, history=history)),
            "zones": zone_bar(activity, session_intent(activity, planned, week)),
            "trend": trend_table(activity, history),
        },
    )
    return send_email(
        settings.email_to, f"MyCoach — Post-Workout: {activity_title}", html, settings
    )


def _short_date(iso_day: str) -> str:
    """ "2026-11-02" → "2 Nov"."""
    day = date.fromisoformat(iso_day)
    return f"{day.day} {day:%b}"


def send_weekly_recap(
    content: Mapping[str, Any] | WeeklyRecapEmail | None,
    week_start: str,
    settings: Settings | None = None,
    adherence: Mapping[str, Any] | None = None,
    episodes: Sequence[Episode] | None = None,
    today: date | None = None,
    health_days_missing: int | None = None,
    garmin_auth_failed: bool = False,
    *,
    health_week: Sequence[Mapping[str, Any]] | None = None,
    gym_sets: Sequence[Mapping[str, Any]] | None = None,
    has_mesocycle: bool | None = None,
    plan_failed: bool = False,
    insight_id: int | None = None,
) -> bool:
    """Send the weekly recap: Coach notes in their slots, every number from code (#242).

    ``content`` is the insight's Coach notes (``WeeklyRecapEmail``, prompt v5), or
    ``None`` after a ``notes_fallback``: the code blocks then go out under a "Coach
    notes unavailable today" Notice. Content that does not validate counts as none.

    ``adherence`` is the recapped week's Completion, from the DB, not the LLM:
    it leads the email as the hero (#232), above the coach's summary.
    ``episodes`` are the Life context episodes touching the week; None (not
    read) leaves the Life context section out rather than claim there were none.
    ``health_days_missing`` (days of the week with no health snapshot, None if
    unread) and ``garmin_auth_failed`` feed the cause quick replies (#237);
    ``today`` caps their illness prefill at yesterday.
    ``health_week`` (the week's health snapshots) gives the recovery tiles and load
    values, ``gym_sets`` (its gym log) the top sets; ``has_mesocycle`` (None if unread)
    and ``plan_failed`` (the last weekly-plan run failed) the setup notices.
    ``insight_id`` is the recap Insight this email renders: with Coach notes, "Full
    recap →" opens its full analysis (#240); otherwise the dashboard.
    """
    if settings is None:
        settings = get_settings()
    if today is None:
        today = local_today(settings.timezone)
    notes = coach_notes(content, WeeklyRecapEmail)
    full_recap = (
        insight_url(settings, insight_id) if notes is not None and insight_id is not None else None
    )
    html = _render_template(
        "weekly_recap.html",
        {
            "full_recap_url": full_recap or dashboard_url(settings),
            "notes": notes,
            "notes_unavailable": NOTES_UNAVAILABLE,
            "quick_replies": quick_replies(
                adherence,
                episodes,
                date.fromisoformat(week_start),
                today,
                settings,
                health_days_missing,
                garmin_auth_failed,
            ),
            "week_label": _short_date(week_start),
            "dashboard_url": dashboard_url(settings),
            "hero": completion_hero(adherence),
            "sessions": session_rows(
                adherence, recap_session_notes(notes.session_notes, adherence) if notes else None
            ),
            "extras": extra_rows(adherence),
            "episodes": None if episodes is None else [episode_line(e) for e in episodes],
            "log_episode_url": log_episode_url(settings, week_start),
            "recovery_tiles": recovery_tiles(health_week),
            "lifts": lift_rows(gym_sets, notes.lift_notes if notes else ()),
            "loads": load_rows(health_week),
            "setup_notices": setup_notices(
                settings.app_base_url, has_mesocycle=has_mesocycle, plan_failed=plan_failed
            ),
        },
    )
    return send_email(settings.email_to, "MyCoach — Weekly Recap", html, settings)
