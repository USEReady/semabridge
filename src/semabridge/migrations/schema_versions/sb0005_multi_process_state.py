"""Multi-process state: Postgres is the authority, memory only a cache.

* ``projects``: ``owner_user_id``, ``status``, ``sync_mode``, ``folder_id``
  (project metadata that drives decisions, previously only in the per-process
  compat store) and ``deleted_at`` (soft delete: a deleted project keeps its
  row as a tombstone, so every server process excludes it -- previously a
  per-process ``deleted_project_ids`` set).
* ``drift_events``: the metric drift events of governance/drift_service
  (previously ``~/.semabridge/drift_state.json`` on each host). The old JSON
  events are imported on first use when the table is empty.

Data: the compat-store metadata of existing projects and its deleted-project
ids are written into ``projects`` by the application on its next start
(api/services/project_shared._compat_sync_store_meta_to_db). Additive and
idempotent; nothing is deleted.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0005_multi_process_state"
down_revision = "sb0004_run_reliability"
branch_labels = None
depends_on = None

_PROJECT_COLUMNS = [
    ("owner_user_id", lambda: sa.Column("owner_user_id", sa.String(255), nullable=True)),
    ("status", lambda: sa.Column("status", sa.String(32), nullable=True)),
    ("sync_mode", lambda: sa.Column("sync_mode", sa.String(20), nullable=True)),
    ("folder_id", lambda: sa.Column("folder_id", sa.String(255), nullable=True)),
    ("deleted_at", lambda: sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)),
]


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    existing_cols = {c["name"] for c in _inspector().get_columns("projects")}
    for name, factory in _PROJECT_COLUMNS:
        if name not in existing_cols:
            op.add_column("projects", factory())

    if not _inspector().has_table("drift_events"):
        op.create_table(
            "drift_events",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("project_id", sa.String(255), nullable=True),
            sa.Column("status", sa.String(32), nullable=True),
            sa.Column("position", sa.Integer, nullable=False, server_default="0"),
            sa.Column("payload_json", sa.Text, nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_drift_events_project", "drift_events", ["project_id"])
        op.create_index("ix_drift_events_position", "drift_events", ["position"])


def downgrade() -> None:
    op.drop_index("ix_drift_events_position", table_name="drift_events")
    op.drop_index("ix_drift_events_project", table_name="drift_events")
    op.drop_table("drift_events")
    for name, _ in reversed(_PROJECT_COLUMNS):
        op.drop_column("projects", name)
