"""Databricks deploy intents (Phase 3.8b): stored previews that a confirmation
executes, at most once. Additive and idempotent."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0009_databricks_deploy_intents"
down_revision = "sb0008_databricks_deploy"
branch_labels = None
depends_on = None

TABLE = "databricks_deploy_intents"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    ts = sa.DateTime(timezone=True)
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("intent_id", sa.String(64), nullable=False, unique=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("model_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("plan_revision", sa.BigInteger, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("intent_json", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("expires_at", ts, nullable=False),
        sa.Column("used_at", ts, nullable=True),
        sa.Column("used_by", sa.String(255), nullable=True),
    )
    op.create_index("ix_dbx_intent_project", TABLE, ["project_id", "model_key", "status"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)
