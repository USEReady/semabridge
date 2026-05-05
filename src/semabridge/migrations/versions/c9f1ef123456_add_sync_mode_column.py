"""add runs.sync_mode column (defensive)

Revision ID: c9f1ef123456
Revises: a2b3c4d5e6f7
Create Date: 2026-05-04 19:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9f1ef123456'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the `sync_mode` column to `runs` if it does not exist."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('runs')]
    if 'sync_mode' not in cols:
        op.add_column('runs', sa.Column('sync_mode', sa.String(length=20), nullable=False, server_default='copy'))


def downgrade() -> None:
    """Remove the `sync_mode` column if present."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('runs')]
    if 'sync_mode' in cols:
        op.drop_column('runs', 'sync_mode')
