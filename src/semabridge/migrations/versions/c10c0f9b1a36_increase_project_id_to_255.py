"""increase project_id to 255

Revision ID: c10c0f9b1a36
Revises: c9f1ef123456
Create Date: 2026-05-05 12:06:36.338424

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c10c0f9b1a36'
down_revision: Union[str, Sequence[str], None] = 'c9f1ef123456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # postgres requires changing FK constraints when altering type if they conflict
    # but altering VARCHAR length usually works in-place in recent PG versions
    # unless there is an index that exceeds limits.
    op.alter_column('projects', 'project_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=255),
               existing_nullable=False)
    op.alter_column('snapshots', 'project_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=255),
               existing_nullable=False)
    op.alter_column('runs', 'project_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=255),
               existing_nullable=False)
    op.alter_column('retention_policies', 'project_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=255),
               existing_nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('retention_policies', 'project_id',
               existing_type=sa.String(length=255),
               type_=sa.String(length=36),
               existing_nullable=False)
    op.alter_column('runs', 'project_id',
               existing_type=sa.String(length=255),
               type_=sa.String(length=36),
               existing_nullable=False)
    op.alter_column('snapshots', 'project_id',
               existing_type=sa.String(length=255),
               type_=sa.String(length=36),
               existing_nullable=False)
    op.alter_column('projects', 'project_id',
               existing_type=sa.String(length=255),
               type_=sa.String(length=36),
               existing_nullable=False)
