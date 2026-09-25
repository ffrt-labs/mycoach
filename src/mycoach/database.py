from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from mycoach.config import get_settings

settings = get_settings()

_is_sqlite = settings.db_url.startswith("sqlite")

# No pooling on Postgres: scheduler jobs each run in their own throwaway event
# loop (scheduler/jobs.py `_run_async`), and an asyncpg connection is bound to
# the loop that opened it, so a shared pool hands jobs connections from the
# wrong loop. One user on an internal network makes per-use connects cheap.
engine = create_async_engine(
    settings.db_url,
    echo=settings.debug,
    **({} if _is_sqlite else {"poolclass": NullPool}),
)

# Postgres is the production engine (map #85, ticket #107). The SQLite branch is
# gated, not removed: the test suite and one-off local runs still use it.
if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
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
