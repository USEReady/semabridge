"""Backfill existing snapshot rows with a default source_format value.

Revision ID: ff2a3b4c5d6
Revises: e7f1c2d3b4a5
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa


revision = "ff2a3b4c5d6"
down_revision = "e7f1c2d3b4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Set a conservative default for any existing rows where source_format is NULL/empty
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE snapshots SET source_format = :val WHERE source_format IS NULL OR TRIM(source_format) = ''"
        ),
        {"val": "TMDL"},
    )


def downgrade() -> None:
    # No-op downgrade: leaving historical values is safer than reverting data.
    pass
