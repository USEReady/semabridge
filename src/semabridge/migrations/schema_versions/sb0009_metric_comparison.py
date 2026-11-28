"""Metric Comparison configs and run history tables.

Revision ID: sb0009_metric_comparison
Revises: sb0008_bi_truth_critical
Create Date: 2026-11-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0009_metric_comparison"
down_revision = "sb0008_bi_truth_critical"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    insp = _inspector()

    # 1. Create metric_comparison_configs
    if not insp.has_table("metric_comparison_configs"):
        op.create_table(
            "metric_comparison_configs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), nullable=False, index=True),
            sa.Column("metric_name", sa.String(255), nullable=True),
            sa.Column("tolerance_pct", sa.Float, nullable=False, server_default="0.01"),
            sa.Column("absolute_floor", sa.Float, nullable=False, server_default="0.01"),
            sa.Column("null_policy", sa.String(64), nullable=False, server_default="STRICT_NULL"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "ix_metric_cmp_cfg_proj_metric",
            "metric_comparison_configs",
            ["project_id", "metric_name"],
            unique=True,
        )

    # 2. Create metric_comparison_runs
    if not insp.has_table("metric_comparison_runs"):
        op.create_table(
            "metric_comparison_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), nullable=False, index=True),
            sa.Column("metric_name", sa.String(255), nullable=False),
            sa.Column("source_platform", sa.String(64), nullable=False),
            sa.Column("target_platform", sa.String(64), nullable=False),
            sa.Column("target_view", sa.String(512), nullable=False, server_default=""),
            sa.Column("source_query", sa.Text, nullable=False, server_default=""),
            sa.Column("target_query", sa.Text, nullable=False, server_default=""),
            sa.Column("source_value", sa.Text, nullable=True),
            sa.Column("target_value", sa.Text, nullable=True),
            sa.Column("variance_pct", sa.Float, nullable=True),
            sa.Column("absolute_difference", sa.Float, nullable=True),
            sa.Column("tolerance_pct", sa.Float, nullable=False, server_default="0.01"),
            sa.Column("verdict", sa.String(32), nullable=False, server_default="NOT_COMPARABLE"),
            sa.Column("reason", sa.Text, nullable=False, server_default=""),
            sa.Column("execution_ms", sa.Float, nullable=False, server_default="0.0"),
            sa.Column("mock_mode", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "ix_metric_cmp_run_metric",
            "metric_comparison_runs",
            ["project_id", "metric_name"],
        )


def downgrade() -> None:
    insp = _inspector()
    if insp.has_table("metric_comparison_runs"):
        op.drop_table("metric_comparison_runs")
    if insp.has_table("metric_comparison_configs"):
        op.drop_table("metric_comparison_configs")
