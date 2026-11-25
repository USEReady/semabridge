"""databricks_probe_evidence: Databricks capability-probe runs (Phase 3.5).

Append-only proof records per workspace host + warehouse. Additive and
idempotent (the table may already exist when the ORM's create_all ran first).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0007_databricks_probe_evidence"
down_revision = "sb0006_mapping_override_origin"
branch_labels = None
depends_on = None

TABLE = "databricks_probe_evidence"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    ts = sa.DateTime(timezone=True)
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("workspace_host", sa.String(512), nullable=False),
        sa.Column("warehouse_id", sa.String(128), nullable=False),
        sa.Column("probe_schema", sa.String(512), nullable=True),
        sa.Column("dbsql_version", sa.String(128), nullable=True),
        sa.Column("platform_json", sa.Text, nullable=True),
        sa.Column("suite_revision", sa.String(32), nullable=False),
        sa.Column("writer_revision", sa.String(32), nullable=False),
        sa.Column("outcomes_json", sa.Text, nullable=False),
        sa.Column("cleanup_json", sa.Text, nullable=True),
        sa.Column("started_at", ts, nullable=False),
        sa.Column("completed_at", ts, nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
    )
    op.create_index("ix_dbx_probe_target", TABLE, ["workspace_host", "warehouse_id", "started_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)
