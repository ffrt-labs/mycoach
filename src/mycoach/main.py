import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import mycoach.models  # noqa: F401 — register all models with Base.metadata
from mycoach.api.error_handlers import register_error_handlers
from mycoach.api.pages.availability import router as availability_page_router
from mycoach.api.pages.dashboard import router as dashboard_router
from mycoach.api.pages.history import router as history_router
from mycoach.api.pages.plan import router as plan_router
from mycoach.api.routes.activities import router as activities_router
from mycoach.api.routes.availability import router as availability_router
from mycoach.api.routes.coaching import router as coaching_router
from mycoach.api.routes.email_preferences import router as email_prefs_router
from mycoach.api.routes.health import router as health_router
from mycoach.api.routes.mesocycles import router as mesocycles_router
from mycoach.api.routes.plans import router as plans_router
from mycoach.api.routes.sources import router as sources_router
from mycoach.config import get_settings
from mycoach.database import Base, engine
from mycoach.scheduler.scheduler import create_scheduler

_BASE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)


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
    app = FastAPI(
        title="MyCoach",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    # Global error handlers
    register_error_handlers(app)

    # Static files + Jinja2 templates
    app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=_BASE_DIR / "templates")
    app.state.templates = templates

    # API routes (JSON)
    app.include_router(activities_router)
    app.include_router(availability_router)
    app.include_router(coaching_router)
    app.include_router(email_prefs_router)
    app.include_router(health_router)
    app.include_router(mesocycles_router)
    app.include_router(plans_router)
    app.include_router(sources_router)

    # Page routes (HTML)
    app.include_router(availability_page_router)
    app.include_router(dashboard_router)
    app.include_router(history_router)
    app.include_router(plan_router)

    @app.get("/api/system/status")
    async def system_status() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/system/scheduler")
    async def scheduler_status() -> dict:  # type: ignore[type-arg]
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is None:
            return {"running": False, "jobs": []}
        jobs = []
        for job in scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append(
                {
                    "id": job.id,
                    "next_run_time": next_run.isoformat() if next_run else None,
                    "trigger": str(job.trigger),
                }
            )
        return {
            "running": scheduler.running,
            "timezone": str(scheduler.timezone),
            "now": datetime.now(UTC).isoformat(),
            "jobs": jobs,
        }

    return app


app = create_app()
