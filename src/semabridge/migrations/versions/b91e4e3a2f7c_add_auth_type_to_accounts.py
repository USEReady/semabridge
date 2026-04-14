"""add auth_type to accounts

Revision ID: b91e4e3a2f7c
Revises: a3c91f2e7b84
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b91e4e3a2f7c"
down_revision: Union[str, Sequence[str], None] = "a3c91f2e7b84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "accounts" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("accounts")}
    if "auth_type" not in existing_columns:
        op.add_column("accounts", sa.Column("auth_type", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "accounts" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("accounts")}
    if "auth_type" in existing_columns:
        op.drop_column("accounts", "auth_type")
