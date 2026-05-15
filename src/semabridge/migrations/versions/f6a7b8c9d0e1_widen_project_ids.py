"""widen project identifiers

Revision ID: f6a7b8c9d0e1
Revises: d1e2f3g4h5i6
Create Date: 2026-05-15 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "d1e2f3g4h5i6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROJECT_ID_LENGTH = 255
LEGACY_PROJECT_ID_LENGTH = 36
PROJECT_FK_TABLES = ("runs", "snapshots", "retention_policies")


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if table_name not in set(inspector.get_table_names()):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _alter_project_id_length(table_name: str, column_name: str, length: int) -> None:
    op.alter_column(
        table_name,
        column_name,
        existing_type=sa.String(
            length=LEGACY_PROJECT_ID_LENGTH
            if length == PROJECT_ID_LENGTH
            else PROJECT_ID_LENGTH
        ),
        type_=sa.String(length=length),
        existing_nullable=(table_name == "command_log"),
    )


def _project_id_foreign_keys(inspector: sa.Inspector) -> list[dict[str, object]]:
    foreign_keys: list[dict[str, object]] = []
    for table_name in PROJECT_FK_TABLES:
        if table_name not in set(inspector.get_table_names()):
            continue
        for fk in inspector.get_foreign_keys(table_name):
            if (
                fk.get("name")
                and fk.get("constrained_columns") == ["project_id"]
                and fk.get("referred_table") == "projects"
                and fk.get("referred_columns") == ["project_id"]
            ):
                foreign_keys.append({"table_name": table_name, **fk})
    return foreign_keys


def _drop_project_id_foreign_keys(foreign_keys: list[dict[str, object]]) -> None:
    for fk in foreign_keys:
        op.drop_constraint(str(fk["name"]), str(fk["table_name"]), type_="foreignkey")


def _create_project_id_foreign_keys(foreign_keys: list[dict[str, object]]) -> None:
    for fk in foreign_keys:
        options = fk.get("options") or {}
        kwargs: dict[str, str] = {}
        if isinstance(options, dict):
            if options.get("ondelete"):
                kwargs["ondelete"] = str(options["ondelete"])
            if options.get("onupdate"):
                kwargs["onupdate"] = str(options["onupdate"])
        op.create_foreign_key(
            str(fk["name"]),
            str(fk["table_name"]),
            "projects",
            ["project_id"],
            ["project_id"],
            **kwargs,
        )


def upgrade() -> None:
    """Allow semantic model names longer than UUID-sized project ids."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    foreign_keys = _project_id_foreign_keys(inspector)

    _drop_project_id_foreign_keys(foreign_keys)
    try:
        for table_name, column_name in [
            ("projects", "project_id"),
            ("runs", "project_id"),
            ("snapshots", "project_id"),
            ("retention_policies", "project_id"),
            ("command_log", "project_id"),
        ]:
            if _has_column(inspector, table_name, column_name):
                _alter_project_id_length(table_name, column_name, PROJECT_ID_LENGTH)
    finally:
        _create_project_id_foreign_keys(foreign_keys)


def downgrade() -> None:
    """Restore legacy UUID-sized project id columns."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    foreign_keys = _project_id_foreign_keys(inspector)

    _drop_project_id_foreign_keys(foreign_keys)
    try:
        for table_name, column_name in [
            ("command_log", "project_id"),
            ("retention_policies", "project_id"),
            ("snapshots", "project_id"),
            ("runs", "project_id"),
            ("projects", "project_id"),
        ]:
            if _has_column(inspector, table_name, column_name):
                _alter_project_id_length(table_name, column_name, LEGACY_PROJECT_ID_LENGTH)
    finally:
        _create_project_id_foreign_keys(foreign_keys)
