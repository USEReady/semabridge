"""add connection_tag to projects

Revision ID: e5a72b8f1d93
Revises: d9f3a1b7c2e4
Create Date: 2026-04-20 21:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a72b8f1d93"
down_revision: str | Sequence[str] | None = "d9f3a1b7c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    """Add connection_tag column to projects table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "projects", "connection_tag"):
        op.add_column(
            "projects",
            sa.Column("connection_tag", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    """Remove connection_tag column from projects table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "projects", "connection_tag"):
        op.drop_column("projects", "connection_tag")
