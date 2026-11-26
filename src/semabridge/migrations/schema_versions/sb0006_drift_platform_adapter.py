"""Drift monitoring platform adapter tables: drift_baselines, drift_last_checks, drift_schedules.

* ``drift_baselines``: canonical deployed view definitions and version history snapshots per view.
* ``drift_last_checks``: persistent last check execution status and metadata per project/view.
* ``drift_schedules``: persistent drift check schedules per project.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0006_drift_platform_adapter"
down_revision = "sb0005_multi_process_state"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    insp = _inspector()

    if not insp.has_table("drift_baselines"):
        op.create_table(
            "drift_baselines",
            sa.Column("id", sa.String(256), primary_key=True),
            sa.Column("project_id", sa.String(255), nullable=False),
            sa.Column("view_name", sa.String(256), nullable=False),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("payload_json", sa.Text, nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_drift_baselines_project", "drift_baselines", ["project_id"])
        op.create_index("ix_drift_baselines_view", "drift_baselines", ["view_name"])

    if not insp.has_table("drift_last_checks"):
        op.create_table(
            "drift_last_checks",
            sa.Column("id", sa.String(256), primary_key=True),
            sa.Column("project_id", sa.String(255), nullable=False),
            sa.Column("view_name", sa.String(256), nullable=True),
            sa.Column("payload_json", sa.Text, nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_drift_last_checks_project", "drift_last_checks", ["project_id"])

    if not insp.has_table("drift_schedules"):
        op.create_table(
            "drift_schedules",
            sa.Column("id", sa.String(256), primary_key=True),
            sa.Column("project_id", sa.String(255), nullable=False),
            sa.Column("payload_json", sa.Text, nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_drift_schedules_project", "drift_schedules", ["project_id"])


def downgrade() -> None:
    insp = _inspector()
    if insp.has_table("drift_schedules"):
        op.drop_index("ix_drift_schedules_project", table_name="drift_schedules")
        op.drop_table("drift_schedules")

    if insp.has_table("drift_last_checks"):
        op.drop_index("ix_drift_last_checks_project", table_name="drift_last_checks")
        op.drop_table("drift_last_checks")

    if insp.has_table("drift_baselines"):
        op.drop_index("ix_drift_baselines_view", table_name="drift_baselines")
        op.drop_index("ix_drift_baselines_project", table_name="drift_baselines")
        op.drop_table("drift_baselines")
