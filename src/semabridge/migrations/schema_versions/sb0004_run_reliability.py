"""Run execution reliability: one run identity, heartbeats, leases, deploy journal.

* ``runs``: ``run_kind`` ("project" = the run a user/schedule started, the
  row the UI lists; "model" = one engine row per model), ``parent_run_id``
  (model row -> project run), ``heartbeat_at`` (liveness; a running run whose
  heartbeat is older than the lease is "interrupted"), ``schedule_label``,
  ``message``, ``meta_json`` (run-level fields of the project run),
  ``retry_of_run_id``, ``worker_id``.
* ``run_leases``: DB-backed mutual exclusion, valid across processes --
  ``project:<id>`` (one sync of a project at a time) and
  ``target:<db>.<schema>.<object>`` (objects a deploy writes). A lease
  expires unless its run keeps heartbeating, so a crashed holder never
  blocks forever.
* ``run_deploy_events``: every Snowflake object a deploy created, promoted,
  dropped or cleaned up, per run (audit + cleanup).

Data: the compat-store runs (``run-<ms>`` ids) are imported as project rows
and their engine rows linked (repository/maintenance/run_identity_backfill).
Additive and idempotent; nothing is deleted. Back up ``runs`` first.
"""
from __future__ import annotations

import logging
import sqlalchemy as sa
from alembic import op

revision = "sb0004_run_reliability"
down_revision = "sb0003_tz_backfill"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.sb0004")

_RUN_COLUMNS = [
    ("run_kind", lambda: sa.Column("run_kind", sa.String(20), nullable=False, server_default="model")),
    ("parent_run_id", lambda: sa.Column("parent_run_id", sa.String(64), nullable=True)),
    ("heartbeat_at", lambda: sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)),
    ("schedule_label", lambda: sa.Column("schedule_label", sa.String(50), nullable=True)),
    ("message", lambda: sa.Column("message", sa.Text, nullable=True)),
    ("meta_json", lambda: sa.Column("meta_json", sa.Text, nullable=True)),
    ("retry_of_run_id", lambda: sa.Column("retry_of_run_id", sa.String(64), nullable=True)),
    ("worker_id", lambda: sa.Column("worker_id", sa.String(128), nullable=True)),
]


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    existing_cols = {c["name"] for c in _inspector().get_columns("runs")}
    for name, factory in _RUN_COLUMNS:
        if name not in existing_cols:
            op.add_column("runs", factory())
    existing_idx = {i["name"] for i in _inspector().get_indexes("runs")}
    if "ix_runs_parent" not in existing_idx:
        op.create_index("ix_runs_parent", "runs", ["parent_run_id"])
    if "ix_runs_kind_status" not in existing_idx:
        op.create_index("ix_runs_kind_status", "runs", ["run_kind", "status"])

    if not _inspector().has_table("run_leases"):
        op.create_table(
            "run_leases",
            sa.Column("lease_key", sa.String(512), primary_key=True),
            sa.Column("lease_type", sa.String(20), nullable=False),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("holder", sa.String(255), nullable=False),
            sa.Column("project_id", sa.String(255), nullable=True),
            sa.Column("acquired_at", ts, nullable=False),
            sa.Column("heartbeat_at", ts, nullable=False),
            sa.Column("expires_at", ts, nullable=False),
        )
        op.create_index("ix_run_leases_run", "run_leases", ["run_id"])
    if not _inspector().has_table("run_deploy_events"):
        op.create_table(
            "run_deploy_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(64), nullable=True),
            sa.Column("project_run_id", sa.String(64), nullable=True),
            sa.Column("project_id", sa.String(255), nullable=True),
            sa.Column("model", sa.Text, nullable=True),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("object_type", sa.String(40), nullable=True),
            sa.Column("object_name", sa.Text, nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("detail", sa.Text, nullable=True),
            sa.Column("created_at", ts, nullable=False),
        )
        op.create_index("ix_run_deploy_events_run", "run_deploy_events", ["run_id"])
        op.create_index("ix_run_deploy_events_project_run", "run_deploy_events", ["project_run_id"])

    _backfill()


def _backfill() -> None:
    from semabridge.repository.maintenance.run_identity_backfill import backfill_run_identity, load_store_runs

    store_path = None
    try:
        from semabridge.api.services.project_shared import _compat_store_path

        store_path = _compat_store_path()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sb0004: compat store path unavailable (%s); no runs imported", exc)
    report = backfill_run_identity(op.get_bind(), load_store_runs(store_path))
    logger.warning("sb0004 run identity backfill: %s", report)


def downgrade() -> None:
    op.drop_table("run_deploy_events")
    op.drop_table("run_leases")
    op.drop_index("ix_runs_kind_status", table_name="runs")
    op.drop_index("ix_runs_parent", table_name="runs")
    for name, _ in reversed(_RUN_COLUMNS):
        op.drop_column("runs", name)
