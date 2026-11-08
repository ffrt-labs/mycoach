"""record where each artifact's prompt came from (#174)

Widens coaching_insights.prompt_version to hold "lf:<n>" / "bundled:v2" and
adds weekly_plans.gym_prompt_version and cardio_prompt_version.

Revision ID: a7c41e9d2b56
Revises: 7cf0523de01b
Create Date: 2026-11-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c41e9d2b56'
down_revision: Union[str, Sequence[str], None] = '7cf0523de01b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('coaching_insights') as batch:
        batch.alter_column(
            'prompt_version',
            existing_type=sa.String(length=10),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
    # Nullable and unbackfilled: older plans recorded no per-track version.
    with op.batch_alter_table('weekly_plans') as batch:
        batch.add_column(sa.Column('gym_prompt_version', sa.String(length=20), nullable=True))
        batch.add_column(sa.Column('cardio_prompt_version', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    Narrowing back to 10 characters fails if a row holds a longer value, so
    those are cut to the bare template version first ("bundled:v2" -> "v2").
    """
    with op.batch_alter_table('weekly_plans') as batch:
        batch.drop_column('cardio_prompt_version')
        batch.drop_column('gym_prompt_version')
    op.execute(
        "UPDATE coaching_insights SET prompt_version = 'v2' "
        "WHERE length(prompt_version) > 10"
    )
    with op.batch_alter_table('coaching_insights') as batch:
        batch.alter_column(
            'prompt_version',
            existing_type=sa.String(length=20),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
