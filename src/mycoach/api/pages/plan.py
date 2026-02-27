"""Weekly plan page — full plan view with expandable sessions."""

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.plan import PlannedSession, WeeklyPlan

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(
    request: Request,
    week: date | None = None,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the weekly plan page with all sessions.

    Query param ``week`` selects the Monday of the week to display.
    Defaults to the current week. Non-Monday dates snap to their Monday.
    """
    today = date.today()
    if week is not None:
        monday = week - timedelta(days=week.weekday())
    else:
        monday = today - timedelta(days=today.weekday())

    current_monday = today - timedelta(days=today.weekday())
    is_current_week = monday == current_monday

    # Fetch the plan for the selected week (prefer active, fall back to any)
    plan_result = await session.execute(
        select(WeeklyPlan).where(
            WeeklyPlan.user_id == USER_ID,
            WeeklyPlan.week_start == monday,
        )
    )
    plans = list(plan_result.scalars().all())
    plan = next((p for p in plans if p.status == "active"), plans[0] if plans else None)

    # Check if availability exists for this week (used when no plan)
    has_availability = False
    if not plan:
        avail_result = await session.execute(
            select(WeeklyAvailability.id).where(
                WeeklyAvailability.user_id == USER_ID,
                WeeklyAvailability.week_start == monday,
            ).limit(1)
        )
        has_availability = avail_result.scalar_one_or_none() is not None

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
                "is_today": is_current_week and ps.day_of_week == today.weekday(),
            }

    # Build full week (Mon-Sun) with rest days
    week_days = []
    for i in range(7):
        day_date = monday + timedelta(days=i)
        if i in sessions_by_day:
            week_days.append(
                {
                    **sessions_by_day[i],
                    "date": day_date,
                }
            )
        else:
            week_days.append(
                {
                    "session": None,
                    "details": None,
                    "day_name": DAY_NAMES[i],
                    "is_today": is_current_week and i == today.weekday(),
                    "date": day_date,
                }
            )

    adherence_pct = round(completed_sessions / total_sessions * 100) if total_sessions > 0 else 0

    week_end = monday + timedelta(days=6)
    prev_monday = monday - timedelta(weeks=1)
    next_monday = monday + timedelta(weeks=1)

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
            "prev_week": prev_monday.isoformat(),
            "next_week": next_monday.isoformat(),
            "is_current_week": is_current_week,
            "has_availability": has_availability,
            "week_start_iso": monday.isoformat(),
        },
    )
