"""scope health snapshot uniqueness to user, date, and source

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _snapshot_columns() -> list[sa.Column]:
    """Column definitions for daily_health_snapshots, shared by both target tables."""
    return [
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('resting_hr', sa.Integer(), nullable=True),
        sa.Column('max_hr', sa.Integer(), nullable=True),
        sa.Column('avg_hr', sa.Integer(), nullable=True),
        sa.Column('hrv_status', sa.Float(), nullable=True),
        sa.Column('hrv_7day_avg', sa.Float(), nullable=True),
        sa.Column('hrv_status_text', sa.String(length=50), nullable=True),
        sa.Column('sleep_duration_minutes', sa.Integer(), nullable=True),
        sa.Column('sleep_score', sa.Integer(), nullable=True),
        sa.Column('sleep_deep_minutes', sa.Integer(), nullable=True),
        sa.Column('sleep_light_minutes', sa.Integer(), nullable=True),
        sa.Column('sleep_rem_minutes', sa.Integer(), nullable=True),
        sa.Column('sleep_awake_minutes', sa.Integer(), nullable=True),
        sa.Column('body_battery_high', sa.Integer(), nullable=True),
        sa.Column('body_battery_low', sa.Integer(), nullable=True),
        sa.Column('body_battery_morning', sa.Integer(), nullable=True),
        sa.Column('avg_stress', sa.Integer(), nullable=True),
        sa.Column('training_readiness', sa.Integer(), nullable=True),
        sa.Column('training_load', sa.Float(), nullable=True),
        sa.Column('training_status', sa.String(length=50), nullable=True),
        sa.Column('vo2_max', sa.Float(), nullable=True),
        sa.Column('recovery_time_hours', sa.Float(), nullable=True),
        sa.Column('load_focus', sa.Text(), nullable=True),
        sa.Column('steps', sa.Integer(), nullable=True),
        sa.Column('respiration_avg', sa.Float(), nullable=True),
        sa.Column('spo2_avg', sa.Float(), nullable=True),
        sa.Column('intensity_minutes', sa.Integer(), nullable=True),
        sa.Column('raw_data', sa.Text(), nullable=True),
        sa.Column('data_source', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    target_metadata = sa.MetaData()
    target_table = sa.Table(
        'daily_health_snapshots',
        target_metadata,
        *_snapshot_columns(),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'snapshot_date', 'data_source',
            name='uq_daily_health_snapshots_user_date_source',
        ),
    )

    with op.batch_alter_table(
        'daily_health_snapshots', recreate='always', copy_from=target_table
    ):
        pass


def downgrade() -> None:
    """Downgrade schema."""
    target_metadata = sa.MetaData()
    target_table = sa.Table(
        'daily_health_snapshots',
        target_metadata,
        *_snapshot_columns(),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('snapshot_date'),
    )

    with op.batch_alter_table(
        'daily_health_snapshots', recreate='always', copy_from=target_table
    ):
        pass
