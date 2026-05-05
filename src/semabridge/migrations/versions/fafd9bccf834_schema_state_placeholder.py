"""schema state placeholder

Revision ID: fafd9bccf834
Revises: 89c75544e395
Create Date: 2026-04-28 17:10:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "fafd9bccf834"
down_revision: Union[str, Sequence[str], None] = "89c75544e395"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Placeholder for databases already stamped at this revision."""
    pass


def downgrade() -> None:
    """No-op placeholder downgrade."""
    pass
