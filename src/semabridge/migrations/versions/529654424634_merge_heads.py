"""merge heads

Revision ID: 529654424634
Revises: 004_add_notification_routing_rules, d1e2f3g4h5i6
Create Date: 2026-05-15 15:32:30.499903

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '529654424634'
down_revision: Union[str, Sequence[str], None] = ('004_add_notification_routing_rules', 'd1e2f3g4h5i6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
