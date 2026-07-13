"""add external_id to activities

Revision ID: a1b2c3d4e5f6
Revises: 745b7e4011b2
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '745b7e4011b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('activities', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_id', sa.String(length=100), nullable=True))
        batch_op.create_index('ix_activities_external_id', ['external_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('activities', schema=None) as batch_op:
        batch_op.drop_index('ix_activities_external_id')
        batch_op.drop_column('external_id')
