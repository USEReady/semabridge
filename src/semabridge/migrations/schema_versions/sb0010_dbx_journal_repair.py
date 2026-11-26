"""Repair: databricks_deploy_journal.definition_hash.

A development build of 3.8a could create the journal table (through the ORM's
create_all, before sb0008 ran) with a column named ``body_sha256``; sb0008 then
skipped the existing table. This renames that column to ``definition_hash``
(or adds it when neither exists). Idempotent; a no-op on correct databases.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0010_dbx_journal_repair"
down_revision = "sb0009_databricks_deploy_intents"
branch_labels = None
depends_on = None

TABLE = "databricks_deploy_journal"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(TABLE):
        return
    cols = {c["name"] for c in insp.get_columns(TABLE)}
    if "definition_hash" in cols:
        return
    with op.batch_alter_table(TABLE) as batch:
        if "body_sha256" in cols:
            batch.alter_column("body_sha256", new_column_name="definition_hash", existing_type=sa.String(64),
                               existing_nullable=True)
        else:
            batch.add_column(sa.Column("definition_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    pass   # a repair: nothing to undo
