"""Dashboard page — today's readiness, health metrics, planned workout."""

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.coaching import CoachingInsight
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.plan import PlannedSession, WeeklyPlan

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


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
    plan = plan_result.scalar_one_or_none()

    today_session = None
    today_session_details = None
    if plan:
        session_result = await session.execute(
            select(PlannedSession).where(
                PlannedSession.plan_id == plan.id,
                PlannedSession.day_of_week == weekday_num,
            )
        )
        today_session = session_result.scalar_one_or_none()
        if today_session and today_session.details:
            try:
                today_session_details = json.loads(today_session.details)
            except (json.JSONDecodeError, TypeError):
                today_session_details = None

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_page": "dashboard",
            "today_str": today.strftime("%B %d, %Y"),
            "weekday": DAY_NAMES[weekday_num],
            "health": health,
            "briefing": briefing,
            "briefing_data": briefing_data,
            "plan": plan,
            "today_session": today_session,
            "today_session_details": today_session_details,
        },
    )
