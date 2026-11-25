"""mapping_overrides.origin: provenance of a saved target name.

NULL  -- unmarked (every row saved before this revision, and every
         Snowflake / Fabric / no-target row: those platforms do not write it).
"user" -- an explicit user edit (written by Databricks projects only).

Additive, nullable and idempotent (the column may already exist when the
ORM's create_all ran first). Existing rows stay NULL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0006_mapping_override_origin"
down_revision = "sb0005_multi_process_state"
branch_labels = None
depends_on = None


def _columns() -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("mapping_overrides"):
        return set()
    return {c["name"] for c in inspector.get_columns("mapping_overrides")}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("mapping_overrides") and "origin" not in _columns():
        op.add_column("mapping_overrides", sa.Column("origin", sa.String(32), nullable=True))


def downgrade() -> None:
    if "origin" in _columns():
        op.drop_column("mapping_overrides", "origin")
