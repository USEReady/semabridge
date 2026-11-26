"""Databricks deploy (Phase 3.8): deploy runs, deployed objects, deployed names,
the operation journal and the per-schema lease.

Additive and idempotent (tables may already exist when the ORM's create_all
ran first).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0008_databricks_deploy"
down_revision = "sb0007_databricks_probe_evidence"
branch_labels = None
depends_on = None

TABLES = ("databricks_deployments", "databricks_deployed_objects", "databricks_deployed_names",
          "databricks_deploy_journal", "databricks_deploy_leases")


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    ts = sa.DateTime(timezone=True)
    if not insp.has_table("databricks_deployments"):
        op.create_table(
            "databricks_deployments",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(64), nullable=False, unique=True),
            sa.Column("project_id", sa.String(255), nullable=False),
            sa.Column("model_key", sa.String(255), nullable=False, server_default=""),
            sa.Column("plan_revision", sa.Integer, nullable=False),
            sa.Column("writer_revision", sa.String(32), nullable=False),
            sa.Column("dbsql_version", sa.String(128), nullable=True),
            sa.Column("metastore_id", sa.String(255), nullable=True),
            sa.Column("catalog", sa.String(255), nullable=False),
            sa.Column("schema_name", sa.String(255), nullable=False),
            sa.Column("workspace_host", sa.String(512), nullable=True),
            sa.Column("warehouse_id", sa.String(128), nullable=True),
            sa.Column("identity", sa.String(255), nullable=True),
            sa.Column("stub_form", sa.String(32), nullable=False, server_default="empty_table"),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("summary_json", sa.Text, nullable=True),
            sa.Column("started_at", ts, nullable=False),
            sa.Column("finished_at", ts, nullable=True),
        )
        op.create_index("ix_dbx_deploy_target", "databricks_deployments", ["project_id", "model_key", "started_at"])
    if not insp.has_table("databricks_deployed_objects"):
        op.create_table(
            "databricks_deployed_objects",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("project_id", sa.String(255), nullable=False),
            sa.Column("model_key", sa.String(255), nullable=False, server_default=""),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("fqn", sa.String(767), nullable=False),
            sa.Column("source", sa.String(512), nullable=True),
            sa.Column("uc_object_id", sa.String(128), nullable=True),
            sa.Column("body_sha256", sa.String(64), nullable=True),
            sa.Column("definition_hash", sa.String(64), nullable=True),
            sa.Column("readiness", sa.String(32), nullable=True),
            sa.Column("outcome", sa.String(32), nullable=False),
            sa.Column("details_json", sa.Text, nullable=True),
            sa.Column("recorded_at", ts, nullable=False),
        )
        op.create_index("ix_dbx_deployed_obj", "databricks_deployed_objects", ["project_id", "model_key", "fqn"])
    if not insp.has_table("databricks_deployed_names"):
        op.create_table(
            "databricks_deployed_names",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("project_id", sa.String(255), nullable=False),
            sa.Column("model_key", sa.String(255), nullable=False, server_default=""),
            sa.Column("view_name", sa.String(255), nullable=True),
            sa.Column("view_source", sa.String(512), nullable=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("source_key_json", sa.Text, nullable=False),
            sa.Column("target_name", sa.String(255), nullable=False),
            sa.Column("recorded_at", ts, nullable=False),
        )
        op.create_index("ix_dbx_deployed_name", "databricks_deployed_names", ["project_id", "model_key", "run_id"])
    if not insp.has_table("databricks_deploy_journal"):
        op.create_table(
            "databricks_deploy_journal",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("destination", sa.String(767), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("target_fqn", sa.String(767), nullable=False),
            sa.Column("statement_sha256", sa.String(64), nullable=False),
            sa.Column("definition_hash", sa.String(64), nullable=True),
            sa.Column("previous_hash", sa.String(64), nullable=True),
            sa.Column("state", sa.String(32), nullable=False),
            sa.Column("statement_id", sa.String(128), nullable=True),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("updated_at", ts, nullable=False),
        )
        op.create_index("ix_dbx_journal_run", "databricks_deploy_journal", ["run_id", "seq"], unique=True)
        op.create_index("ix_dbx_journal_dest", "databricks_deploy_journal", ["destination", "state"])
    if not insp.has_table("databricks_deploy_leases"):
        op.create_table(
            "databricks_deploy_leases",
            sa.Column("lease_key", sa.String(767), primary_key=True),
            sa.Column("holder_run_id", sa.String(64), nullable=False),
            sa.Column("acquired_at", ts, nullable=False),
            sa.Column("expires_at", ts, nullable=False),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for name in reversed(TABLES):
        if insp.has_table(name):
            op.drop_table(name)
