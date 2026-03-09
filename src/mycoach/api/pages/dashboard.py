"""Dashboard page — today's readiness, health metrics, planned workout."""

import json
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.coaching import CoachingInsight
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.plan import PlannedSession, WeeklyPlan

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_AVG_FIELDS = [
    "resting_hr", "hrv_status", "sleep_score", "sleep_duration_minutes",
    "steps", "avg_stress", "body_battery_morning", "training_readiness",
]


def _compute_7day_averages(snapshots: list[DailyHealthSnapshot]) -> dict[str, Any]:
    """Compute averages for key metrics over a list of snapshots."""
    if not snapshots:
        return {}
    avgs: dict[str, Any] = {}
    for field in _AVG_FIELDS:
        values = [getattr(s, field) for s in snapshots if getattr(s, field) is not None]
        if values:
            avg = sum(values) / len(values)
            avgs[field] = round(avg, 1) if isinstance(values[0], float) else round(avg)
    # Prefer Garmin's hrv_7day_avg from most recent snapshot that has it
    for s in reversed(snapshots):
        if s.hrv_7day_avg is not None:
            avgs["hrv_7day_avg"] = round(s.hrv_7day_avg, 1)
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

    # Fetch today's health snapshot
    health_result = await session.execute(
        select(DailyHealthSnapshot).where(
            DailyHealthSnapshot.user_id == USER_ID,
            DailyHealthSnapshot.snapshot_date == today,
        )
    )
    health = health_result.scalar_one_or_none()

    # Fetch yesterday's snapshot
    yesterday = today - timedelta(days=1)
    yesterday_result = await session.execute(
        select(DailyHealthSnapshot).where(
            DailyHealthSnapshot.user_id == USER_ID,
            DailyHealthSnapshot.snapshot_date == yesterday,
        )
    )
    yesterday_health = yesterday_result.scalar_one_or_none()

    # Fetch last 7 days for averages
    week_ago = today - timedelta(days=7)
    week_result = await session.execute(
        select(DailyHealthSnapshot).where(
            DailyHealthSnapshot.user_id == USER_ID,
            DailyHealthSnapshot.snapshot_date >= week_ago,
            DailyHealthSnapshot.snapshot_date <= today,
        ).order_by(DailyHealthSnapshot.snapshot_date)
    )
    week_snapshots = list(week_result.scalars().all())
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

    # Last Garmin sync time
    garmin_last_sync = await session.scalar(
        select(func.max(DailyHealthSnapshot.created_at)).where(
            DailyHealthSnapshot.user_id == USER_ID,
            DailyHealthSnapshot.data_source == "garmin",
        )
    )

    templates: Jinja2Templates = request.app.state.templates

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_page": "dashboard",
            "today_str": today.strftime("%B %d, %Y"),
            "weekday": DAY_NAMES[weekday_num],
            "health": health,
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
