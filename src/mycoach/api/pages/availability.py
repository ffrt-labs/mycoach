"""Availability input page — set weekly training availability slots."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.availability import WeeklyAvailability

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _current_monday(ref: date | None = None) -> date:
    """Return the Monday of the current week relative to `ref` (default: today)."""
    d = ref or date.today()
    return d - timedelta(days=d.weekday())


def _next_monday(ref: date | None = None) -> date:
    """Return the Monday of the next week relative to `ref` (default: today)."""
    d = ref or date.today()
    days_ahead = 7 - d.weekday()  # weekday(): 0=Mon
    return d + timedelta(days=days_ahead)


@router.get("/availability", response_class=HTMLResponse)
async def availability_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    week: str = Query("next", pattern="^(current|next)$"),
) -> HTMLResponse:
    """Render the availability input page for the selected week."""
    current_mon = _current_monday()
    next_mon = _next_monday()
    target_monday = current_mon if week == "current" else next_mon
    week_label = "this week" if week == "current" else "next week"

    # Fetch existing slots for target week
    result = await session.execute(
        select(WeeklyAvailability)
        .where(
            WeeklyAvailability.user_id == USER_ID,
            WeeklyAvailability.week_start == target_monday,
        )
        .order_by(WeeklyAvailability.day_of_week)
    )
    existing_slots = list(result.scalars().all())

    # Build a lookup by day_of_week for pre-filling the form
    slots_by_day: dict[int, WeeklyAvailability] = {}
    for slot in existing_slots:
        slots_by_day[slot.day_of_week] = slot

    # Build week days with dates
    week_days = []
    for i in range(7):
        day_date = target_monday + timedelta(days=i)
        existing = slots_by_day.get(i)
        week_days.append(
            {
                "day_of_week": i,
                "day_name": DAY_NAMES[i],
                "date": day_date,
                "slot": existing,
            }
        )

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "availability.html",
        {
            "active_page": "availability",
            "week": week,
            "week_label": week_label,
            "week_start": target_monday,
            "week_start_str": target_monday.strftime("%b %d"),
            "week_end_str": (target_monday + timedelta(days=6)).strftime("%b %d, %Y"),
            "week_days": week_days,
        },
    )
