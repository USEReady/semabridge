"""Create notification_routing_rules table for rule-based routing.

Revision ID: 004_add_notification_routing_rules
Revises: 003_add_notification_templates
Create Date: 2024-01-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '004_add_notification_routing_rules'
down_revision = '003_add_notification_templates'
branch_labels = None
depends_on = None


def upgrade():
    """Create notification_routing_rules table for rule-based message routing."""
    op.create_table(
        'notification_routing_rules',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column('name', sa.String(255), nullable=False, comment='Human-readable rule name'),
        sa.Column('priority', sa.Integer(), nullable=False, comment='Lower number = higher priority (evaluated first)'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('conditions', sa.JSON(), nullable=False, comment='JSON conditions: level_mask, project_ids, source_pattern, title_contains, payload_key_exists, payload_value_matches'),
        sa.Column('channel_ids', postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, comment='Target channels for matched events'),
        sa.Column('stop_on_match', sa.Boolean(), nullable=False, server_default=sa.false(), comment='Stop evaluating rules after this one matches'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    
    # Create indexes
    op.create_index('idx_routing_priority', 'notification_routing_rules', ['priority'])
    op.create_index('idx_routing_enabled', 'notification_routing_rules', ['enabled'])
    op.create_index('idx_routing_enabled_priority', 'notification_routing_rules', ['enabled', 'priority'])


def downgrade():
    """Drop notification_routing_rules table."""
    op.drop_index('idx_routing_enabled_priority', 'notification_routing_rules')
    op.drop_index('idx_routing_enabled', 'notification_routing_rules')
    op.drop_index('idx_routing_priority', 'notification_routing_rules')
    op.drop_table('notification_routing_rules')
