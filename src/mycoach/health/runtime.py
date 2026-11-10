"""Where the health module gets its database sessions.

The module's public functions take no session: they open their own, commit their own
transaction and return plain data, exactly the shape an HTTP client would have after the
physical split (map #85). Tests point this at their in-memory engine.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

SessionFactory = Callable[[], AsyncSession]

_factory: SessionFactory | None = None


def configure(factory: SessionFactory | None) -> None:
    """Use ``factory`` for every health session (``None`` restores the app's own)."""
    global _factory
    _factory = factory


def _default_factory() -> AsyncSession:
    from mycoach.database import health_async_session

    return health_async_session()


@asynccontextmanager
async def health_session() -> AsyncIterator[AsyncSession]:
    """A health session; the caller commits for writes."""
    async with (_factory or _default_factory)() as session:
        yield session
