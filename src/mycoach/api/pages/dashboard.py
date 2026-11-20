"""Dashboard page — today's readiness, health metrics, planned workout."""

import json
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.coaching import life_context
from mycoach.coaching.athlete import athlete_id_for
from mycoach.coaching.context import (
    get_open_this_week,
    get_today_health,
    sessions_excused_ill_between,
)
from mycoach.coaching.recovery_data import missing_recovery_data
from mycoach.config import get_settings
from mycoach.database import get_db
from mycoach.localtime import WEEKDAY_NAMES, local_today
from mycoach.models.coaching import CoachingInsight
from mycoach.models.life_context import EpisodeKind
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.scheduler.briefing_window import load_briefing_window

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

_AVG_FIELDS = [
    "resting_hr", "hrv_status", "sleep_score", "sleep_duration_minutes",
    "steps", "avg_stress", "body_battery_morning", "training_readiness",
]


def _compute_7day_averages(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute averages for key metrics over a list of snapshots."""
    if not snapshots:
        return {}
    avgs: dict[str, Any] = {}
    for field in _AVG_FIELDS:
        values = [s[field] for s in snapshots if s.get(field) is not None]
        if values:
            avg = sum(values) / len(values)
            avgs[field] = round(avg, 1) if isinstance(values[0], float) else round(avg)
    # Prefer Garmin's hrv_7day_avg from most recent snapshot that has it
    for s in reversed(snapshots):
        if s.get("hrv_7day_avg") is not None:
            avgs["hrv_7day_avg"] = round(s["hrv_7day_avg"], 1)
            break
    return avgs


_PREFILL_FORMS = ("illness", "travel")


def _day_or_none(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _prefill(log: str | None, start: str | None, end: str | None) -> dict[str, Any] | None:
    """A check-in link (#236, ``?log=illness&from&to``): which form opens, with its dates.

    It only fills the form in; nothing is saved until "Tell the coach". A malformed
    date is dropped rather than refusing the page.
    """
    if log not in _PREFILL_FORMS:
        return None
    return {"log": log, "from": _day_or_none(start), "to": _day_or_none(end)}


async def _logged_line(session: AsyncSession, episode_id: int | None, today: date) -> str | None:
    """The one-line confirmation after "Tell the coach" on an illness (#236)."""
    if episode_id is None:
        return None
    row = await life_context.get_episode(session, USER_ID, episode_id)
    if row is None or row.kind != EpisodeKind.ILLNESS:
        return None
    episode = life_context.Episode.from_row(row)
    last = episode.last_day(today)
    excused = await sessions_excused_ill_between(
        session, USER_ID, episode.started_on, last, today=today
    )
    noun = "session" if excused == 1 else "sessions"
    days = life_context.format_days(episode.started_on, last)
    return f"Logged ill {days} · {excused} {noun} excused"


def _briefing_read(content: Any) -> dict[str, Any] | None:
    """Today's briefing in the shape the hero and "The read" print.

    A v5 briefing (Coach notes, #241) is mapped onto the v4 fields: the verdict from
    ``readiness``, the Lead as the recovery status, tonight's tips as the read. A
    briefing stored without Coach notes (``null``) has none. #244 replaces this.
    """
    if not isinstance(content, dict):
        return None
    if "readiness" not in content:
        return content  # v4
    headline = content.get("headline") or {}
    tips = [t.get("text") for t in content.get("tonight") or [] if isinstance(t, dict)]
    return {
        "readiness_verdict": content.get("readiness"),
        "recovery_status": headline.get("summary"),
        "workout_adjustments": None,
        "sleep_recommendation": " · ".join(t for t in tips if t) or None,
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db),
    log: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    logged: int | None = None,
) -> HTMLResponse:
    """Render the dashboard page with today's data.

    ``?log=illness|travel&from&to`` opens that life context form with the dates
    filled in (#236), as the recap's quick replies link to it. ``?logged=<id>``
    confirms an illness just saved.
    """
    # One today: the athlete's local day, as the check-in API and the briefing use.
    today = local_today(get_settings().timezone)
    weekday_num = today.weekday()

    # Health facts come through the health module (plain dicts), never SQL on its schema.
    athlete_id = await athlete_id_for(session, USER_ID)
    health_snapshot = await health.get_snapshot(athlete_id, today)
    yesterday = today - timedelta(days=1)
    yesterday_health = await health.get_snapshot(athlete_id, yesterday)

    # Last 7 days for averages
    week_ago = today - timedelta(days=7)
    week_snapshots = await health.list_snapshots(
        athlete_id, week_ago, until=today, newest_first=False
    )
    avg_metrics = _compute_7day_averages(week_snapshots)

    # Fetch today's daily briefing
    briefing_result = await session.execute(
        select(CoachingInsight).where(
            CoachingInsight.user_id == USER_ID,
            CoachingInsight.insight_date == today,
            CoachingInsight.insight_type == "daily_briefing",
        )
    )
    briefing = briefing_result.scalar_one_or_none()
    # Parse briefing content JSON
    briefing_data = None
    if briefing and briefing.content:
        try:
            briefing_data = _briefing_read(json.loads(briefing.content))
        except (json.JSONDecodeError, TypeError):
            briefing_data = None

    # Fetch current week's plan + today's session
    monday = today - timedelta(days=weekday_num)
    plan_result = await session.execute(
        select(WeeklyPlan).where(
            WeeklyPlan.user_id == USER_ID,
            WeeklyPlan.week_start == monday,
            WeeklyPlan.status == "active",
        )
    )
    plan = plan_result.scalars().first()

    today_session = None
    today_session_details = None
    if plan:
        session_result = await session.execute(
            select(PlannedSession).where(
                PlannedSession.plan_id == plan.id,
                PlannedSession.day_of_week == weekday_num,
            )
        )
        today_session = session_result.scalars().first()
        if today_session and today_session.details:
            try:
                today_session_details = json.loads(today_session.details)
            except (json.JSONDecodeError, TypeError):
                today_session_details = None

    # The week's work still owed (#218), not just today's slot.
    open_this_week = await get_open_this_week(session, USER_ID, today)

    # Life context (#220): an open illness, or the ramp-back after one.
    open_illness = next(
        iter(await life_context.open_episodes(session, USER_ID, EpisodeKind.ILLNESS)), None
    )
    life = await life_context.life_context_for(session, USER_ID, today, today, today=today)
    # Travel (#221): the trip under way, and trips entered in advance.
    trips = [
        e
        for e in await life_context.episodes_between(
            session, USER_ID, today, today + timedelta(days=365)
        )
        if e.kind == EpisodeKind.TRAVEL
    ]
    current_trip = next((t for t in trips if t.started_on <= today), None)
    upcoming_trips = [t for t in trips if t.started_on > today]

    # Last Garmin sync time. updated_at is bumped on every successful sync touch
    # (even ones that change nothing), so it's a truer "last synced" stamp than
    # created_at, which is only ever set once per row.
    last_snapshot_sync = (await health.get_last_sync(athlete_id))["garmin_snapshots"]
    garmin_last_sync = datetime.fromisoformat(last_snapshot_sync) if last_snapshot_sync else None

    # Why there is no briefing, straight from the retry loop's own record. The
    # page is server-rendered Jinja and job_runs is an ordinary table, so this
    # is one query rather than a new API endpoint.
    briefing_banner = None
    if briefing is None:
        window = await load_briefing_window(session)
        briefing_banner = window.banner

    # And why the Generate button would refuse — the same guard the endpoint
    # behind the button now enforces, asked before the click rather than after.
    briefing_blocked_reason = missing_recovery_data(
        await get_today_health(session, USER_ID, today)
    )

    templates: Jinja2Templates = request.app.state.templates

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "briefing_banner": briefing_banner,
            "briefing_blocked_reason": briefing_blocked_reason,
            "active_page": "dashboard",
            "today_str": today.strftime("%B %d, %Y"),
            "weekday": WEEKDAY_NAMES[weekday_num],
            "health": health_snapshot,
            "yesterday_health": yesterday_health,
            "avg_metrics": avg_metrics,
            "briefing": briefing,
            "briefing_data": briefing_data,
            "plan": plan,
            "today_session": today_session,
            "today_session_details": today_session_details,
            "open_this_week": open_this_week,
            "open_illness": open_illness,
            "open_illness_day": (
                (today - open_illness.started_on).days + 1 if open_illness is not None else None
            ),
            "ramp_back": life.ramp_back,
            "prefill": _prefill(log, from_, to),
            "logged_line": await _logged_line(session, logged, today),
            "current_trip": current_trip,
            "current_trip_day": (
                (today - current_trip.started_on).days + 1 if current_trip is not None else None
            ),
            "upcoming_trips": upcoming_trips,
            "garmin_last_sync": garmin_last_sync,
        },
    )
