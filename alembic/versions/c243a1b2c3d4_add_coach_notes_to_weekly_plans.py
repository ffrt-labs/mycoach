"""add coach_notes to weekly_plans

Revision ID: c243a1b2c3d4
Revises: c221a1b2c3d4
Create Date: 2026-11-19 21:00:00.000000

Ticket #243. The cardio plan answers in Coach notes (prompt v5): the plan-level
slots (the email's focus sentence, its Guardrails and the goal assessment) are
stored on the plan as JSON. Older plans keep a null: there is no backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "c243a1b2c3d4"
down_revision: str | Sequence[str] | None = "c221a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return db_roles.COACHING_SCHEMA if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    with op.batch_alter_table("weekly_plans", schema=_schema()) as batch:
        batch.add_column(sa.Column("coach_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("weekly_plans", schema=_schema()) as batch:
        batch.drop_column("coach_notes")
