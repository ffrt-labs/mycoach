"""settled activities and a unique external id per athlete

Revision ID: h186a1b2c3d4
Revises: h185a1b2c3d4
Create Date: 2026-11-10 18:00:00.000000

Map #85, ticket #186 (design #111, #112). The health half of the module boundary:

* ``settled_at`` / ``settlement_reason`` on ``canonical_activities`` (a Settled activity,
  CONTEXT.md). Every activity that exists at cutover is backfilled as settled with reason
  ``reconciled``: it was already reconciled or aged out under the old model, and must not
  trigger a burst of final-coaching events on the first sync after deploy.
* ``external_id`` becomes the one name coaching uses for an activity (planned sessions and
  insights are re-keyed to it by the coaching revision ``c186a1b2c3d4``). Every row gets one:
  a missing value is filled from the activity's linked source records (a gym source's id
  wins over a Garmin one, because the gym source is the base of a reconciled activity), then
  a unique index per athlete is added.

Refuses to run (changing nothing) when two activities of one athlete already share an
external id: they would be indistinguishable to coaching, and picking a winner would be a
data decision for a human (docs/migrations/186-cutover.md).

ORDER MATTERS on the one-time cutover: run this history BEFORE the coaching revision
``c186a1b2c3d4``, which reads ``canonical_activities`` to re-key ``activity_id`` to
``activity_external_id``. The ``health_analytics`` views are not touched: they list their
columns explicitly, so the new columns do not appear in them and no published column
changes (the additive-only rule of #113 holds).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "h186a1b2c3d4"
down_revision: str | Sequence[str] | None = "h185a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_canonical_activities_athlete_external_id"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _t(name: str, pg: bool) -> str:
    return f"health.{name}" if pg else name


def _missing_external_ids(pg: bool) -> dict[int, str]:
    """What each NULL external id will become: the linked source records' id, a gym source's
    before a Garmin one (the gym source is the base of a reconciled activity)."""
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT a.id AS id, ("
                "SELECT s.external_id FROM "
                f"{_t('canonical_activity_sources', pg)} cs "
                f"JOIN {_t('source_records', pg)} s ON s.id = cs.source_record_id "
                "WHERE cs.canonical_activity_id = a.id "
                "ORDER BY CASE WHEN s.source = 'garmin' THEN 1 ELSE 0 END, s.id LIMIT 1"
                f") AS xid FROM {_t('canonical_activities', pg)} a WHERE a.external_id IS NULL"
            )
        )
        .all()
    )
    return {r.id: r.xid or f"legacy-activity-{r.id}" for r in rows}


def _guard_duplicates(pg: bool, filled: dict[int, str]) -> None:
    """Refuse before any change when two activities of one athlete would share an id."""
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT id, athlete_id, external_id FROM {_t('canonical_activities', pg)}"
            )
        )
        .all()
    )
    seen: dict[tuple[int, str], int] = {}
    for r in rows:
        key = (r.athlete_id, r.external_id if r.external_id is not None else filled[r.id])
        seen[key] = seen.get(key, 0) + 1
    duplicated = {k: n for k, n in seen.items() if n > 1}
    if duplicated:
        listed = list(duplicated.items())[:10]
        shown = ", ".join(f"(athlete {a}, {x!r} x{n})" for (a, x), n in listed)
        raise RuntimeError(
            f"{len(duplicated)} external id(s) are shared by several activities of one athlete: "
            f"{shown}. Coaching could not tell them apart; resolve them by hand and re-run "
            "(docs/migrations/186-cutover.md)."
        )


def upgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.HEALTH_SCHEMA if pg else None
    ca = _t("canonical_activities", pg)

    # Decide everything that can refuse before the first change: SQLite cannot roll DDL back.
    filled = _missing_external_ids(pg)
    _guard_duplicates(pg, filled)

    with op.batch_alter_table("canonical_activities", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("settled_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("settlement_reason", sa.String(20), nullable=True))

    # Whatever exists at cutover is settled: it was reconciled, or old enough that the
    # deadline would pass at once. Nothing re-fires final coaching for it.
    op.execute(
        f"UPDATE {ca} SET settled_at = CURRENT_TIMESTAMP, settlement_reason = 'reconciled'"
    )

    for activity_id, external_id in filled.items():
        op.get_bind().execute(
            sa.text(f"UPDATE {ca} SET external_id = :x WHERE id = :i"),
            {"x": external_id, "i": activity_id},
        )

    with op.batch_alter_table("canonical_activities", schema=schema) as batch_op:
        batch_op.alter_column("external_id", existing_type=sa.String(100), nullable=False)
    op.create_index(
        INDEX, "canonical_activities", ["athlete_id", "external_id"], unique=True, schema=schema
    )


def downgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.HEALTH_SCHEMA if pg else None

    op.drop_index(INDEX, table_name="canonical_activities", schema=schema)
    with op.batch_alter_table("canonical_activities", schema=schema) as batch_op:
        batch_op.alter_column("external_id", existing_type=sa.String(100), nullable=True)
        batch_op.drop_column("settlement_reason")
        batch_op.drop_column("settled_at")
