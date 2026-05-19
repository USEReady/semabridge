"""Increase String length to 255 for all internal IDs

Revision ID: c65172df5585
Revises: 529654424634
Create Date: 2026-05-15 15:34:10.420832

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c65172df5585'
down_revision: Union[str, Sequence[str], None] = '529654424634'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('projects', 'project_id', type_=sa.String(255))
    op.alter_column('runs', 'project_id', type_=sa.String(255))
    op.alter_column('runs', 'run_id', type_=sa.String(255))
    op.execute('ALTER TABLE snapshots ALTER COLUMN project_id TYPE VARCHAR(255)')
    op.execute('ALTER TABLE snapshots ALTER COLUMN snapshot_id TYPE VARCHAR(255)')
    op.execute('ALTER TABLE accounts ALTER COLUMN id TYPE VARCHAR(255)')


def downgrade() -> None:
    """Downgrade schema."""
    pass
