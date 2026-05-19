"""Create notification tables.

Revision ID: 001_create_notifications
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_create_notifications'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create notification_channels table
    op.create_table(
        'notification_channels',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('channel_type', sa.Enum('slack', 'teams', 'email', 'webhook', 'pagerduty', 'snowflake', name='channeltypeenum'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('config_json', sa.Text(), nullable=False),
        sa.Column('level_mask', sa.Integer(), nullable=False, server_default='63'),
        sa.Column('project_scope', sa.String(255), nullable=True),
        sa.Column('quiet_hours_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('quiet_hours_start', sa.Time(), nullable=True),
        sa.Column('quiet_hours_end', sa.Time(), nullable=True),
        sa.Column('timezone', sa.String(63), nullable=False, server_default='UTC'),
        sa.Column('digest_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('status', sa.Enum('ACTIVE', 'DEGRADED', 'DISABLED', name='channelstatusenum'), nullable=False, server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    
    # Create indexes on notification_channels
    op.create_index('idx_channel_type_enabled', 'notification_channels', ['channel_type', 'enabled'])
    op.create_index('idx_channel_project_scope', 'notification_channels', ['project_scope'])
    op.create_index('idx_channel_name', 'notification_channels', ['name'])
    
    # Create notification_logs table
    op.create_table(
        'notification_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column('event_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('channel_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.Enum('delivered', 'failed', 'retrying', 'dead', 'skipped_dedupe', 'skipped_quiet', name='deliverystatusenum'), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('response_code', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['channel_id'], ['notification_channels.id'], ondelete='CASCADE'),
    )
    
    # Create indexes on notification_logs
    op.create_index('idx_log_event_id', 'notification_logs', ['event_id'])
    op.create_index('idx_log_channel_id', 'notification_logs', ['channel_id'])
    op.create_index('idx_log_status', 'notification_logs', ['status'])
    op.create_index('idx_log_created_at', 'notification_logs', ['created_at'])
    op.create_index('idx_log_event_channel', 'notification_logs', ['event_id', 'channel_id'])
    op.create_index('idx_log_status_created', 'notification_logs', ['status', 'created_at'])
    
    # Create notification_dedupes table
    op.create_table(
        'notification_dedupes',
        sa.Column('fingerprint', sa.String(64), nullable=False, primary_key=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    
    # Create indexes on notification_dedupes
    op.create_index('idx_dedupe_expires', 'notification_dedupes', ['expires_at'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_dedupe_expires', 'notification_dedupes')
    op.drop_index('idx_log_status_created', 'notification_logs')
    op.drop_index('idx_log_event_channel', 'notification_logs')
    op.drop_index('idx_log_created_at', 'notification_logs')
    op.drop_index('idx_log_status', 'notification_logs')
    op.drop_index('idx_log_channel_id', 'notification_logs')
    op.drop_index('idx_log_event_id', 'notification_logs')
    op.drop_index('idx_channel_project_scope', 'notification_channels')
    op.drop_index('idx_channel_type_enabled', 'notification_channels')
    op.drop_index('idx_channel_name', 'notification_channels')
    
    # Drop tables
    op.drop_table('notification_dedupes')
    op.drop_table('notification_logs')
    op.drop_table('notification_channels')
    
    # Drop enums
    op.execute('DROP TYPE IF EXISTS deliverystatusenum')
    op.execute('DROP TYPE IF EXISTS channelstatusenum')
    op.execute('DROP TYPE IF EXISTS channeltypeenum')
