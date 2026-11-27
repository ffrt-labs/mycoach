"""Weekly plan page — full plan view with expandable sessions."""

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.context import (
    done_session_ids,
    get_open_this_week,
    week_completion_of,
)
from mycoach.config import get_settings
from mycoach.database import get_db
from mycoach.email.coach_notes import NOTES_UNAVAILABLE
from mycoach.email.plan_views import (
    plan_block_rows,
    plan_notes_unavailable,
    stored_plan_notes,
)
from mycoach.localtime import WEEKDAY_NAMES, local_today
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.plan import PlannedSession, WeeklyPlan

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP


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
    today = local_today(get_settings().timezone)
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
    notes_fell_back = False

    if plan:
        sessions_result = await session.execute(
            select(PlannedSession)
            .where(PlannedSession.plan_id == plan.id)
            .order_by(PlannedSession.day_of_week)
        )
        planned = sessions_result.scalars().all()
        notes_fell_back = plan_notes_unavailable(plan, planned)
        # Week completion (ADR 0005), the count Open this week uses: a session
        # done on another day, or substituted, is done though nobody ticked it.
        completion = await week_completion_of(session, USER_ID, plan, today)
        done_ids = done_session_ids(completion, planned)
        if completion is not None:
            total_sessions = completion["total_sessions"]
            completed_sessions = completion["completed_sessions"]
        else:
            total_sessions = len(planned)
            completed_sessions = len(done_ids)
        for ps in planned:
            details = None
            if ps.details:
                try:
                    details = json.loads(ps.details)
                except (json.JSONDecodeError, TypeError):
                    details = None
            sessions_by_day[ps.day_of_week] = {
                "session": ps,
                "done": ps.id in done_ids,
                "details": details,
                # A v5 cardio session's Blocks (#243); none for gym and v4 sessions.
                "blocks": plan_block_rows(details if isinstance(details, dict) else None),
                "day_name": WEEKDAY_NAMES[ps.day_of_week],
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
                    "day_name": WEEKDAY_NAMES[i],
                    "is_today": is_current_week and i == today.weekday(),
                    "date": day_date,
                }
            )

    # The week's work still owed (#218); only the current week has a "today".
    open_this_week = (
        await get_open_this_week(session, USER_ID, today) if plan and is_current_week else None
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
            "open_this_week": open_this_week,
            "plan_notes": stored_plan_notes(plan.coach_notes) if plan else None,
            "plan_notes_unavailable": notes_fell_back,
            "notes_unavailable": NOTES_UNAVAILABLE,
        },
    )
