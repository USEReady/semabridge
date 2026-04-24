"""add version control fields

Revision ID: f2b8c9d1e0a4
Revises: e5a72b8f1d93
Create Date: 2026-04-24 13:50:00.000000

"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "f2b8c9d1e0a4"
down_revision: str | Sequence[str] | None = "e5a72b8f1d93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # snapshots table
    if not _has_column(inspector, "snapshots", "deleted_at"):
        op.add_column("snapshots", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    # runs table
    if not _has_column(inspector, "runs", "run_type"):
        op.add_column("runs", sa.Column("run_type", sa.String(length=50), nullable=True))
    if not _has_column(inspector, "runs", "before_src_snapshot_id"):
        op.add_column("runs", sa.Column("before_src_snapshot_id", sa.String(length=36), nullable=True))
    if not _has_column(inspector, "runs", "restore_snapshot_id"):
        op.add_column("runs", sa.Column("restore_snapshot_id", sa.String(length=36), nullable=True))
    if not _has_column(inspector, "runs", "before_target_snapshot_ids"):
        op.add_column("runs", sa.Column("before_target_snapshot_ids", sa.Text(), nullable=True))
    if not _has_column(inspector, "runs", "after_target_snapshot_ids"):
        op.add_column("runs", sa.Column("after_target_snapshot_ids", sa.Text(), nullable=True))

def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # runs table
    for col in ["after_target_snapshot_ids", "before_target_snapshot_ids", "restore_snapshot_id", "before_src_snapshot_id", "run_type"]:
        if _has_column(inspector, "runs", col):
            op.drop_column("runs", col)

    # snapshots table
    if _has_column(inspector, "snapshots", "deleted_at"):
        op.drop_column("snapshots", "deleted_at")
