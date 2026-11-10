"""Apply both Alembic histories, coaching first (ticket #184).

    python -m mycoach.tools.migrate

The coaching history (``alembic/``) and the health history (``alembic_health/``) are
independent, each with its own version table; on Postgres each runs as its schema's owner
role (``MYCOACH_COACHING_OWNER_DB_URL`` / ``MYCOACH_HEALTH_OWNER_DB_URL``). Coaching goes
first because the one-time cutover needs it: the health history refuses to drop
``prescribed_*`` until coaching's ``prescription_fulfillments`` holds them. Steady state
does not care about the order.

Run from the repository root, where ``alembic.ini`` lives (the container's WORKDIR).
"""

import sys

from alembic.config import Config

from alembic import command


def main(argv: list[str] | None = None) -> int:
    command.upgrade(Config("alembic.ini"), "head")
    command.upgrade(Config("alembic_health.ini"), "head")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
