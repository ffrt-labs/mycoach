"""add intent to planned_sessions

Revision ID: c215a1b2c3d4
Revises: c187a1b2c3d4
Create Date: 2026-11-19 12:00:00.000000

Ticket #215. A planned session records its session intent (strength, aerobic_base,
threshold, intervals, recovery, skill): the training purpose a substitute activity has to
deliver. The cardio plan emits it, gym sessions default to strength.

Existing sessions stay null, on purpose: there is no backfill, so an older plan's sessions
are never substitutable.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "c215a1b2c3d4"
down_revision: str | Sequence[str] | None = "c187a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return db_roles.COACHING_SCHEMA if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    with op.batch_alter_table("planned_sessions", schema=_schema()) as batch_op:
        batch_op.add_column(sa.Column("intent", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("planned_sessions", schema=_schema()) as batch_op:
        batch_op.drop_column("intent")
