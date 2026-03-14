"""
ORM models for the SemaBridge metadata cache.

These tables replace the raw DuckDB DDL previously in
:class:`~semabridge.utils.cache.MetadataCache`.  Using SQLAlchemy models
means the cache automatically works with any configured database dialect
(SQLite, DuckDB, PostgreSQL, MySQL) without code changes.

Tables
------
* **CacheMeta** — key/value store for schema version and other cache metadata.
* **TableHash** — per-table column-metadata hashes for incremental processing.
* **SyncHistoryCache** — log of completed sync operations (for ``get_last_sync``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, func

from sqlalchemy.orm import Mapped, mapped_column

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.compat import UTC_DATETIME


class CacheMeta(Base):
    """Key/value metadata store for the incremental processing cache.

    Used to store the ``schema_version`` sentinel and any other cache-level
    flags.  Replaces the raw ``cache_meta`` DuckDB table.
    """

    __tablename__ = "cache_meta"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<CacheMeta(key={self.key!r}, value={self.value!r})>"


class TableHash(Base):
    """Column-metadata hash for a single database table.

    A new row is written (or updated) whenever a table is processed.
    Comparing the stored hash against a freshly-computed hash detects
    schema changes without querying the full table.

    The composite primary key ``(database, schema, table_name)`` mirrors
    the previous DuckDB PRIMARY KEY constraint.
    """

    __tablename__ = "cache_table_hashes"
    __table_args__ = (
        UniqueConstraint(
            "database", "schema_name", "table_name",
            name="uq_tablehash_database_schema_table",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    database: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_altered: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    cached_at: Mapped[Optional[datetime]] = mapped_column(
        UTC_DATETIME, server_default=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<TableHash(database={self.database!r}, schema={self.schema_name!r}, "
            f"table={self.table_name!r}, hash={self.column_hash!r})>"
        )


class SyncHistoryCache(Base):
    """Log of completed sync operations.

    Replaces the ``sync_history`` raw DuckDB table.  Each row describes
    one sync run: when it started, how many tables were processed, and
    whether it succeeded.
    """

    __tablename__ = "cache_sync_history"
    __table_args__ = (
        Index("ix_synchist_model_completed", "model_name", "completed_at"),
    )

    sync_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    tables_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tables_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SyncHistoryCache(sync_id={self.sync_id!r}, "
            f"status={self.status!r}, model={self.model_name!r})>"
        )
