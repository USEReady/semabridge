"""Add source_format column to snapshots table for metadata provenance.

Revision ID: e7f1c2d3b4a5
Revises: d1e2f3g4h5i6
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa


revision = "e7f1c2d3b4a5"
down_revision = "d1e2f3g4h5i6"
branch_labels = None
depends_on = None


def _get_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    cols = _get_columns("snapshots")
    if "source_format" not in cols:
        op.add_column(
            "snapshots",
            sa.Column("source_format", sa.String(length=20), nullable=False, server_default="TMDL"),
        )


def downgrade() -> None:
    cols = _get_columns("snapshots")
    if "source_format" in cols:
        op.drop_column("snapshots", "source_format")

