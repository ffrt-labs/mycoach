"""Gym routine management page."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mycoach.database import get_db
from mycoach.models.routine import RoutineDay, WorkoutRoutine

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@router.get("/routine", response_class=HTMLResponse)
async def routine_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the gym routine management page."""
    stmt = (
        select(WorkoutRoutine)
        .where(WorkoutRoutine.user_id == USER_ID, WorkoutRoutine.is_active.is_(True))
        .options(selectinload(WorkoutRoutine.days).selectinload(RoutineDay.exercises))
    )
    result = await session.execute(stmt)
    routine = result.scalar_one_or_none()

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "routine.html",
        {
            "active_page": "routine",
            "routine": routine,
            "day_names": DAY_NAMES,
        },
    )
