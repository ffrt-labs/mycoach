"""Body page: log weight and waist, enter height, see the last 10 readings (ticket #198)."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.api.defaults import DEFAULT_USER_ID
from mycoach.coaching.athlete import athlete_id_for
from mycoach.config import get_settings
from mycoach.database import get_db
from mycoach.localtime import local_now

router = APIRouter(tags=["pages"])

PROTOCOL_HINT = (
    "Morning, after the bathroom, before food. Waist at the navel, end of a normal exhale, "
    "average 2 readings."
)


@router.get("/body", response_class=HTMLResponse)
async def body_page(request: Request, session: AsyncSession = Depends(get_db)) -> HTMLResponse:
    athlete_id = await athlete_id_for(session, DEFAULT_USER_ID)
    measurements = [
        {**m, "measured_at": datetime.fromisoformat(m["measured_at"])}
        for m in await health.list_body_measurements(athlete_id, limit=10)
    ]
    now_local = local_now(get_settings().timezone)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "body.html",
        {
            "active_page": "body",
            "measurements": measurements,
            "height_cm": await health.get_athlete_height(athlete_id),
            "now_local": now_local.strftime("%Y-%m-%dT%H:%M"),
            "protocol_hint": PROTOCOL_HINT,
        },
    )
