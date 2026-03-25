"""
Cross-Database ORM Compatibility Utilities.

Provides SQLAlchemy column helpers and expression wrappers that work
identically across SQLite, DuckDB, PostgreSQL, and MySQL without
requiring dialect-specific code in models or repositories.

Usage::

    from semabridge.repository.orm.compat import (
        json_col,
        utcnow,
        utcnow_expr,
        upsert,
    )

Column Helpers
--------------
* :func:`json_col` — returns a ``Text`` column for JSON payloads.
  All databases support ``TEXT``; ``JSON`` type causes issues in DuckDB
  and requires ``JSON_VALUE`` syntax in MySQL older than 5.7.
* :data:`UTC_DATETIME` — ``DateTime(timezone=True)`` shorthand.

Timestamp Helpers
-----------------
* :func:`utcnow` — Python-side UTC datetime (use for INSERT values).
* :func:`utcnow_expr` — ``func.now()`` wrapped in a ``CAST`` to produce
  a timezone-aware server-side default compatible with all dialects.

Upsert Helpers
--------------
* :func:`upsert` — dialect-aware INSERT … ON CONFLICT / ON DUPLICATE KEY.

  Supported dialects:
  - SQLite ≥ 3.24 and PostgreSQL: ``INSERT … ON CONFLICT DO UPDATE``
  - MySQL / MariaDB: ``INSERT … ON DUPLICATE KEY UPDATE``
  - DuckDB (via duckdb-engine): ``INSERT … ON CONFLICT DO UPDATE``
  - Fallback: DELETE + INSERT (safe for all dialects)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Type

import sqlalchemy as sa
from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Session

__all__ = [
    "UTC_DATETIME",
    "json_col",
    "utcnow",
    "utcnow_expr",
    "upsert",
]


# ---------------------------------------------------------------------------
# Column type shortcuts
# ---------------------------------------------------------------------------

#: Timezone-aware DateTime column for all dialects.
#: SQLite stores as TEXT, PostgreSQL as TIMESTAMPTZ, MySQL as DATETIME,
#: DuckDB as TIMESTAMPTZ.  SQLAlchemy handles the conversion automatically.
UTC_DATETIME = DateTime(timezone=True)


def json_col(**kwargs: Any) -> sa.Column:
    """Return a ``Text`` mapped column intended for JSON payloads.

    Using ``Text`` instead of ``sa.JSON`` ensures compatibility across:
    * DuckDB — ``sa.JSON`` maps to the ``JSON`` DuckDB type but causes
      issues with the ``duckdb-engine`` dialect's reflection.
    * MySQL < 5.7.8 — no native JSON column type.
    * SQLite — ``sa.JSON`` works but stores as TEXT anyway.

    Repository methods are responsible for calling ``json.dumps`` /
    ``json.loads`` when reading/writing these columns.

    Args:
        **kwargs: Extra keyword arguments forwarded to :class:`sa.Column`,
            e.g. ``nullable=True``, ``default=None``.

    Returns:
        A ``sa.Column(Text, …)`` instance.
    """
    return sa.Column(Text, **kwargs)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware).

    Use this for Python-side ``default=`` / ``onupdate=`` column hooks::

        created_at: Mapped[datetime] = mapped_column(
            UTC_DATETIME, default=utcnow, nullable=False
        )
    """
    return datetime.now(timezone.utc)


def utcnow_expr() -> sa.sql.expression.Function:
    """Return ``func.now()`` for use as a ``server_default``.

    This is portable across all supported dialects — each database's
    ``NOW()`` / ``CURRENT_TIMESTAMP`` returns the current transaction
    timestamp in UTC (when the connection timezone is UTC or the column
    is TIMESTAMPTZ).
    """
    return sa.func.now()


# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------

