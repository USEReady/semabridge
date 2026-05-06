"""Add sync_mode column to snapshots table for v4.3 rollback tracking

Revision ID: d1e2f3g4h5i6
Revises: c9f1ef123456
Create Date: 2026-05-06 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3g4h5i6'
down_revision: Union[str, Sequence[str], None] = 'c9f1ef123456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the `sync_mode` column to `snapshots` table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('snapshots')]
    if 'sync_mode' not in cols:
        op.add_column(
            'snapshots',
            sa.Column(
                'sync_mode',
                sa.String(length=20),
                nullable=False,
                server_default='copy'
            )
        )
        print("✓ Added sync_mode column to snapshots table")
    else:
        print("→ sync_mode column already exists in snapshots table")


def downgrade() -> None:
    """Remove the `sync_mode` column if present."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('snapshots')]
    if 'sync_mode' in cols:
        op.drop_column('snapshots', 'sync_mode')
        print("✓ Removed sync_mode column from snapshots table")
    else:
        print("→ sync_mode column does not exist in snapshots table")
