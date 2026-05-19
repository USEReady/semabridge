"""Add snowflake_flushed_at column to notification_logs.

Revision ID: 002_add_snowflake_flushed_at
Revises: 001_create_notifications
Create Date: 2024-01-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002_add_snowflake_flushed_at'
down_revision = '001_create_notifications'
branch_labels = None
depends_on = None


def upgrade():
    """Add snowflake_flushed_at column for Snowflake analytics flush tracking."""
    # Add column with NULL default (idempotent with check_first)
    with op.batch_operations.batch_alter_table('notification_logs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'snowflake_flushed_at',
                sa.DateTime(),
                nullable=True,
                comment='Timestamp when this log was flushed to Snowflake for analytics'
            )
        )
        # Index for finding unflushed logs
        batch_op.create_index('idx_log_snowflake_flushed', ['snowflake_flushed_at'])


def downgrade():
    """Remove snowflake_flushed_at column."""
    with op.batch_operations.batch_alter_table('notification_logs', schema=None) as batch_op:
        batch_op.drop_index('idx_log_snowflake_flushed')
        batch_op.drop_column('snowflake_flushed_at')
