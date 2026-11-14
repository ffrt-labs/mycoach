"""garmin weight push tracking on body measurements

Revision ID: h199a1b2c3d4
Revises: h198a1b2c3d4
Create Date: 2026-11-14 12:00:00.000000

Spec #191, ticket #199. MyCoach owns the weight and copies it one-way to Garmin Connect,
after the local save has committed. Each reading records how that copy went:

* ``garmin_push_status``: ``pending`` / ``pushed`` / ``failed``; NULL for a waist-only
  reading, which is never pushed.
* ``garmin_weigh_in_id``: Garmin's ``samplePk``, so deleting the reading deletes the copy.
* ``garmin_push_error``: the last failure, for the logs and the Body page.
* ``garmin_push_attempts``: the Garmin sync retries unpushed rows up to 5 attempts.

Weights logged before this revision are queued as ``pending`` (Garmin is owed them).
Additive only; ``health_analytics`` is not touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "h199a1b2c3d4"
down_revision: str | Sequence[str] | None = "h198a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    schema = db_roles.HEALTH_SCHEMA if _is_postgres() else None

    with op.batch_alter_table("body_measurements", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("garmin_push_status", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("garmin_weigh_in_id", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("garmin_push_error", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("garmin_push_attempts", sa.Integer(), nullable=False, server_default="0")
        )

    body = sa.table(
        "body_measurements",
        sa.column("weight_kg", sa.Float()),
        sa.column("garmin_push_status", sa.String()),
        schema=schema,
    )
    op.execute(
        body.update().where(body.c.weight_kg.is_not(None)).values(garmin_push_status="pending")
    )


def downgrade() -> None:
    schema = db_roles.HEALTH_SCHEMA if _is_postgres() else None
    with op.batch_alter_table("body_measurements", schema=schema) as batch_op:
        batch_op.drop_column("garmin_push_attempts")
        batch_op.drop_column("garmin_push_error")
        batch_op.drop_column("garmin_weigh_in_id")
        batch_op.drop_column("garmin_push_status")
