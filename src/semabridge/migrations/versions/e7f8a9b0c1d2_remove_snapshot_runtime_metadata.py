"""remove runtime metadata columns from snapshots

Revision ID: e7f8a9b0c1d2
Revises: d1e2f3g4h5i6
Create Date: 2026-05-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d1e2f3g4h5i6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Drop runtime-only metadata from snapshots."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_index(inspector, "snapshots", "ix_snapshots_trigger"):
        op.drop_index("ix_snapshots_trigger", table_name="snapshots")
    if _has_index(inspector, "snapshots", "ix_snapshots_connector"):
        op.drop_index("ix_snapshots_connector", table_name="snapshots")

    if _has_column(inspector, "snapshots", "trigger"):
        op.drop_column("snapshots", "trigger")
    if _has_column(inspector, "snapshots", "connector_id"):
        op.drop_column("snapshots", "connector_id")
    if _has_column(inspector, "snapshots", "initiated_by"):
        op.drop_column("snapshots", "initiated_by")


def downgrade() -> None:
    """Restore runtime-only metadata columns if needed."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "snapshots", "initiated_by"):
        op.add_column("snapshots", sa.Column("initiated_by", sa.String(length=20), nullable=False, server_default="cli"))
    if not _has_column(inspector, "snapshots", "connector_id"):
        op.add_column("snapshots", sa.Column("connector_id", sa.String(length=36), nullable=True))
    if not _has_column(inspector, "snapshots", "trigger"):
        op.add_column("snapshots", sa.Column("trigger", sa.String(length=50), nullable=True))

    if not _has_index(inspector, "snapshots", "ix_snapshots_connector"):
        op.create_index("ix_snapshots_connector", "snapshots", ["connector_id"])
    if not _has_index(inspector, "snapshots", "ix_snapshots_trigger"):
        op.create_index("ix_snapshots_trigger", "snapshots", ["trigger"])