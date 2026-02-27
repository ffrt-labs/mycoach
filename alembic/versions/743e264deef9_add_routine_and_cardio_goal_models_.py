"""add routine and cardio goal models, modify availability and planned sessions

Revision ID: 743e264deef9
Revises: 34197c0b4014
Create Date: 2026-02-20 11:51:48.266831

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '743e264deef9'
down_revision: Union[str, Sequence[str], None] = '34197c0b4014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    """Check if a table already exists (SQLite)."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"
    ), {"name": name})
    return result.scalar() is not None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column exists in a table (SQLite)."""
    conn = op.get_bind()
    result = conn.execute(sa.text(f"PRAGMA table_info('{table}')"))
    return any(row[1] == column for row in result)


def upgrade() -> None:
    """Upgrade schema."""
    if not _table_exists('workout_routines'):
        op.create_table(
            'workout_routines',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
    if not _table_exists('routine_days'):
        op.create_table(
            'routine_days',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('routine_id', sa.Integer(), sa.ForeignKey('workout_routines.id', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('day_of_week', sa.Integer(), nullable=False),
            sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        )
    if not _table_exists('routine_exercises'):
        op.create_table(
            'routine_exercises',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('routine_day_id', sa.Integer(), sa.ForeignKey('routine_days.id', ondelete='CASCADE'), nullable=False),
            sa.Column('exercise_name', sa.String(length=200), nullable=False),
            sa.Column('sets', sa.Integer(), nullable=False),
            sa.Column('rep_range', sa.String(length=20), nullable=False),
            sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('notes', sa.Text(), nullable=True),
        )
    if not _table_exists('cardio_goals'):
        op.create_table(
            'cardio_goals',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('sport', sa.String(length=50), nullable=False),
            sa.Column('weekly_target', sa.String(length=100), nullable=True),
            sa.Column('fitness_goal', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )

    if not _column_exists('planned_sessions', 'track'):
        with op.batch_alter_table('planned_sessions', schema=None) as batch_op:
            batch_op.add_column(sa.Column('track', sa.String(length=20), nullable=False, server_default='cardio'))

    if _column_exists('weekly_availabilities', 'preferred_sport'):
        with op.batch_alter_table('weekly_availabilities', schema=None) as batch_op:
            batch_op.drop_column('preferred_sport')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('weekly_availabilities', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preferred_sport', sa.VARCHAR(length=50), nullable=False, server_default='gym'))

    with op.batch_alter_table('planned_sessions', schema=None) as batch_op:
        batch_op.drop_column('track')

    op.drop_table('cardio_goals')
    op.drop_table('routine_exercises')
    op.drop_table('routine_days')
    op.drop_table('workout_routines')
