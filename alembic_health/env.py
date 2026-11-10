"""Alembic environment for the *health* schema history (map #85, ticket #184).

Two histories, one per schema (docs/adr/0001): ``alembic/`` owns the coaching side and
this directory the health side, each with its own version table, so a coaching migration
cannot touch a health table and the reverse. On Postgres this history runs as
``health_owner`` (``MYCOACH_HEALTH_OWNER_DB_URL``); on SQLite, which has no schemas, it
shares the one database file under the version table ``alembic_version_health``.
"""

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import mycoach.models  # noqa: F401 — register all models with Base.metadata
from alembic import context
from mycoach.config import get_settings
from mycoach.database import HEALTH_SCHEMA, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
db_url = settings.health_owner_db_url or settings.db_url
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata
_is_sqlite = db_url.startswith("sqlite")
VERSION_TABLE = "alembic_version"
VERSION_TABLE_SCHEMA = None if _is_sqlite else HEALTH_SCHEMA
if _is_sqlite:
    # One flat file: keep the two histories' version tables apart by name.
    VERSION_TABLE = "alembic_version_health"


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Autogenerate sees only the health schema's tables."""
    if type_ == "table" and not reflected:
        return getattr(obj, "schema", None) == HEALTH_SCHEMA
    return True


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_as_batch=True,
        include_schemas=not _is_sqlite,
        include_object=include_object,
        version_table=VERSION_TABLE,
        version_table_schema=VERSION_TABLE_SCHEMA,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
