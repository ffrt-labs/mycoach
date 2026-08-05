"""Add default_availability table and availability_source on weekly_plans

Revision ID: a2b3c4d5e6f7
Revises: f6a7b8c9d0e1
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'default_availability',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('sport', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )

    with op.batch_alter_table('weekly_plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('availability_source', sa.String(length=20), nullable=True))

    # Seed the standing schedule from the most recently declared week, so the
    # feature is live without a setup step. Picks the single latest week_start
    # per user and copies its rows; a database with no declared weeks at all
    # (or no users) seeds nothing, leaving an empty (still valid) default.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO default_availability (user_id, day_of_week, sport)
            SELECT wa.user_id, wa.day_of_week, wa.sport
            FROM weekly_availabilities wa
            INNER JOIN (
                SELECT user_id, MAX(week_start) AS latest_week_start
                FROM weekly_availabilities
                GROUP BY user_id
            ) latest
                ON latest.user_id = wa.user_id
                AND latest.latest_week_start = wa.week_start
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('weekly_plans', schema=None) as batch_op:
        batch_op.drop_column('availability_source')

    op.drop_table('default_availability')
