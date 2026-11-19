"""add travel columns to life_context_episodes

Revision ID: c221a1b2c3d4
Revises: c220a1b2c3d4
Create Date: 2026-11-19 20:00:00.000000

Ticket #221. Travel is a kind of life context episode: its kind-specific fields are
nullable columns only travel sets. ``equipment`` (none / hotel_gym / full_gym),
``tz_shift_hours`` (destination minus home, may be fractional) and ``no_training``
("no training possible": each covered day excuses one session).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "c221a1b2c3d4"
down_revision: str | Sequence[str] | None = "c220a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("equipment", "tz_shift_hours", "no_training")


def _schema() -> str | None:
    return db_roles.COACHING_SCHEMA if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    with op.batch_alter_table("life_context_episodes", schema=_schema()) as batch:
        batch.add_column(sa.Column("equipment", sa.String(length=10), nullable=True))
        batch.add_column(sa.Column("tz_shift_hours", sa.Float(), nullable=True))
        batch.add_column(sa.Column("no_training", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("life_context_episodes", schema=_schema()) as batch:
        for column in reversed(COLUMNS):
            batch.drop_column(column)
