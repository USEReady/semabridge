"""add local folders registry

Revision ID: a3c91f2e7b84
Revises: ff10eddde5b9
Create Date: 2026-04-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c91f2e7b84'
down_revision: Union[str, Sequence[str], None] = 'ff10eddde5b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	"""Upgrade schema."""
	op.create_table(
		'local_folders',
		sa.Column('id', sa.String(length=36), nullable=False),
		sa.Column('tag_name', sa.String(length=255), nullable=False),
		sa.Column('absolute_path', sa.Text(), nullable=False),
		sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
		sa.PrimaryKeyConstraint('id'),
		sa.UniqueConstraint('absolute_path', name='uq_local_folders_absolute_path'),
		sa.UniqueConstraint('tag_name', name='uq_local_folders_tag_name'),
	)
	with op.batch_alter_table('local_folders', schema=None) as batch_op:
		batch_op.create_index('ix_local_folders_is_active', ['is_active'], unique=False)


def downgrade() -> None:
	"""Downgrade schema."""
	with op.batch_alter_table('local_folders', schema=None) as batch_op:
		batch_op.drop_index('ix_local_folders_is_active')

	op.drop_table('local_folders')
