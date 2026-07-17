import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

import mycoach.models  # noqa: F401 — register all models with Base.metadata
from mycoach.api.error_handlers import register_error_handlers
from mycoach.api.pages.availability import router as availability_page_router
from mycoach.api.pages.dashboard import router as dashboard_router
from mycoach.api.pages.history import router as history_router
from mycoach.api.pages.logger import router as logger_page_router
from mycoach.api.pages.mesocycles import router as mesocycles_page_router
from mycoach.api.pages.plan import router as plan_router
from mycoach.api.pages.prompt_logs import router as prompt_logs_page_router
from mycoach.api.pages.routine import router as routine_page_router
from mycoach.api.pages.sport_profiles import router as sport_profiles_page_router
from mycoach.api.routes.activities import router as activities_router
from mycoach.api.routes.availability import router as availability_router
from mycoach.api.routes.coaching import router as coaching_router
from mycoach.api.routes.credentials import router as credentials_router
from mycoach.api.routes.email_preferences import router as email_prefs_router
from mycoach.api.routes.health import router as health_router
from mycoach.api.routes.logger import router as logger_router
from mycoach.api.routes.mesocycles import router as mesocycles_router
from mycoach.api.routes.plans import router as plans_router
from mycoach.api.routes.profile import router as profile_router
from mycoach.api.routes.routines import router as routines_router
from mycoach.api.routes.sources import router as sources_router
from mycoach.api.routes.sport_profiles import router as sport_profiles_router
from mycoach.api.routes.system import router as system_router
from mycoach.config import get_settings
from mycoach.database import Base, engine
from mycoach.logging_config import setup_logging
from mycoach.scheduler.scheduler import create_scheduler

_BASE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)
_request_logger = logging.getLogger("mycoach.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every HTTP request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        _request_logger.info(
            "%s %s %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Create tables on startup (dev convenience; Alembic for production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Start the background scheduler if not in test mode
    settings = get_settings()
    scheduler = None
    if settings.env != "test":
        scheduler = create_scheduler(settings)
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Background scheduler started")

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped")
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(
        title="MyCoach",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    # Request logging middleware
    app.add_middleware(RequestLoggingMiddleware)

    # Global error handlers
    register_error_handlers(app)

    # Static files + Jinja2 templates
    app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=_BASE_DIR / "templates")
    local_tz = ZoneInfo(settings.timezone)

    def _localtime(value: datetime) -> datetime:
        """Convert a naive-UTC or aware-UTC datetime to the configured local timezone."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(local_tz)

    _BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    _BULLET_RE = re.compile(r"(?:^|\n)\s*- ")

    def _md_bold(value: str) -> Markup:
        """Convert **bold** markdown and `- ` bullets to HTML (XSS-safe)."""
        escaped = str(Markup.escape(value))
        html = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
        # If text contains bullet points, split into intro paragraph + <ul> list
        if _BULLET_RE.search(html):
            parts = re.split(r"(?:^|\n)\s*- ", html)
            intro = parts[0].strip()
            items = [p.strip() for p in parts[1:] if p.strip()]
            if items:
                li = "".join(f"<li>{item}</li>" for item in items)
                intro_html = f"<p>{intro}</p>" if intro else ""
                html = f'{intro_html}<ul class="list-disc ml-4 mt-1 space-y-1">{li}</ul>'
        return Markup(html)

    templates.env.filters["localtime"] = _localtime
    templates.env.filters["md_bold"] = _md_bold
    app.state.templates = templates

    # API routes (JSON)
    app.include_router(activities_router)
    app.include_router(availability_router)
    app.include_router(coaching_router)
    app.include_router(credentials_router)
    app.include_router(email_prefs_router)
    app.include_router(health_router)
    app.include_router(logger_router)
    app.include_router(mesocycles_router)
    app.include_router(plans_router)
    app.include_router(profile_router)
    app.include_router(routines_router)
    app.include_router(sources_router)
    app.include_router(sport_profiles_router)
    app.include_router(system_router)

    # Page routes (HTML)
    app.include_router(availability_page_router)
    app.include_router(dashboard_router)
    app.include_router(history_router)
    app.include_router(logger_page_router)
    app.include_router(plan_router)
    app.include_router(routine_page_router)
    app.include_router(prompt_logs_page_router)
    app.include_router(mesocycles_page_router)
    app.include_router(sport_profiles_page_router)

    return app


app = create_app()
