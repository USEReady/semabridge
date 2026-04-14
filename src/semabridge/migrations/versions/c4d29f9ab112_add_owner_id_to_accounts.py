"""add owner_id to accounts

Revision ID: c4d29f9ab112
Revises: b91e4e3a2f7c
Create Date: 2026-04-14 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d29f9ab112"
down_revision: Union[str, Sequence[str], None] = "b91e4e3a2f7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "accounts" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("accounts")}
    if "owner_id" not in existing_columns:
        op.add_column("accounts", sa.Column("owner_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "accounts" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("accounts")}
    if "owner_id" in existing_columns:
        op.drop_column("accounts", "owner_id")
