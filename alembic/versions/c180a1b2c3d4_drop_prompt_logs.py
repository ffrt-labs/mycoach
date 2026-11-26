"""drop prompt_logs

Revision ID: c180a1b2c3d4
Revises: c243a1b2c3d4
Create Date: 2026-11-26 12:00:00.000000

Ticket #180. Langfuse is now the only record of coaching prompts and calls, so the
hand-built call log goes. Its rows were dumped to the homelab backups before this
runs (they outlive Langfuse's 90-day trace retention). On Postgres the table lives in
the ``coaching`` schema since c184. Downgrade brings back an empty table of the same
shape; the rows come back only from the dump.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "c180a1b2c3d4"
down_revision: str | Sequence[str] | None = "c243a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return db_roles.COACHING_SCHEMA if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    op.drop_table("prompt_logs", schema=_schema())


def downgrade() -> None:
    op.create_table(
        "prompt_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prompt_type", sa.String(length=50), nullable=False),
        sa.Column("prompt_version", sa.String(length=10), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=_schema(),
    )
