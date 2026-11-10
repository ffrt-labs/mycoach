"""Apply both Alembic histories, interleaved where one reads the other (tickets #184, #186).

    python -m mycoach.tools.migrate

The coaching history (``alembic/``) and the health history (``alembic_health/``) are
independent, each with its own version table; on Postgres each runs as its schema's owner
role (``MYCOACH_COACHING_OWNER_DB_URL`` / ``MYCOACH_HEALTH_OWNER_DB_URL``). The two cutovers
read across the histories, so the order is fixed: coaching up to ``c184`` (its
``prescription_fulfillments`` must hold the prescriptions before health drops ``prescribed_*``),
then the whole health history (``h186`` guarantees every activity an external id), then the rest
of coaching (``c186`` re-keys ``activity_id`` to that external id). Steady state does not care.

Run from the repository root, where ``alembic.ini`` lives (the container's WORKDIR).
"""

import sys

from alembic.config import Config

from alembic import command

# The last coaching revision the health history must follow: its fulfillments copy (#184).
COACHING_BEFORE_HEALTH = "c184a1b2c3d4"


def main(argv: list[str] | None = None) -> int:
    command.upgrade(Config("alembic.ini"), COACHING_BEFORE_HEALTH)
    command.upgrade(Config("alembic_health.ini"), "head")
    command.upgrade(Config("alembic.ini"), "head")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
