"""Sport profiles page — manage per-sport skill levels, goals, and preferences."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.sport_profile import SportProfile

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

SPORT_CHOICES = ["gym", "swimming", "padel", "cardio"]


@router.get("/sport-profiles", response_class=HTMLResponse)
async def sport_profiles_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the sport profiles management page."""
    result = await session.execute(
        select(SportProfile)
        .where(SportProfile.user_id == USER_ID)
        .order_by(SportProfile.sport)
    )
    profiles = list(result.scalars().all())

    existing_sports = {p.sport for p in profiles}
    available_sports = [s for s in SPORT_CHOICES if s not in existing_sports]

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "sport_profiles.html",
        {
            "active_page": "sport_profiles",
            "profiles": profiles,
            "available_sports": available_sports,
        },
    )
