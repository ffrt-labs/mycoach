"""Mesocycles page — manage per-sport training block configurations."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.coaching import MesocycleConfig

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

SPORT_CHOICES = ["gym", "swimming", "padel", "cardio"]


@router.get("/mesocycles", response_class=HTMLResponse)
async def mesocycles_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the mesocycles management page."""
    result = await session.execute(
        select(MesocycleConfig)
        .where(MesocycleConfig.user_id == USER_ID)
        .order_by(MesocycleConfig.sport)
    )
    configs = list(result.scalars().all())

    existing_sports = {c.sport for c in configs}
    available_sports = [s for s in SPORT_CHOICES if s not in existing_sports]

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "mesocycles.html",
        {
            "active_page": "mesocycles",
            "configs": configs,
            "available_sports": available_sports,
        },
    )
