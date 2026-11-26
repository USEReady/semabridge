"""plan_revision as BIGINT (deploy runs and deploy intents).

Plan revisions are millisecond timestamps (~1.8e12), beyond a 32-bit INTEGER;
Postgres refused them ("integer out of range"). sb0008/sb0009 now create the
columns as BIGINT; this widens databases created before that. Idempotent.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0011_dbx_revision_bigint"
down_revision = "sb0010_dbx_journal_repair"
branch_labels = None
depends_on = None

TABLES = ("databricks_deployments", "databricks_deploy_intents")


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in TABLES:
        if not insp.has_table(table):
            continue
        col = next((c for c in insp.get_columns(table) if c["name"] == "plan_revision"), None)
        if col is None or isinstance(col["type"], sa.BigInteger):
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column("plan_revision", type_=sa.BigInteger, existing_type=sa.Integer,
                               existing_nullable=False)


def downgrade() -> None:
    pass   # widening only: nothing to undo
