"""Offline companion logger PWA — standalone shell + scoped service worker.

Once `settings.logger_origin` is set, the logger itself is served from that
separate origin (see the `mycoach-logger` container / homelab-edge route) and
`/logger` here becomes a redirect for anyone hitting the old address. Until
then — local dev, or a deployment that hasn't split it out — this still
serves the shell directly, same-origin, exactly as before.
"""

from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mycoach.config import get_settings

router = APIRouter(tags=["logger"])

_SW_PATH = Path(__file__).resolve().parents[1].parent / "static" / "logger" / "sw.js"


@router.get("/logger")
async def logger_app(request: Request) -> Response:
    """Serve the standalone offline logger shell, or redirect to its new origin."""
    logger_origin = get_settings().logger_origin
    if logger_origin:
        return RedirectResponse(logger_origin)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "logger/index.html", {})


@router.get("/logger/sw.js", include_in_schema=False)
async def logger_service_worker() -> FileResponse:
    """Serve the logger service worker with a widened scope.

    The script lives at /logger/sw.js (directory scope /logger/), but it must
    control the shell at /logger, so it declares Service-Worker-Allowed: /logger.
    No-cache so shell/SW updates are picked up promptly.

    Kept even once split: an already-installed old PWA still fetches this URL,
    and a working service worker for it is better than a 404 underneath a
    redirecting shell.
    """
    return FileResponse(
        _SW_PATH,
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/logger",
            "Cache-Control": "no-cache",
        },
    )
