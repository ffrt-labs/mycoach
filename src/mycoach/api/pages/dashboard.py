"""Dashboard page — today's readiness, health metrics, planned workout."""

import json
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.coaching.athlete import athlete_id_for
from mycoach.coaching.context import get_today_health
from mycoach.coaching.recovery_data import missing_recovery_data
from mycoach.database import get_db
from mycoach.models.coaching import CoachingInsight
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.scheduler.briefing_window import load_briefing_window

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

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


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the dashboard page with today's data."""
    today = date.today()
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
            briefing_data = json.loads(briefing.content)
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
            "weekday": DAY_NAMES[weekday_num],
            "health": health_snapshot,
            "yesterday_health": yesterday_health,
            "avg_metrics": avg_metrics,
            "briefing": briefing,
            "briefing_data": briefing_data,
            "plan": plan,
            "today_session": today_session,
            "today_session_details": today_session_details,
            "garmin_last_sync": garmin_last_sync,
        },
    )
