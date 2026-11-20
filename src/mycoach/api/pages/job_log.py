"""Job log page: a scheduled job's recent runs, read-only.

The weekly recap's Setup notice links here when next week's plan failed to generate
(#242). ``/api/system/jobs/{job_name}/runs`` serves the same runs as JSON.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.api.routes.system import recent_job_runs
from mycoach.database import get_db

router = APIRouter(tags=["pages"])


@router.get("/jobs/{job_name}", response_class=HTMLResponse)
async def job_log_page(
    request: Request, job_name: str, session: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Render the job's recent runs, newest first."""
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "job_log.html",
        {"job_name": job_name, "runs": await recent_job_runs(session, job_name)},
    )
