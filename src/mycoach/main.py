from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

import mycoach.models  # noqa: F401 — register all models with Base.metadata
from mycoach.api.routes.activities import router as activities_router
from mycoach.api.routes.health import router as health_router
from mycoach.api.routes.sources import router as sources_router
from mycoach.config import get_settings
from mycoach.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Create tables on startup (dev convenience; Alembic for production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="MyCoach",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.include_router(activities_router)
    app.include_router(health_router)
    app.include_router(sources_router)

    @app.get("/api/system/status")
    async def system_status() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
