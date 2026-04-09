"""Persistent duplicate-name mapping for deterministic sync naming.

Stores stable assignments for duplicate-normalized names (for example
PLANT_DESCRIPTION_1 / PLANT_DESCRIPTION_2) so subsequent sync runs do not
re-number differently and create drift.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from typing import Optional

try:
    import duckdb
except Exception:  # pragma: no cover - fallback path
    duckdb = None

from semabridge.core.db_resolver import get_default_db_path
from semabridge.repository.orm.session_factory import get_engine
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class DuplicateNameMappingRepository:
    """Repository for duplicate-name mappings across sync runs."""

    def __init__(self) -> None:
        # Primary runtime mapping store: in-memory Python dictionaries.
        # DB is used as fallback source of truth + persistence layer.
        self._cache_lock = threading.RLock()
        self._mapping_cache: dict[tuple[str, str, str, str, str], str] = {}
        self._used_names_cache: dict[tuple[str, str, str, str], set[str]] = {}

        self._use_duckdb = False
        self._duckdb_conn = None
        self._engine = None

        try:
            self._engine = get_engine()
            logger.info("Duplicate-name mappings using SQLAlchemy engine backend")
        except Exception as exc:
            if duckdb is None:
                raise
            self._use_duckdb = True
            db_path = get_default_db_path()
            self._duckdb_conn = duckdb.connect(db_path)
            logger.warning(
                "SQLAlchemy engine unavailable for duplicate mappings (%s); falling back to DuckDB local state: %s",
                exc,
                db_path,
            )

        self._ensure_table()

    def _ensure_table(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS duplicate_name_mappings (
            mapping_id VARCHAR(64) PRIMARY KEY,
            scope_type VARCHAR(32) NOT NULL,
            namespace_key VARCHAR(512) NOT NULL,
            dataset_key VARCHAR(512) NOT NULL,
            normalized_base VARCHAR(256) NOT NULL,
            source_name VARCHAR(512),
            source_signature VARCHAR(1024) NOT NULL,
            assigned_name VARCHAR(256) NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
        idx1 = (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_dup_map_signature "
            "ON duplicate_name_mappings "
            "(scope_type, namespace_key, dataset_key, normalized_base, source_signature)"
        )
        idx2 = (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_dup_map_assigned "
            "ON duplicate_name_mappings "
            "(scope_type, namespace_key, dataset_key, normalized_base, assigned_name)"
        )

        if self._use_duckdb and self._duckdb_conn is not None:
            self._duckdb_conn.execute(ddl)
            self._duckdb_conn.execute(idx1)
            self._duckdb_conn.execute(idx2)
            return

        from sqlalchemy import text

        with self._engine.begin() as conn:
            conn.execute(text(ddl))
            conn.execute(text(idx1))
            conn.execute(text(idx2))

    @staticmethod
    def _build_mapping_id(
        *,
        scope_type: str,
        namespace_key: str,
        dataset_key: str,
        normalized_base: str,
        source_signature: str,
    ) -> str:
        raw_key = (
            f"{scope_type}|{namespace_key}|{dataset_key}|"
            f"{normalized_base}|{source_signature}"
        )
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get_or_create_assigned_name(
        self,
        *,
        scope_type: str,
        namespace_key: str,
        dataset_key: str,
        normalized_base: str,
        source_name: str,
        source_signature: str,
        preferred_name: Optional[str] = None,
    ) -> str:
        """Return stable assigned duplicate name, creating mapping if missing."""
        base_key = (scope_type, namespace_key, dataset_key, normalized_base)
        signature_key = (
            scope_type,
            namespace_key,
            dataset_key,
            normalized_base,
            source_signature,
        )

        with self._cache_lock:
            cached = self._mapping_cache.get(signature_key)
            if cached:
                return cached

        now = datetime.now(timezone.utc)

        if self._use_duckdb and self._duckdb_conn is not None:
            assigned_name = self._get_or_create_assigned_name_duckdb(
                scope_type=scope_type,
                namespace_key=namespace_key,
                dataset_key=dataset_key,
                normalized_base=normalized_base,
                source_name=source_name,
                source_signature=source_signature,
                preferred_name=preferred_name,
                now=now,
            )
            with self._cache_lock:
                self._mapping_cache[signature_key] = assigned_name
                self._used_names_cache.setdefault(base_key, set()).add(assigned_name)
            return assigned_name

        from sqlalchemy import text

        with self._engine.begin() as conn:
            existing = conn.execute(
                text(
                    """
                    SELECT assigned_name
                    FROM duplicate_name_mappings
                    WHERE scope_type = :scope_type
                      AND namespace_key = :namespace_key
                      AND dataset_key = :dataset_key
                      AND normalized_base = :normalized_base
                      AND source_signature = :source_signature
                    LIMIT 1
                    """
                ),
                {
                    "scope_type": scope_type,
                    "namespace_key": namespace_key,
                    "dataset_key": dataset_key,
                    "normalized_base": normalized_base,
                    "source_signature": source_signature,
                },
            ).fetchone()
            if existing and existing[0]:
                assigned_name = str(existing[0])
                conn.execute(
                    text(
                        """
                        UPDATE duplicate_name_mappings
                        SET updated_at = :updated_at,
                            source_name = :source_name
                        WHERE scope_type = :scope_type
                          AND namespace_key = :namespace_key
                          AND dataset_key = :dataset_key
                          AND normalized_base = :normalized_base
                          AND source_signature = :source_signature
                        """
                    ),
                    {
                        "updated_at": now,
                        "source_name": source_name,
                        "scope_type": scope_type,
                        "namespace_key": namespace_key,
                        "dataset_key": dataset_key,
                        "normalized_base": normalized_base,
                        "source_signature": source_signature,
                    },
                )
                with self._cache_lock:
                    self._mapping_cache[signature_key] = assigned_name
                    self._used_names_cache.setdefault(base_key, set()).add(assigned_name)
                return assigned_name

            used_rows = conn.execute(
                text(
                    """
                    SELECT assigned_name
                    FROM duplicate_name_mappings
                    WHERE scope_type = :scope_type
                      AND namespace_key = :namespace_key
                      AND dataset_key = :dataset_key
                      AND normalized_base = :normalized_base
                    """
                ),
                {
                    "scope_type": scope_type,
                    "namespace_key": namespace_key,
                    "dataset_key": dataset_key,
                    "normalized_base": normalized_base,
                },
            ).fetchall()
            used_names = {str(r[0]) for r in used_rows if r and r[0]}
            with self._cache_lock:
                cache_used = self._used_names_cache.get(base_key)
                if cache_used:
                    used_names.update(cache_used)

            assigned_name = None
            if preferred_name and preferred_name not in used_names:
                assigned_name = preferred_name
            else:
                idx = 1
                while True:
                    candidate = f"{normalized_base}_{idx}"
                    if candidate not in used_names:
                        assigned_name = candidate
                        break
                    idx += 1

            mapping_id = self._build_mapping_id(
                scope_type=scope_type,
                namespace_key=namespace_key,
                dataset_key=dataset_key,
                normalized_base=normalized_base,
                source_signature=source_signature,
            )

            conn.execute(
                text(
                    """
                    INSERT INTO duplicate_name_mappings (
                        mapping_id,
                        scope_type,
                        namespace_key,
                        dataset_key,
                        normalized_base,
                        source_name,
                        source_signature,
                        assigned_name,
                        created_at,
                        updated_at
                    ) VALUES (
                        :mapping_id,
                        :scope_type,
                        :namespace_key,
                        :dataset_key,
                        :normalized_base,
                        :source_name,
                        :source_signature,
                        :assigned_name,
                        :created_at,
                        :updated_at
                    )
                    """
                ),
                {
                    "mapping_id": mapping_id,
                    "scope_type": scope_type,
                    "namespace_key": namespace_key,
                    "dataset_key": dataset_key,
                    "normalized_base": normalized_base,
                    "source_name": source_name,
                    "source_signature": source_signature,
                    "assigned_name": assigned_name,
                    "created_at": now,
                    "updated_at": now,
                },
            )

            logger.debug(
                "Stored duplicate name mapping: %s/%s/%s/%s -> %s",
                scope_type,
                namespace_key,
                dataset_key,
                source_name,
                assigned_name,
            )
            with self._cache_lock:
                self._mapping_cache[signature_key] = assigned_name
                self._used_names_cache.setdefault(base_key, set()).add(assigned_name)
            return assigned_name

    def _get_or_create_assigned_name_duckdb(
        self,
        *,
        scope_type: str,
        namespace_key: str,
        dataset_key: str,
        normalized_base: str,
        source_name: str,
        source_signature: str,
        preferred_name: Optional[str],
        now: datetime,
    ) -> str:
        existing = self._duckdb_conn.execute(
            """
            SELECT assigned_name
            FROM duplicate_name_mappings
            WHERE scope_type = ?
              AND namespace_key = ?
              AND dataset_key = ?
              AND normalized_base = ?
              AND source_signature = ?
            LIMIT 1
            """,
            [
                scope_type,
                namespace_key,
                dataset_key,
                normalized_base,
                source_signature,
            ],
        ).fetchone()

        if existing and existing[0]:
            assigned_name = str(existing[0])
            self._duckdb_conn.execute(
                """
                UPDATE duplicate_name_mappings
                SET updated_at = ?,
                    source_name = ?
                WHERE scope_type = ?
                  AND namespace_key = ?
                  AND dataset_key = ?
                  AND normalized_base = ?
                  AND source_signature = ?
                """,
                [
                    now,
                    source_name,
                    scope_type,
                    namespace_key,
                    dataset_key,
                    normalized_base,
                    source_signature,
                ],
            )
            return assigned_name

        used_rows = self._duckdb_conn.execute(
            """
            SELECT assigned_name
            FROM duplicate_name_mappings
            WHERE scope_type = ?
              AND namespace_key = ?
              AND dataset_key = ?
              AND normalized_base = ?
            """,
            [scope_type, namespace_key, dataset_key, normalized_base],
        ).fetchall()
        used_names = {str(r[0]) for r in used_rows if r and r[0]}

        if preferred_name and preferred_name not in used_names:
            assigned_name = preferred_name
        else:
            idx = 1
            while True:
                candidate = f"{normalized_base}_{idx}"
                if candidate not in used_names:
                    assigned_name = candidate
                    break
                idx += 1

        mapping_id = self._build_mapping_id(
            scope_type=scope_type,
            namespace_key=namespace_key,
            dataset_key=dataset_key,
            normalized_base=normalized_base,
            source_signature=source_signature,
        )

        self._duckdb_conn.execute(
            """
            INSERT INTO duplicate_name_mappings (
                mapping_id,
                scope_type,
                namespace_key,
                dataset_key,
                normalized_base,
                source_name,
                source_signature,
                assigned_name,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                mapping_id,
                scope_type,
                namespace_key,
                dataset_key,
                normalized_base,
                source_name,
                source_signature,
                assigned_name,
                now,
                now,
            ],
        )

        logger.debug(
            "Stored duplicate name mapping: %s/%s/%s/%s -> %s",
            scope_type,
            namespace_key,
            dataset_key,
            source_name,
            assigned_name,
        )
        return assigned_name