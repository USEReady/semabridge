"""add semantic routing tables

Revision ID: d9f3a1b7c2e4
Revises: c4d29f9ab112
Create Date: 2026-04-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9f3a1b7c2e4"
down_revision: str | Sequence[str] | None = "c4d29f9ab112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
    return index_name in existing


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "router_decisions"):
        op.create_table(
            "router_decisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("table_name", sa.String(length=255), nullable=False),
            sa.Column("category", sa.String(length=20), nullable=False),
            sa.Column("confidence", sa.String(length=20), nullable=False),
            sa.Column("reason_code", sa.String(length=100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_index(inspector, "router_decisions", "idx_router_decisions_model_table"):
        op.create_index(
            "idx_router_decisions_model_table",
            "router_decisions",
            ["model_name", "table_name"],
            unique=True,
        )

    if not _has_table(inspector, "measure_anchor_decisions"):
        op.create_table(
            "measure_anchor_decisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("measure_name", sa.String(length=255), nullable=False),
            sa.Column("anchor_table", sa.String(length=255), nullable=True),
            sa.Column("confidence", sa.String(length=20), nullable=False),
            sa.Column("is_ambiguous", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reason_code", sa.String(length=100), nullable=False),
            sa.Column("candidate_facts", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_index(inspector, "measure_anchor_decisions", "idx_measure_anchor_decisions_model_measure"):
        op.create_index(
            "idx_measure_anchor_decisions_model_measure",
            "measure_anchor_decisions",
            ["model_name", "measure_name"],
            unique=True,
        )

    if not _has_table(inspector, "fact_deployment_outcomes"):
        op.create_table(
            "fact_deployment_outcomes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("fact_table", sa.String(length=255), nullable=False),
            sa.Column("artifact_name", sa.String(length=255), nullable=False),
            sa.Column("deployment_status", sa.String(length=20), nullable=False),
            sa.Column("skipped_reason", sa.String(length=100), nullable=True),
            sa.Column("measure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("dimension_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("traversal_trace", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_index(inspector, "fact_deployment_outcomes", "idx_fact_deployment_outcomes_model_fact"):
        op.create_index(
            "idx_fact_deployment_outcomes_model_fact",
            "fact_deployment_outcomes",
            ["model_name", "fact_table"],
            unique=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "fact_deployment_outcomes"):
        if _has_index(inspector, "fact_deployment_outcomes", "idx_fact_deployment_outcomes_model_fact"):
            op.drop_index("idx_fact_deployment_outcomes_model_fact", table_name="fact_deployment_outcomes")
        op.drop_table("fact_deployment_outcomes")

    if _has_table(inspector, "measure_anchor_decisions"):
        if _has_index(inspector, "measure_anchor_decisions", "idx_measure_anchor_decisions_model_measure"):
            op.drop_index("idx_measure_anchor_decisions_model_measure", table_name="measure_anchor_decisions")
        op.drop_table("measure_anchor_decisions")

    if _has_table(inspector, "router_decisions"):
        if _has_index(inspector, "router_decisions", "idx_router_decisions_model_table"):
            op.drop_index("idx_router_decisions_model_table", table_name="router_decisions")
        op.drop_table("router_decisions")
