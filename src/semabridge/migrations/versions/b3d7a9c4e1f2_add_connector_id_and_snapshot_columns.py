"""add connector_id and snapshot columns to runs and snapshots tables

Revision ID: b3d7a9c4e1f2
Revises: fafd9bccf834
Create Date: 2026-04-28 17:11:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3d7a9c4e1f2"
down_revision: Union[str, Sequence[str], None] = "fafd9bccf834"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "snapshots", "connector_id"):
        op.add_column("snapshots", sa.Column("connector_id", sa.String(length=36), nullable=True))
    if not _has_column(inspector, "snapshots", "trigger"):
        op.add_column("snapshots", sa.Column("trigger", sa.String(length=50), nullable=True))
    if not _has_index(inspector, "snapshots", "ix_snapshots_connector"):
        op.create_index("ix_snapshots_connector", "snapshots", ["connector_id"])
    if not _has_index(inspector, "snapshots", "ix_snapshots_trigger"):
        op.create_index("ix_snapshots_trigger", "snapshots", ["trigger"])

    if not _has_column(inspector, "runs", "before_tgt_snapshots"):
        op.add_column("runs", sa.Column("before_tgt_snapshots", sa.Text(), nullable=True))
    if not _has_column(inspector, "runs", "after_tgt_snapshots"):
        op.add_column("runs", sa.Column("after_tgt_snapshots", sa.Text(), nullable=True))
    if not _has_column(inspector, "runs", "before_target_snapshot_ids"):
        op.add_column("runs", sa.Column("before_target_snapshot_ids", sa.Text(), nullable=True))
    if not _has_column(inspector, "runs", "after_target_snapshot_ids"):
        op.add_column("runs", sa.Column("after_target_snapshot_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "runs", "after_tgt_snapshots"):
        op.drop_column("runs", "after_tgt_snapshots")
    if _has_column(inspector, "runs", "before_tgt_snapshots"):
        op.drop_column("runs", "before_tgt_snapshots")

    if _has_index(inspector, "snapshots", "ix_snapshots_trigger"):
        op.drop_index("ix_snapshots_trigger", table_name="snapshots")
    if _has_index(inspector, "snapshots", "ix_snapshots_connector"):
        op.drop_index("ix_snapshots_connector", table_name="snapshots")
    if _has_column(inspector, "snapshots", "trigger"):
        op.drop_column("snapshots", "trigger")
    if _has_column(inspector, "snapshots", "connector_id"):
        op.drop_column("snapshots", "connector_id")
