"""add missing snapshot and run fields

Revision ID: a2b3c4d5e6f7
Revises: b3d7a9c4e1f2
Create Date: 2026-04-29 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'b3d7a9c4e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if columns exist before adding (defensive)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    # snapshots table
    snap_cols = [col['name'] for col in inspector.get_columns('snapshots')]
    if 'connector_id' not in snap_cols:
        op.add_column('snapshots', sa.Column('connector_id', sa.String(length=36), nullable=True))
    if 'trigger' not in snap_cols:
        op.add_column('snapshots', sa.Column('trigger', sa.String(length=50), nullable=True))
        
    # runs table
    run_cols = [col['name'] for col in inspector.get_columns('runs')]
    if 'sync_mode' not in run_cols:
        op.add_column('runs', sa.Column('sync_mode', sa.String(length=20), nullable=False, server_default='copy'))
    if 'restored_from_snapshot_id' not in run_cols:
        op.add_column('runs', sa.Column('restored_from_snapshot_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'restored_from_snapshot_id')
    op.drop_column('runs', 'sync_mode')
    op.drop_column('snapshots', 'trigger')
    op.drop_column('snapshots', 'connector_id')
