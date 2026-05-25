"""add_snapshot_project_status_ts

Revision ID: b7c6d5e4f3a2
Revises: e7f8a9b0c1d2
Create Date: 2026-05-25 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c6d5e4f3a2'
down_revision: Union[str, Sequence[str], None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add composite index for snapshots (project_id, status, timestamp)."""
    with op.batch_alter_table('snapshots', schema=None) as batch_op:
        batch_op.create_index('ix_snapshots_project_status_ts', ['project_id', 'status', 'timestamp'], unique=False)


def downgrade() -> None:
    """Downgrade schema: drop composite index."""
    with op.batch_alter_table('snapshots', schema=None) as batch_op:
        batch_op.drop_index('ix_snapshots_project_status_ts')
