"""Weekly plan page — full plan view with expandable sessions."""

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.plan import PlannedSession, WeeklyPlan

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the weekly plan page with all sessions."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    # Fetch current week's active plan
    plan_result = await session.execute(
        select(WeeklyPlan).where(
            WeeklyPlan.user_id == USER_ID,
            WeeklyPlan.week_start == monday,
            WeeklyPlan.status == "active",
        )
    )
    plan = plan_result.scalar_one_or_none()

    # Fetch all sessions for the plan, ordered by day
    sessions_by_day: dict[int, dict] = {}
    total_sessions = 0
    completed_sessions = 0

    if plan:
        sessions_result = await session.execute(
            select(PlannedSession)
            .where(PlannedSession.plan_id == plan.id)
            .order_by(PlannedSession.day_of_week)
        )
        for ps in sessions_result.scalars().all():
            total_sessions += 1
            if ps.completed:
                completed_sessions += 1
            details = None
            if ps.details:
                try:
                    details = json.loads(ps.details)
                except (json.JSONDecodeError, TypeError):
                    details = None
            sessions_by_day[ps.day_of_week] = {
                "session": ps,
                "details": details,
                "day_name": DAY_NAMES[ps.day_of_week],
                "is_today": ps.day_of_week == today.weekday(),
            }

    # Build full week (Mon-Sun) with rest days
    week_days = []
    for i in range(7):
        day_date = monday + timedelta(days=i)
        if i in sessions_by_day:
            week_days.append({
                **sessions_by_day[i],
                "date": day_date,
            })
        else:
            week_days.append({
                "session": None,
                "details": None,
                "day_name": DAY_NAMES[i],
                "is_today": i == today.weekday(),
                "date": day_date,
            })

    adherence_pct = (
        round(completed_sessions / total_sessions * 100) if total_sessions > 0 else 0
    )

    week_end = monday + timedelta(days=6)

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "plan.html",
        {
            "active_page": "plan",
            "plan": plan,
            "week_days": week_days,
            "week_start_str": monday.strftime("%b %d"),
            "week_end_str": week_end.strftime("%b %d, %Y"),
            "total_sessions": total_sessions,
            "completed_sessions": completed_sessions,
            "adherence_pct": adherence_pct,
        },
    )
