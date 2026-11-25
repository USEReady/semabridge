"""Shared application state in the database (state_documents).

One row per entity (project entry, mapping, schedule, folder, run list, run
detail blob ...) keyed by (collection, key), with a content digest and a
tombstone flag. It replaces the per-process JSON compat store as the source
of truth; the application imports an existing JSON store once, on its first
start against an empty table. Additive; nothing is dropped.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0006_state_documents"
down_revision = "sb0005_multi_process_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("state_documents"):
        return
    op.create_table(
        "state_documents",
        sa.Column("collection", sa.String(64), primary_key=True),
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("data_json", sa.Text, nullable=True),
        sa.Column("digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=True),
    )
    op.create_index("ix_state_documents_updated_at", "state_documents", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_state_documents_updated_at", table_name="state_documents")
    op.drop_table("state_documents")
