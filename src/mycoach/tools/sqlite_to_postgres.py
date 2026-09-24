"""One-off faithful copy of the SQLite database into Postgres (map #85, ticket #107).

Same tables, same shapes, no model changes. The Postgres schema is built from the
ORM models (``Base.metadata.create_all``) and stamped at the Alembic head, rather
than by replaying the SQLite-flavoured migration history.

Run inside the app container, with the app stopped and every logger session
drained and synced first:

    python -m mycoach.tools.sqlite_to_postgres \\
        --source sqlite+aiosqlite:////data/mycoach.db \\
        --target postgresql+asyncpg://mycoach:<password>@mycoach-db/mycoach

Refuses to write into a non-empty target. Exits non-zero if any table's row
count differs after the copy.
"""

import argparse
import asyncio
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Table, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

import mycoach.models  # noqa: F401 — register all models with Base.metadata
from mycoach.database import Base

BATCH_SIZE = 500


async def _count(conn: AsyncConnection, table: Table) -> int:
    return (await conn.execute(select(func.count()).select_from(table))).scalar_one()


async def _alembic_version(conn: AsyncConnection) -> str | None:
    rows = (await conn.execute(text("SELECT version_num FROM alembic_version"))).all()
    return rows[0][0] if rows else None


async def _reset_sequence(conn: AsyncConnection, table: Table) -> None:
    """Point a serial PK's sequence past the copied ids so new inserts don't collide."""
    pk = list(table.primary_key.columns)
    if len(pk) != 1 or not pk[0].autoincrement or pk[0].type.python_type is not int:
        return
    col = pk[0].name
    await conn.execute(
        text(
            f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col}'), "
            f'COALESCE((SELECT MAX("{col}") FROM "{table.name}"), 1), '
            f'(SELECT MAX("{col}") IS NOT NULL FROM "{table.name}"))'
        )
    )


async def copy(source_url: str, target_url: str) -> bool:
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    src = create_async_engine(source_url)
    dst = create_async_engine(target_url)
    tables = list(Base.metadata.sorted_tables)  # FK-dependency order

    try:
        async with src.connect() as sconn:
            version = await _alembic_version(sconn)
            if version != head:
                print(f"ABORT: source is at revision {version}, head is {head}. Migrate it first.")
                return False

            async with dst.begin() as dconn:
                await dconn.run_sync(Base.metadata.create_all)
                for table in tables:
                    if await _count(dconn, table):
                        print(f"ABORT: target table {table.name} is not empty.")
                        return False
                if await dconn.scalar(text("SELECT to_regclass('alembic_version')")):
                    await dconn.execute(text("DROP TABLE alembic_version"))

                for table in tables:
                    result = await sconn.stream(select(table))
                    async for chunk in result.partitions(BATCH_SIZE):
                        await dconn.execute(insert(table), [dict(r._mapping) for r in chunk])
                    await _reset_sequence(dconn, table)

                await dconn.execute(
                    text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
                )
                await dconn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": version})

        ok = True
        async with src.connect() as sconn, dst.connect() as dconn:
            print(f"{'table':<32}{'sqlite':>10}{'postgres':>10}")
            for table in tables:
                s, d = await _count(sconn, table), await _count(dconn, table)
                flag = "" if s == d else "  <-- MISMATCH"
                ok &= s == d
                print(f"{table.name:<32}{s:>10}{d:>10}{flag}")
            pg_version = await _alembic_version(dconn)
            print(f"alembic_version: {pg_version} (head {head})")
            ok &= pg_version == head
        return ok
    finally:
        await src.dispose()
        await dst.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", required=True, help="SQLite URL (sqlite+aiosqlite:///...)")
    parser.add_argument("--target", required=True, help="Postgres URL (postgresql+asyncpg://...)")
    args = parser.parse_args()
    if not args.source.startswith("sqlite"):
        parser.error("--source must be a sqlite URL")
    if not args.target.startswith("postgresql"):
        parser.error("--target must be a postgresql URL")
    sys.exit(0 if asyncio.run(copy(args.source, args.target)) else 1)


if __name__ == "__main__":
    main()
