"""Lightweight schema compatibility fixes for existing repository databases."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_ID_LENGTH = 255


def _column_length(inspector: Any, table_name: str, column_name: str) -> int | None:
    for column in inspector.get_columns(table_name):
        if column.get("name") != column_name:
            continue
        column_type = column.get("type")
        return getattr(column_type, "length", None)
    return None


def widen_project_id_columns(conn: Any) -> list[str]:
    """Widen semantic project identifiers on databases that enforce VARCHAR(n).

    Early ORM/Alembic schemas modeled ``project_id`` as a UUID-sized value, but
    runtime Snowflake/Fabric model identifiers are semantic names such as
    ``COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC``. PostgreSQL enforces the old
    ``VARCHAR(36)`` limit, so existing deployments need an idempotent widen.
    """
    dialect = conn.dialect.name
    if dialect != "postgresql":
        return []

    inspector = inspect(conn)
    table_names = set(inspector.get_table_names())
    candidates = [
        ("projects", "project_id"),
        ("runs", "project_id"),
        ("snapshots", "project_id"),
        ("retention_policies", "project_id"),
        ("command_log", "project_id"),
    ]
    needs_widen = {
        (table_name, column_name)
        for table_name, column_name in candidates
        if table_name in table_names
        and (current_length := _column_length(inspector, table_name, column_name)) is not None
        and current_length < PROJECT_ID_LENGTH
    }
    if not needs_widen:
        return []

    preparer = conn.dialect.identifier_preparer
    dropped_fks: list[dict[str, Any]] = []
    for table_name in ("runs", "snapshots", "retention_policies"):
        if table_name not in table_names:
            continue
        for fk in inspector.get_foreign_keys(table_name):
            if (
                fk.get("name")
                and fk.get("constrained_columns") == ["project_id"]
                and fk.get("referred_table") == "projects"
                and fk.get("referred_columns") == ["project_id"]
            ):
                conn.exec_driver_sql(
                    f"ALTER TABLE {preparer.quote(table_name)} "
                    f"DROP CONSTRAINT {preparer.quote(fk['name'])}"
                )
                dropped_fks.append({"table_name": table_name, **fk})

    applied: list[str] = []
    try:
        for table_name, column_name in candidates:
            if (table_name, column_name) not in needs_widen:
                continue
            ddl = (
                f"ALTER TABLE {preparer.quote(table_name)} "
                f"ALTER COLUMN {preparer.quote(column_name)} TYPE VARCHAR({PROJECT_ID_LENGTH})"
            )
            conn.exec_driver_sql(ddl)
            applied.append(f"{table_name}.{column_name}")
    finally:
        for fk in dropped_fks:
            options = fk.get("options") or {}
            ondelete = options.get("ondelete")
            onupdate = options.get("onupdate")
            ddl = (
                f"ALTER TABLE {preparer.quote(fk['table_name'])} "
                f"ADD CONSTRAINT {preparer.quote(fk['name'])} "
                f"FOREIGN KEY ({preparer.quote('project_id')}) "
                f"REFERENCES {preparer.quote('projects')} ({preparer.quote('project_id')})"
            )
            if ondelete:
                ddl += f" ON DELETE {ondelete}"
            if onupdate:
                ddl += f" ON UPDATE {onupdate}"
            conn.exec_driver_sql(ddl)

    if applied:
        logger.info("Widened project_id columns to VARCHAR(%s): %s", PROJECT_ID_LENGTH, ", ".join(applied))
    return applied