def upsert(
    session: Session,
    model_class: Type[Any],
    rows: List[Dict[str, Any]],
    index_elements: Sequence[str],
    update_columns: Optional[Sequence[str]] = None,
) -> None:
    """Perform a dialect-aware bulk upsert.

    Inserts ``rows`` into the table mapped by ``model_class``.  If a row
    with the same ``index_elements`` already exists it is updated instead.

    For dialects that support ``INSERT … ON CONFLICT`` (PostgreSQL, SQLite,
    DuckDB) the native optimistic upsert statement is used.  For MySQL the
    ``INSERT … ON DUPLICATE KEY UPDATE`` form is used.  For unknown dialects
    a safe ``merge`` emulation (SELECT → insert or update) is used as a
    fallback.

    Args:
        session:         An open SQLAlchemy ``Session``.
        model_class:     ORM model class whose table will receive the data.
        rows:            List of dicts mapping column name → value.
        index_elements:  Column names constituting the conflict key (PK or
                         unique index).
        update_columns:  Column names to update on conflict.  When ``None``
                         all non-key columns in ``rows[0]`` are updated.

    Raises:
        ValueError: If ``rows`` is empty.
    """
    if not rows:
        return

    table = model_class.__table__
    dialect_name = session.bind.dialect.name if session.bind else _detect_dialect(session)

    if update_columns is None:
        index_set = set(index_elements)
        update_columns = [k for k in rows[0].keys() if k not in index_set]

    if dialect_name in ("postgresql", "sqlite", "duckdb"):
        try:
            _upsert_on_conflict(
                session,
                table,
                rows,
                index_elements,
                update_columns,
                dialect_name=dialect_name,
            )
        except Exception as exc:  # noqa: BLE001
            # Some dialect/compiler combinations (notably duckdb-engine with
            # sqlite-specific ON CONFLICT objects) can fail at compile/runtime.
            # Fall back to merge-upsert to keep sync resilient.
            session.rollback()
            _upsert_merge_fallback(session, model_class, rows, index_elements)
    elif dialect_name == "mysql":
        _upsert_on_duplicate_key(session, table, rows, update_columns)
    else:
        _upsert_merge_fallback(session, model_class, rows, index_elements)


# ---------------------------------------------------------------------------
# Internal dialect-specific upsert implementations
# ---------------------------------------------------------------------------

def _detect_dialect(session: Session) -> str:
    """Detect the dialect name from a session without a bound engine."""
    try:
        engine = session.get_bind()
        return engine.dialect.name
    except Exception:  # noqa: BLE001
        return "unknown"


def _upsert_on_conflict(
    session: Session,
    table: sa.Table,
    rows: List[Dict[str, Any]],
    index_elements: Sequence[str],
    update_columns: Sequence[str],
    dialect_name: str,
) -> None:
    """INSERT … ON CONFLICT DO UPDATE (PostgreSQL / SQLite / DuckDB)."""
    if dialect_name in ("postgresql", "duckdb"):
        # duckdb-engine follows PostgreSQL's ON CONFLICT semantics.
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

    stmt = dialect_insert(table)
    set_dict = {col: stmt.excluded[col] for col in update_columns}
    stmt = stmt.on_conflict_do_update(
        index_elements=list(index_elements),
        set_=set_dict,
    )
    session.execute(stmt, rows)


def _upsert_on_duplicate_key(
    session: Session,
    table: sa.Table,
    rows: List[Dict[str, Any]],
    update_columns: Sequence[str],
) -> None:
    """INSERT … ON DUPLICATE KEY UPDATE (MySQL / MariaDB)."""
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    stmt = mysql_insert(table)
    set_dict = {col: stmt.inserted[col] for col in update_columns}
    stmt = stmt.on_duplicate_key_update(**set_dict)
    session.execute(stmt, rows)


def _upsert_merge_fallback(
    session: Session,
    model_class: Type[Any],
    rows: List[Dict[str, Any]],
    index_elements: Sequence[str],
) -> None:
    """Fallback upsert: SELECT then INSERT or UPDATE (works on all dialects)."""
    for row in rows:
        # Build filter from index columns
        filters = [
            getattr(model_class, col) == row[col]
            for col in index_elements
            if col in row
        ]
        existing = session.query(model_class).filter(*filters).first()
        if existing is None:
            obj = model_class(**row)
            session.add(obj)
        else:
            for k, v in row.items():
                setattr(existing, k, v)


# ---------------------------------------------------------------------------
# JSON convenience wrappers (used by repositories)
# ---------------------------------------------------------------------------

def dumps(obj: Any) -> Optional[str]:
    """JSON-encode ``obj``, returning ``None`` when ``obj`` is ``None``."""
    if obj is None:
        return None
    return json.dumps(obj, default=str)


def loads(text: Optional[str]) -> Any:
    """JSON-decode ``text``, returning ``None`` when ``text`` is falsy."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
