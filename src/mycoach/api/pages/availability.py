"""Availability input page — set weekly training availability slots."""

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.context import get_default_availability
from mycoach.database import get_db
from mycoach.models.availability import DefaultAvailability, WeeklyAvailability

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
    week: str = Query("next", pattern="^(current|next|default)$"),
) -> HTMLResponse:
    """Render the availability input page for the selected week.

    ``week=default`` renders the standing schedule (no dates — it has none).
    ``week=next`` with no declared rows for that week pre-fills the form from
    the standing schedule and marks each pre-filled day as coming from it;
    this is display-only and writes nothing until the user saves.
    """
    templates: Jinja2Templates = request.app.state.templates

    if week == "default":
        default_slots = await get_default_availability(session, USER_ID)
        slots_by_day: dict[int, DefaultAvailability | WeeklyAvailability] = {
            slot.day_of_week: slot for slot in default_slots
        }
        week_days: list[dict[str, Any]] = [
            {
                "day_of_week": i,
                "day_name": DAY_NAMES[i],
                "date": None,
                "slot": slots_by_day.get(i),
                "from_default": False,
            }
            for i in range(7)
        ]

        return templates.TemplateResponse(
            request,
            "availability.html",
            {
                "active_page": "availability",
                "week": week,
                "week_label": "your standing schedule",
                "week_start": None,
                "week_start_str": "Standing schedule",
                "week_end_str": "applies every week by default",
                "week_days": week_days,
            },
        )

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
    slots_by_day = {slot.day_of_week: slot for slot in existing_slots}

    # A declared-empty week (only possible for "next") is pre-filled from the
    # standing default, purely for display — nothing is written on this GET.
    # Materialization only ever happens inside plan generation.
    default_by_day: dict[int, DefaultAvailability] = {}
    if week == "next" and not existing_slots:
        default_slots = await get_default_availability(session, USER_ID)
        default_by_day = {slot.day_of_week: slot for slot in default_slots}

    # Build week days with dates
    week_days = []
    for i in range(7):
        day_date = target_monday + timedelta(days=i)
        existing = slots_by_day.get(i)
        from_default = existing is None and i in default_by_day
        slot = existing if existing is not None else default_by_day.get(i)
        week_days.append(
            {
                "day_of_week": i,
                "day_name": DAY_NAMES[i],
                "date": day_date,
                "slot": slot,
                "from_default": from_default,
            }
        )

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
