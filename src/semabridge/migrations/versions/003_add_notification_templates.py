"""Create notification_templates table for Jinja2 templates.

Revision ID: 003_add_notification_templates
Revises: 002_add_snowflake_flushed_at
Create Date: 2024-01-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '003_add_notification_templates'
down_revision = '002_add_snowflake_flushed_at'
branch_labels = None
depends_on = None


def upgrade():
    """Create notification_templates table for per-channel Jinja2 templates."""
    op.create_table(
        'notification_templates',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column('channel_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('level_mask', sa.Integer(), nullable=False, comment='Bitmask of notification levels'),
        sa.Column('title_template', sa.String(4000), nullable=False, comment='Jinja2 template for title'),
        sa.Column('body_template', sa.String(8000), nullable=False, comment='Jinja2 template for body/message'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false(), comment='Use as fallback when no specific template matches'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['channel_id'], ['notification_channels.id'], ondelete='CASCADE'),
    )
    
    # Create indexes
    op.create_index('idx_template_channel_id', 'notification_templates', ['channel_id'])
    op.create_index('idx_template_channel_level', 'notification_templates', ['channel_id', 'level_mask'])
    op.create_index('idx_template_channel_is_default', 'notification_templates', ['channel_id', 'is_default'])


def downgrade():
    """Drop notification_templates table."""
    op.drop_index('idx_template_channel_is_default', 'notification_templates')
    op.drop_index('idx_template_channel_level', 'notification_templates')
    op.drop_index('idx_template_channel_id', 'notification_templates')
    op.drop_table('notification_templates')
