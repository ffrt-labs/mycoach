"""Add sport to weekly_availabilities, drop cardio_goals table

Revision ID: d4e5f6a7b8c9
Revises: b66448d822d1
Create Date: 2026-02-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "77bcfaf2e87c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("weekly_availabilities", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sport", sa.String(50), nullable=True))

    op.drop_table("cardio_goals")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "cardio_goals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sport", sa.String(50), nullable=False),
        sa.Column("weekly_target", sa.String(100), nullable=True),
        sa.Column("fitness_goal", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("weekly_availabilities", schema=None) as batch_op:
        batch_op.drop_column("sport")
