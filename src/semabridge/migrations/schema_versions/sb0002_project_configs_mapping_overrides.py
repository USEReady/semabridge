"""project_configs (versioned authoritative project config) and
mapping_overrides (the single home of user-chosen target names).

Idempotent: the tables may already exist when the ORM's create_all ran first.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0002_config_overrides"
down_revision = "sb0001_baseline"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    if not _has_table("project_configs"):
        op.create_table(
            "project_configs",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.String(255), nullable=False),
            sa.Column("version", sa.Integer, nullable=False),
            sa.Column("config_yaml", sa.Text, nullable=False),
            sa.Column("content_sha256", sa.String(64), nullable=True),
            sa.Column("created_at", ts, nullable=False),
            sa.Column("created_by", sa.String(255), nullable=True),
            sa.Column("reason", sa.String(64), nullable=True),
        )
        op.create_index("ix_project_configs_project_id", "project_configs", ["project_id"])
        op.create_index("ux_project_configs_project_version", "project_configs", ["project_id", "version"], unique=True)
    if not _has_table("mapping_overrides"):
        op.create_table(
            "mapping_overrides",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.String(255), nullable=False),
            sa.Column("model_key", sa.String(512), nullable=False, server_default=""),
            sa.Column("model_ref", sa.Text, nullable=True),
            sa.Column("source_path", sa.String(1024), nullable=False),
            sa.Column("target_name", sa.String(1024), nullable=False),
            sa.Column("entity_kind", sa.String(32), nullable=True),
            sa.Column("source_name", sa.String(1024), nullable=True),
            sa.Column("position", sa.Integer, nullable=True),
            sa.Column("updated_at", ts, nullable=False),
            sa.Column("updated_by", sa.String(255), nullable=True),
        )
        op.create_index("ix_mapping_overrides_project_id", "mapping_overrides", ["project_id"])
        op.create_index(
            "ux_mapping_overrides_identity", "mapping_overrides",
            ["project_id", "model_key", "source_path"], unique=True,
        )


def downgrade() -> None:
    op.drop_table("mapping_overrides")
    op.drop_table("project_configs")
