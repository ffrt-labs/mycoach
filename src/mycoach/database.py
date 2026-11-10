from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from mycoach.config import get_settings

settings = get_settings()

# The two schemas of the single Postgres instance (map #85, ticket #108; ADR 0001).
# Every model names the one it lives in. SQLite has no schemas, so engines on it
# translate both to "no schema" (``SQLITE_SCHEMA_TRANSLATE_MAP``) and the whole
# suite keeps running against one flat file/in-memory database.
HEALTH_SCHEMA = "health"
COACHING_SCHEMA = "coaching"
SQLITE_SCHEMA_TRANSLATE_MAP: dict[str, str | None] = {
    HEALTH_SCHEMA: None,
    COACHING_SCHEMA: None,
}

_is_sqlite = settings.db_url.startswith("sqlite")

# No pooling on Postgres: scheduler jobs each run in their own throwaway event
# loop (scheduler/jobs.py `_run_async`), and an asyncpg connection is bound to
# the loop that opened it, so a shared pool hands jobs connections from the
# wrong loop. One user on an internal network makes per-use connects cheap.
_raw_engine = create_async_engine(
    settings.db_url,
    echo=settings.debug,
    **({} if _is_sqlite else {"poolclass": NullPool}),
)
engine = (
    _raw_engine.execution_options(schema_translate_map=SQLITE_SCHEMA_TRANSLATE_MAP)
    if _is_sqlite
    else _raw_engine
)

# Postgres is the production engine (map #85, ticket #107). The SQLite branch is
# gated, not removed: the test suite and one-off local runs still use it.
if _is_sqlite:

    @event.listens_for(_raw_engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
