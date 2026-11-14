"""add generation_error to planned_sessions

Revision ID: c187a1b2c3d4
Revises: c186a1b2c3d4
Create Date: 2026-11-14 12:00:00.000000

Ticket #187. A planned session standing in for a failed LLM generation now records why
the generation failed, instead of the reason living only in the logs. The daily briefing
reads it to keep the placeholder's failure text out of the plan it describes.

Existing placeholders predate the column and stay null: the failure reason was never
stored, so there is nothing to backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "c187a1b2c3d4"
down_revision: str | Sequence[str] | None = "c186a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return db_roles.COACHING_SCHEMA if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    with op.batch_alter_table("planned_sessions", schema=_schema()) as batch_op:
        batch_op.add_column(sa.Column("generation_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("planned_sessions", schema=_schema()) as batch_op:
        batch_op.drop_column("generation_error")
