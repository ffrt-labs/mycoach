import logging
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mycoach.coaching.prompt_store import set_prompt_store
from mycoach.database import SQLITE_SCHEMA_TRANSLATE_MAP, Base, get_db
from mycoach.health import runtime as health_runtime
from mycoach.main import app
from mycoach.observability.tracing import NoopTracer, set_tracer

# A file, not ``:memory:``: an in-memory engine shares one connection among every session, so
# the health module's own session (a second connection in production) would roll back
# whatever the test's coaching session has flushed and not yet committed. Real separate
# connections keep the tests honest about the seam (ticket #186).
_DB_FILE = Path(tempfile.mkdtemp(prefix="mycoach-tests-")) / "test.db"
test_engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_FILE}", echo=False).execution_options(
    schema_translate_map=SQLITE_SCHEMA_TRANSLATE_MAP
)
test_session = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with test_session() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db
health_runtime.configure(test_session)


@pytest.fixture(autouse=True)
def _app_sessions_on_the_test_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Event handlers and jobs open their own sessions; point them at the test engine."""
    monkeypatch.setattr("mycoach.database.async_session", test_session)


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Generator[None, None, None]:
    """Undo permanent logging mutations so they can't leak into later tests.

    tests/test_migrations/*.py runs real alembic upgrades in-process, and
    alembic/env.py calls ``logging.config.fileConfig()`` on every invocation.
    That call defaults to ``disable_existing_loggers=True``, which sets
    ``.disabled = True`` on *every* logger already registered in
    ``logging.Logger.manager.loggerDict`` that isn't named in alembic.ini's
    ``[loggers]`` section (only root/sqlalchemy/alembic are). Because pytest
    keeps one process for the whole run, this permanently silences
    already-imported application loggers such as ``mycoach.scheduler.jobs``
    and ``mycoach.sources.garmin`` — any later test's ``logger.error(...)``
    call becomes a no-op before a record is ever created, so ``caplog``
    (which relies on propagation to root) sees nothing.

    Snapshotting the whole loggerDict (name -> disabled/level/propagate/
    handlers) is cheap — a shallow dict copy — and is the only reliably
    narrow fix: the set of loggers fileConfig disables is unpredictable
    (it depends on which modules happened to be imported first), so
    restoring only a hand-picked list of "loggers production code touches"
    would not cover it.
    """
    manager = logging.Logger.manager
    root = logging.root
    snapshot = {
        name: (lg.disabled, lg.level, lg.propagate, list(lg.handlers))
        for name, lg in manager.loggerDict.items()
        if isinstance(lg, logging.Logger)
    }
    root_state = (root.disabled, root.level, list(root.handlers))
    try:
        yield
    finally:
        for name, (disabled, level, propagate, handlers) in snapshot.items():
            lg = manager.loggerDict.get(name)
            if isinstance(lg, logging.Logger):
                lg.disabled = disabled
                lg.level = level
                lg.propagate = propagate
                lg.handlers = handlers
        root.disabled, root.level, root.handlers = root_state


@pytest.fixture(autouse=True)
def _langfuse_off() -> Generator[None, None, None]:
    """No test traces to a real Langfuse, whatever LANGFUSE_* the shell or .env holds.

    Tracing tests pass their own tracer (tests/test_observability/conftest.py)
    and prompt tests their own store; by default prompts are the bundled files.
    """
    set_tracer(NoopTracer())
    set_prompt_store(None)
    yield
    set_tracer(None)
    set_prompt_store(None)


@pytest.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
