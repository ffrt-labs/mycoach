"""key planned sessions and insights by activity external id; insight revisions

Revision ID: c186a1b2c3d4
Revises: c184a1b2c3d4
Create Date: 2026-11-10 18:05:00.000000

Map #85, ticket #186 (design #111, #112). The coaching half of the module boundary:

* ``planned_sessions.activity_id`` and ``coaching_insights.activity_id`` held a health
  row id by value. Health's internal id must not cross the seam, so both become
  ``activity_external_id`` (the activity's immutable external id) and ``activity_id`` is
  dropped. Every existing value is translated through ``canonical_activities`` once.
* ``coaching_insights`` gains ``revision`` (default 1: existing rows are revision 1) and
  ``source_event_id`` (the deterministic health event id that produced a revision, the
  idempotency key of the post-workout revision handler).

ORDER MATTERS on the one-time cutover: run the health revision ``h186a1b2c3d4`` first (it
guarantees every canonical activity has an external id), then this one. The translation
reads the health tables once, so it needs a role that can (the instance's superuser, as in
the #184 cutover); a later downgrade needs the same.

Refuses to run (changing nothing) when a non-null ``activity_id`` no longer resolves to a
canonical activity (a Garmin-only row deleted by an old reconciliation, for instance): the
pointer would be silently lost. The refusal lists them; see docs/migrations/186-cutover.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "c186a1b2c3d4"
down_revision: str | Sequence[str] | None = "c184a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("planned_sessions", "coaching_insights")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _coaching(name: str, pg: bool) -> str:
    return f"{db_roles.COACHING_SCHEMA}.{name}" if pg else name


def _health_activities(pg: bool) -> str:
    return f"{db_roles.HEALTH_SCHEMA}.canonical_activities" if pg else "canonical_activities"


def _health_activities_exist(pg: bool) -> bool:
    inspector = sa.inspect(op.get_bind())
    schema = db_roles.HEALTH_SCHEMA if pg else None
    return inspector.has_table("canonical_activities", schema=schema)


def _guard_unresolved(pg: bool) -> None:
    """Refuse, changing nothing, when a link cannot be re-keyed.

    A link resolves when its canonical activity exists and has an external id (the health
    revision ``h186a1b2c3d4`` guarantees that). Without the health table nothing resolves,
    which is fine only while there is no link to lose.
    """
    bind = op.get_bind()
    resolvable = _health_activities_exist(pg)
    problems: list[str] = []
    for table in TABLES:
        unresolved = (
            "NOT EXISTS (SELECT 1 FROM "
            f"{_health_activities(pg)} a WHERE a.id = t.activity_id AND a.external_id IS NOT NULL)"
            if resolvable
            else "1 = 1"
        )
        rows = bind.execute(
            sa.text(
                f"SELECT t.id, t.activity_id FROM {_coaching(table, pg)} t "
                f"WHERE t.activity_id IS NOT NULL AND {unresolved}"
            )
        ).all()
        problems += [f"{table}.id={r[0]} -> activity_id={r[1]}" for r in rows]
    if problems:
        raise RuntimeError(
            f"{len(problems)} activity_id value(s) do not resolve to a canonical activity with an "
            f"external id: {'; '.join(problems[:10])}. Dropping the column would lose the link. "
            "Run the health revision h186a1b2c3d4 first, or clear/re-point them, and re-run "
            "(docs/migrations/186-cutover.md)."
        )


def upgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.COACHING_SCHEMA if pg else None
    _guard_unresolved(pg)

    with op.batch_alter_table("planned_sessions", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("activity_external_id", sa.String(100), nullable=True))
    with op.batch_alter_table("coaching_insights", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("activity_external_id", sa.String(100), nullable=True))
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("source_event_id", sa.String(64), nullable=True))

    if _health_activities_exist(pg):
        for table in TABLES:
            op.execute(
                f"UPDATE {_coaching(table, pg)} SET activity_external_id = ("
                f"SELECT a.external_id FROM {_health_activities(pg)} a "
                f"WHERE a.id = {_coaching(table, pg)}.activity_id) "
                "WHERE activity_id IS NOT NULL"
            )

    for table in TABLES:
        with op.batch_alter_table(table, schema=schema) as batch_op:
            batch_op.drop_column("activity_id")


def downgrade() -> None:
    """Back to the #184 layout: ``activity_id`` is re-derived from the external id.

    An insight revision beyond the first has no place in the old shape and is dropped; a
    link whose activity is gone comes back as NULL."""
    pg = _is_postgres()
    schema = db_roles.COACHING_SCHEMA if pg else None

    for table in TABLES:
        with op.batch_alter_table(table, schema=schema) as batch_op:
            batch_op.add_column(sa.Column("activity_id", sa.Integer(), nullable=True))
        if _health_activities_exist(pg):
            op.execute(
                f"UPDATE {_coaching(table, pg)} SET activity_id = ("
                f"SELECT a.id FROM {_health_activities(pg)} a "
                f"WHERE a.external_id = {_coaching(table, pg)}.activity_external_id) "
                "WHERE activity_external_id IS NOT NULL"
            )

    op.execute(f"DELETE FROM {_coaching('coaching_insights', pg)} WHERE revision > 1")
    with op.batch_alter_table("coaching_insights", schema=schema) as batch_op:
        batch_op.drop_column("source_event_id")
        batch_op.drop_column("revision")
        batch_op.drop_column("activity_external_id")
    with op.batch_alter_table("planned_sessions", schema=schema) as batch_op:
        batch_op.drop_column("activity_external_id")
