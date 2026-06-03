"""Add resolution_note and escalated columns to sync_conflicts table.

Supports per-conflict human review workflow:
  - resolution_note: optional human explanation for the resolution decision
  - escalated: flag that blocks deploy until a human explicitly clears the conflict

Revision ID: a1b2c3d4e5f6
Revises: fafd9bccf834
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "fafd9bccf834"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add resolution_note (nullable text)
    op.add_column(
        "sync_conflicts",
        sa.Column("resolution_note", sa.Text(), nullable=True),
    )
    # Add escalated (boolean, defaults to false)
    op.add_column(
        "sync_conflicts",
        sa.Column(
            "escalated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sync_conflicts", "escalated")
    op.drop_column("sync_conflicts", "resolution_note")
