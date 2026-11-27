"""body measurements and the athlete's height

Revision ID: h198a1b2c3d4
Revises: h197a1b2c3d4
Create Date: 2026-11-14 10:00:00.000000

Spec #191, ticket #198 (docs/research/health-metrics.md §4). MyCoach owns weight and waist:

* ``body_measurements``: one row per reading the athlete enters on the Body page, with a
  weight in kg and/or a waist in cm (a CHECK refuses a row with neither), an optional note,
  ``source`` (``'manual'``) and ``measured_at`` in UTC. No foreign key to ``athletes``,
  like every other health table. The Garmin weight push (#199) adds its tracking columns
  in its own revision.
* ``athletes.height_cm``: entered once, the divisor of the waist-to-height ratio.

Purely additive: no backfill, and the ``health_analytics`` views are not touched (they list
their columns explicitly, so the additive-only rule of #113 holds).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "h198a1b2c3d4"
down_revision: str | Sequence[str] | None = "h197a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_body_measurements_athlete_measured_at"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.HEALTH_SCHEMA if pg else None

    op.create_table(
        "body_measurements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("waist_cm", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "weight_kg IS NOT NULL OR waist_cm IS NOT NULL",
            name="ck_body_measurements_weight_or_waist",
        ),
        schema=schema,
    )
    op.create_index(INDEX, "body_measurements", ["athlete_id", "measured_at"], schema=schema)
    with op.batch_alter_table("athletes", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("height_cm", sa.Float(), nullable=True))

    if pg:
        for statement in db_roles.finalize_schema_statements(db_roles.HEALTH):
            op.execute(statement)


def downgrade() -> None:
    schema = db_roles.HEALTH_SCHEMA if _is_postgres() else None
    with op.batch_alter_table("athletes", schema=schema) as batch_op:
        batch_op.drop_column("height_cm")
    op.drop_index(INDEX, table_name="body_measurements", schema=schema)
    op.drop_table("body_measurements", schema=schema)
