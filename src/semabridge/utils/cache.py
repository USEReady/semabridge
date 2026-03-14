"""
Metadata caching for incremental processing.

Enables fast re-runs by only processing changed tables.
Uses the shared SQLAlchemy database layer (configured via
``SEMABRIDGE_DATABASE_URL``) instead of a separate DuckDB file, so the
cache works identically on SQLite, DuckDB, PostgreSQL, and MySQL.

Key features:
1. Hash-based change detection (not just timestamps).
2. Atomic writes via SQLAlchemy session transactions.
3. Schema versioning for cache invalidation (managed by Alembic).
4. No ``duckdb.connect()`` — completely dialect-agnostic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.cache_models import (
    CacheMeta,
    SyncHistoryCache,
    TableHash,
)
from semabridge.repository.orm.compat import upsert
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

#: Bump this version when the cache schema changes.
#: On version mismatch the cache is wiped and rebuilt.
CACHE_SCHEMA_VERSION = 2


def _get_session() -> Session:
    """Return a fresh SQLAlchemy session from the shared DatabaseManager."""
    from semabridge.repository.orm.session_factory import db_manager
    return db_manager.get_session_factory()()


class MetadataCache:
    """ORM-based cache for incremental metadata processing.

    Tracks:
    - Table metadata hashes for change detection.
    - Last sync timestamps.
    - Extraction state for resume capability.

    All persistence goes through the shared ``DatabaseManager``
    (configured via ``SEMABRIDGE_DATABASE_URL``), eliminating the
    separate ``.semabridge_cache/`` DuckDB file.

    The ``cache_dir`` parameter is accepted for backward compatibility
    but is no longer used to locate a DuckDB file.
    """

    def __init__(self, cache_dir: str | Path = ".semabridge_cache") -> None:
        """Initialise the cache and ensure schema tables exist.

        Args:
            cache_dir: (Deprecated) Legacy path for the DuckDB cache file.
                       Accepted for backward compatibility but ignored.
        """
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create cache tables and validate schema version."""
        from semabridge.repository.orm.session_factory import db_manager

        engine = db_manager.get_engine()
        # Create cache tables if they don't exist yet.
        Base.metadata.create_all(engine, tables=[
            CacheMeta.__table__,
            TableHash.__table__,
            SyncHistoryCache.__table__,
        ])

        session = _get_session()
        try:
            row = session.execute(
                select(CacheMeta).where(CacheMeta.key == "schema_version")
            ).scalar_one_or_none()

            current_version = int(row.value) if row and row.value else 0

            if current_version < CACHE_SCHEMA_VERSION:
                logger.info(
                    "Cache schema upgrade: %d -> %d — clearing stale data",
                    current_version, CACHE_SCHEMA_VERSION,
                )
                session.execute(delete(TableHash))
                session.execute(delete(SyncHistoryCache))

                # Upsert the version sentinel
                existing = session.execute(
                    select(CacheMeta).where(CacheMeta.key == "schema_version")
                ).scalar_one_or_none()
                if existing is None:
                    session.add(
                        CacheMeta(key="schema_version", value=str(CACHE_SCHEMA_VERSION))
                    )
                else:
                    existing.value = str(CACHE_SCHEMA_VERSION)

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Table hash API
    # ------------------------------------------------------------------

    def get_table_hash(
        self,
        database: str,
        schema: str,
        table_name: str,
    ) -> Optional[str]:
        """Return the cached column hash for a table, or ``None``."""
        session = _get_session()
        try:
            row = session.execute(
                select(TableHash).where(
                    TableHash.database == database.upper(),
                    TableHash.schema_name == schema.upper(),
                    TableHash.table_name == table_name.upper(),
                )
            ).scalar_one_or_none()
            return row.column_hash if row else None
        finally:
            session.close()

    def set_table_hash(
        self,
        database: str,
        schema: str,
        table_name: str,
        column_hash: str,
        row_count: int = 0,
        last_altered: Optional[str] = None,
    ) -> None:
        """Store (insert or update) the hash for a single table.

        Uses the dialect-aware :func:`~semabridge.repository.orm.compat.upsert`
        helper so ``INSERT OR REPLACE`` (SQLite/DuckDB syntax) is never
        written directly.
        """
        session = _get_session()
        try:
            upsert(
                session,
                TableHash,
                rows=[{
                    "database": database.upper(),
                    "schema_name": schema.upper(),
                    "table_name": table_name.upper(),
                    "column_hash": column_hash,
                    "row_count": row_count,
                    "last_altered": last_altered,
                    "cached_at": datetime.now(timezone.utc),
                }],
                index_elements=["database", "schema_name", "table_name"],
                update_columns=["column_hash", "row_count", "last_altered", "cached_at"],
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_changed_tables(
        self,
        current_tables: dict[str, dict[str, Any]],
        database: str,
        schema: str,
    ) -> tuple[list[str], list[str], list[str]]:
        """Detect changed, added, and removed tables.

        Args:
            current_tables: Dict of ``table_name → {columns, row_count, …}``.
            database:       Database name (matched case-insensitively).
            schema:         Schema name (matched case-insensitively).

        Returns:
            Tuple ``(changed, added, removed)`` — lists of table names.
        """
        session = _get_session()
        try:
            rows = session.execute(
                select(TableHash.table_name, TableHash.column_hash).where(
                    TableHash.database == database.upper(),
                    TableHash.schema_name == schema.upper(),
                )
            ).all()
            cached: dict[str, str] = {r.table_name: r.column_hash for r in rows}
        finally:
            session.close()

        current_names = {t.upper() for t in current_tables.keys()}
        cached_names = set(cached.keys())

        added = list(current_names - cached_names)
        removed = list(cached_names - current_names)
        changed: list[str] = []

        for table_name, metadata in current_tables.items():
            table_upper = table_name.upper()
            if table_upper in cached:
                current_hash = self._compute_hash(metadata.get("columns", []))
                if current_hash != cached[table_upper]:
                    changed.append(table_name)

        return changed, added, removed

    @staticmethod
    def _compute_hash(columns: list[dict[str, Any]]) -> str:
        """Compute a deterministic hash of column metadata."""
        sorted_cols = sorted(columns, key=lambda c: c.get("name", ""))
        content = json.dumps(sorted_cols, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Sync history API
    # ------------------------------------------------------------------

    def record_sync(
        self,
        sync_id: str,
        status: str,
        tables_processed: int,
        tables_changed: int,
        model_name: str,
        details: Optional[dict] = None,
    ) -> None:
        """Record a sync operation in history."""
        now = datetime.now(timezone.utc)
        session = _get_session()
        try:
            record = SyncHistoryCache(
                sync_id=sync_id,
                started_at=now,
                completed_at=now,
                status=status,
                tables_processed=tables_processed,
                tables_changed=tables_changed,
                model_name=model_name,
                details=json.dumps(details) if details else None,
            )
            session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_last_sync(self, model_name: Optional[str] = None) -> Optional[dict]:
        """Return the most recent sync record as a plain dict."""
        session = _get_session()
        try:
            stmt = (
                select(SyncHistoryCache)
                .order_by(SyncHistoryCache.completed_at.desc())
                .limit(1)
            )
            if model_name:
                stmt = stmt.where(SyncHistoryCache.model_name == model_name)

            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            return {
                "sync_id": row.sync_id,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "status": row.status,
                "tables_processed": row.tables_processed,
                "tables_changed": row.tables_changed,
                "model_name": row.model_name,
                "details": json.loads(row.details) if row.details else None,
            }
        finally:
            session.close()

    def clear(self) -> None:
        """Clear all cached data (hashes and sync history)."""
        session = _get_session()
        try:
            session.execute(delete(TableHash))
            session.execute(delete(SyncHistoryCache))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        logger.info("Metadata cache cleared")

