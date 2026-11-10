"""Create the health/coaching roles and schemas, or re-assert their grants (ticket #184).

Run once, by a human, as the instance's superuser, BEFORE the two Alembic histories on the
cutover (docs/migrations/184-cutover.md):

    python -m mycoach.tools.db_bootstrap --url postgresql+asyncpg://mycoach:<pw>@mycoach-db/mycoach

``--finalize`` additionally hands every object already in the two schemas to its owner and
re-grants the DML-only runtime roles (what each migration does at its end). Use it after
building a fresh database with ``Base.metadata.create_all``, which has no migration to do it.

``--print`` writes the SQL instead of executing it, for review before running by hand.
Idempotent. Touches no password: the roles are NOLOGIN groups.
"""

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mycoach import db_roles


def statements(*, finalize: bool) -> list[str]:
    out = db_roles.bootstrap_statements()
    if finalize:
        out += db_roles.finalize_schema_statements(db_roles.HEALTH)
        out += db_roles.finalize_schema_statements(db_roles.COACHING)
    return out


async def run(url: str, *, finalize: bool) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            for statement in statements(finalize=finalize):
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="superuser async URL of the target database")
    parser.add_argument("--finalize", action="store_true", help="also fix ownership and grants")
    parser.add_argument("--print", dest="print_only", action="store_true", help="print the SQL")
    args = parser.parse_args(argv)

    if args.print_only:
        for statement in statements(finalize=args.finalize):
            print(statement + ";")
        return 0
    if not args.url:
        parser.error("--url is required unless --print is given")
    asyncio.run(run(args.url, finalize=args.finalize))
    return 0


if __name__ == "__main__":
    sys.exit(main())
