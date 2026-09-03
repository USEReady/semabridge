"""Persistent structural-fingerprint -> deployed-view-name mapping.

Lets the Snowflake deployment path recognize "this is the same underlying
model as an already-deployed view, just uploaded again under a different
file name" (e.g. a typo'd re-upload) and redeploy to that existing view
instead of creating a duplicate one. See utils/model_dedup.py for the
structural fingerprint itself.
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


class ModelFingerprintRepository:
    """Repository for structural-fingerprint -> view-name mappings."""

    def __init__(self) -> None:
        self._cache_lock = threading.RLock()
        self._cache: dict[tuple[str, str], str] = {}

        self._use_duckdb = False
        self._duckdb_conn = None
        self._engine = None

        try:
            self._engine = get_engine()
        except Exception as exc:
            if duckdb is None:
                raise
            self._use_duckdb = True
            db_path = get_default_db_path()
            self._duckdb_conn = duckdb.connect(db_path)
            logger.warning(
                "SQLAlchemy engine unavailable for model fingerprints (%s); "
                "falling back to DuckDB local state: %s",
                exc,
                db_path,
            )

        self._ensure_table()

    def _ensure_table(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS model_fingerprints (
            mapping_id VARCHAR(64) PRIMARY KEY,
            scope_key VARCHAR(512) NOT NULL,
            structural_fingerprint VARCHAR(64) NOT NULL,
            view_name VARCHAR(512) NOT NULL,
            source_name VARCHAR(512),
            project_id VARCHAR(256),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
        idx = (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_model_fingerprint_scope "
            "ON model_fingerprints (scope_key, structural_fingerprint)"
        )

        if self._use_duckdb and self._duckdb_conn is not None:
            self._duckdb_conn.execute(ddl)
            self._duckdb_conn.execute(idx)
            return

        from sqlalchemy import text

        with self._engine.begin() as conn:
            conn.execute(text(ddl))
            conn.execute(text(idx))

    @staticmethod
    def _build_mapping_id(scope_key: str, structural_fingerprint: str) -> str:
        raw_key = f"{scope_key}|{structural_fingerprint}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def find_existing_view_name(
        self, scope_key: str, structural_fingerprint: str
    ) -> Optional[str]:
        """Return the view name already deployed for this fingerprint in
        this scope, or None if this is the first time it's been seen."""
        cache_key = (scope_key, structural_fingerprint)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached:
                return cached

        if self._use_duckdb and self._duckdb_conn is not None:
            row = self._duckdb_conn.execute(
                """
                SELECT view_name FROM model_fingerprints
                WHERE scope_key = ? AND structural_fingerprint = ?
                """,
                [scope_key, structural_fingerprint],
            ).fetchone()
        else:
            from sqlalchemy import text

            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT view_name FROM model_fingerprints
                        WHERE scope_key = :scope_key
                          AND structural_fingerprint = :fp
                        """
                    ),
                    {"scope_key": scope_key, "fp": structural_fingerprint},
                ).fetchone()

        if not row or not row[0]:
            return None
        view_name = str(row[0])
        with self._cache_lock:
            self._cache[cache_key] = view_name
        return view_name

    def record_view_name(
        self,
        *,
        scope_key: str,
        structural_fingerprint: str,
        view_name: str,
        source_name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        """Record (or refresh) the view name deployed for this fingerprint.

        Call only after a successful deploy, so a fingerprint is never
        remembered for a model that didn't actually make it into Snowflake.
        """
        now = datetime.now(timezone.utc)
        mapping_id = self._build_mapping_id(scope_key, structural_fingerprint)

        if self._use_duckdb and self._duckdb_conn is not None:
            existing = self._duckdb_conn.execute(
                "SELECT 1 FROM model_fingerprints WHERE scope_key = ? AND structural_fingerprint = ?",
                [scope_key, structural_fingerprint],
            ).fetchone()
            if existing:
                self._duckdb_conn.execute(
                    """
                    UPDATE model_fingerprints
                    SET view_name = ?, source_name = ?, project_id = ?, updated_at = ?
                    WHERE scope_key = ? AND structural_fingerprint = ?
                    """,
                    [view_name, source_name, project_id, now, scope_key, structural_fingerprint],
                )
            else:
                self._duckdb_conn.execute(
                    """
                    INSERT INTO model_fingerprints (
                        mapping_id, scope_key, structural_fingerprint, view_name,
                        source_name, project_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [mapping_id, scope_key, structural_fingerprint, view_name, source_name, project_id, now, now],
                )
        else:
            from sqlalchemy import text

            with self._engine.begin() as conn:
                existing = conn.execute(
                    text(
                        "SELECT 1 FROM model_fingerprints WHERE scope_key = :scope_key "
                        "AND structural_fingerprint = :fp"
                    ),
                    {"scope_key": scope_key, "fp": structural_fingerprint},
                ).fetchone()
                if existing:
                    conn.execute(
                        text(
                            """
                            UPDATE model_fingerprints
                            SET view_name = :view_name, source_name = :source_name,
                                project_id = :project_id, updated_at = :updated_at
                            WHERE scope_key = :scope_key AND structural_fingerprint = :fp
                            """
                        ),
                        {
                            "view_name": view_name,
                            "source_name": source_name,
                            "project_id": project_id,
                            "updated_at": now,
                            "scope_key": scope_key,
                            "fp": structural_fingerprint,
                        },
                    )
                else:
                    conn.execute(
                        text(
                            """
                            INSERT INTO model_fingerprints (
                                mapping_id, scope_key, structural_fingerprint, view_name,
                                source_name, project_id, created_at, updated_at
                            ) VALUES (
                                :mapping_id, :scope_key, :fp, :view_name,
                                :source_name, :project_id, :created_at, :updated_at
                            )
                            """
                        ),
                        {
                            "mapping_id": mapping_id,
                            "scope_key": scope_key,
                            "fp": structural_fingerprint,
                            "view_name": view_name,
                            "source_name": source_name,
                            "project_id": project_id,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )

        with self._cache_lock:
            self._cache[(scope_key, structural_fingerprint)] = view_name
