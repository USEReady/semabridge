"""add parsed semantic payloads

Revision ID: 2f7c9e8b1a4d
Revises: b7c6d5e4f3a2, f6a7b8c9d0e1
Create Date: 2026-05-26 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2f7c9e8b1a4d"
down_revision: str | Sequence[str] | None = ("b7c6d5e4f3a2", "f6a7b8c9d0e1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
    return index_name in existing


def _payload_type(bind: sa.engine.Connection) -> sa.types.TypeEngine[Any]:
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB()
    return sa.Text()


def upgrade() -> None:
    """Create the parsed semantic payload table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "parsed_semantic_payloads"):
        op.create_table(
            "parsed_semantic_payloads",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("version", sa.String(length=100), nullable=True),
            sa.Column("source_platform", sa.String(length=50), nullable=True),
            sa.Column("target_platform", sa.String(length=50), nullable=True),
            sa.Column("payload_jsonb", _payload_type(bind), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_index(inspector, "parsed_semantic_payloads", "ix_parsed_semantic_payloads_model_name"):
        op.create_index(
            "ix_parsed_semantic_payloads_model_name",
            "parsed_semantic_payloads",
            ["model_name"],
            unique=False,
        )


def downgrade() -> None:
    """Drop the parsed semantic payload table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "parsed_semantic_payloads"):
        if _has_index(inspector, "parsed_semantic_payloads", "ix_parsed_semantic_payloads_model_name"):
            op.drop_index("ix_parsed_semantic_payloads_model_name", table_name="parsed_semantic_payloads")
        op.drop_table("parsed_semantic_payloads")
