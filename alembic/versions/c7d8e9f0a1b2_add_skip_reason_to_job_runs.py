"""add skip_reason to job_runs

Revision ID: c7d8e9f0a1b2
Revises: b3c4d5e6f7a8
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable and left unbackfilled — existing rows genuinely have no recorded
    # skip reason, and the `error` column keeps carrying failure detail as before.
    op.add_column(
        'job_runs',
        sa.Column('skip_reason', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_runs', 'skip_reason')
