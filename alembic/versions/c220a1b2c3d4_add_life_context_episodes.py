"""add life_context_episodes

Revision ID: c220a1b2c3d4
Revises: c215a1b2c3d4
Create Date: 2026-11-19 18:00:00.000000

Ticket #220. A life context episode is a stretch of the athlete's life that coaching
reads around: an illness now, travel in #221. One table for every kind: shared
dates and note, plus nullable kind-specific columns (illness: ``severity``).
``ended_on`` is the last affected day, null while the episode is open.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "c220a1b2c3d4"
down_revision: str | Sequence[str] | None = "c215a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return db_roles.COACHING_SCHEMA if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    schema = _schema()
    users = f"{schema}.users.id" if schema else "users.id"
    op.create_table(
        "life_context_episodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(users), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "ix_life_context_episodes_user_id", "life_context_episodes", ["user_id"], schema=schema
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_life_context_episodes_user_id", table_name="life_context_episodes", schema=schema
    )
    op.drop_table("life_context_episodes", schema=schema)
