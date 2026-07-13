"""Offline companion logger PWA — standalone shell + scoped service worker.

Served on the LAN. Note: the service worker (hence offline caching) only
registers in a secure context — front MyCoach with HTTPS/Caddy (see
Caddyfile.example) for this to work on a phone, especially iOS.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["logger"])

_SW_PATH = Path(__file__).resolve().parents[1].parent / "static" / "logger" / "sw.js"


@router.get("/logger", response_class=HTMLResponse)
async def logger_app(request: Request) -> HTMLResponse:
    """Serve the standalone offline logger shell."""
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "logger/index.html", {})


@router.get("/logger/sw.js", include_in_schema=False)
async def logger_service_worker() -> FileResponse:
    """Serve the logger service worker with a widened scope.

    The script lives at /logger/sw.js (directory scope /logger/), but it must
    control the shell at /logger, so it declares Service-Worker-Allowed: /logger.
    No-cache so shell/SW updates are picked up promptly.
    """
    return FileResponse(
        _SW_PATH,
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/logger",
            "Cache-Control": "no-cache",
        },
    )
