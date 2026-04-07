"""
ORM-based Model Repository.

SQLAlchemy 2.0 replacement for DuckDBManager.  Provides identical
public methods but uses the centralized session factory instead of
raw ``duckdb.connect()`` calls.  Supports any SQLAlchemy-compatible
dialect: SQLite (default), PostgreSQL, DuckDB (via duckdb-engine), etc.

Pydantic dataclasses ``Snapshot`` and ``ModelChange`` are re-exported
from :mod:`semabridge.repository.duckdb_manager` for backward
compatibility so that existing imports continue to work.

Usage::

    from semabridge.repository.model_repository import ModelRepository

    repo = ModelRepository()                      # uses centralized URL
    repo = ModelRepository("sqlite:///:memory:")  # tests
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update, delete, and_
from sqlalchemy.orm import Session, sessionmaker

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import (
    Change,
    ModelVersion,
    Project,
    Run,
    SnapshotRow,
    SourceArtifact,
)
from semabridge.utils.logger import get_logger

# Re-export Pydantic models for backward compat
from semabridge.repository.duckdb_manager import ModelChange, Snapshot  # noqa: F401

logger = get_logger(__name__)


class _DBAPICursorResult:
    """DuckDB-like execute result wrapper for raw DBAPI cursors.

    Legacy code expects ``conn.execute(...).fetchall()`` to work against the
    repository connection. Psycopg2 connections do not expose ``execute`` on
    the connection itself, so we adapt a cursor into that shape.
    """

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> Any:
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class _CompatDBAPIConnection:
    """Compatibility wrapper that emulates DuckDB's connection.execute()."""

    def __init__(self, raw_connection: Any) -> None:
        self._raw_connection = raw_connection

    @staticmethod
    def _normalize_sql(sql: str, params: Optional[List[Any]]) -> str:
        if not params or "?" not in sql:
            return sql
        return sql.replace("?", "%s")

    @staticmethod
    def _is_result_read(sql: str) -> bool:
        first = (sql or "").lstrip().split(None, 1)
        if not first:
            return False
        return first[0].upper() in {"SELECT", "WITH", "SHOW", "DESC", "DESCRIBE", "PRAGMA"}

    def execute(self, sql: str, params: Optional[List[Any]] = None) -> _DBAPICursorResult:
        cursor = self._raw_connection.cursor()
        normalized_sql = self._normalize_sql(sql, params)
        if params:
            cursor.execute(normalized_sql, list(params))
        else:
            cursor.execute(normalized_sql)

        # Persist mutations immediately to match the old repository callers,
        # which usually rely on DuckDB autocommit-ish behavior.
        if not self._is_result_read(sql):
            try:
                self._raw_connection.commit()
            except Exception:
                pass

        return _DBAPICursorResult(cursor)

    def close(self) -> None:
        self._raw_connection.close()

    def commit(self) -> None:
        self._raw_connection.commit()

    def rollback(self) -> None:
        self._raw_connection.rollback()


class ModelRepository:
    """SQLAlchemy-backed semantic model repository.

    Drop-in replacement for ``DuckDBManager`` with identical public API.

    Args:
        url_override: Optional SQLAlchemy connection URL.  When *None*
            the centralized session factory (which reads
            ``SEMABRIDGE_DATABASE_URL`` / ``~/.semabridge/config.yaml``)
            is used.
    """

    _schema_init_lock = threading.Lock()
    _schema_initialized_urls: set[str] = set()

    def __init__(self, url_override: Optional[str] = None) -> None:
        # When a url_override is given this instance owns its own engine.
        # When using the shared singleton, we delegate to db_manager on every
        # _session() call so that database rotation is picked up automatically.
        self._url_override = url_override

        if url_override:
            from semabridge.repository.orm.session_factory import db_manager as _dm

            self._engine = _dm.create_db_engine(url_override)
            self._SessionLocal: sessionmaker[Session] = sessionmaker(
                bind=self._engine, expire_on_commit=False
            )
            self._ensure_schema_initialized_once(self._engine)
        else:
            from semabridge.repository.orm.session_factory import db_manager as _dm

            # Eagerly create tables on the current engine.
            # _session() will re-fetch the factory on every call to pick up
            # any subsequent rotation events.
            engine = _dm.get_engine()
            self._engine = engine
            self._ensure_schema_initialized_once(engine)
            self._SessionLocal = _dm.get_session_factory()

    @classmethod
    def _ensure_schema_initialized_once(cls, engine: Any) -> None:
        """Initialize ORM schema once per database URL.

        Repeated create_all calls on DuckDB during concurrent requests can
        trigger nested transaction errors. This guard keeps initialization
        idempotent and process-safe.
        """
        if engine.dialect.name == "snowflake":
            return

        url_key = engine.url.render_as_string(hide_password=True)
        if url_key in cls._schema_initialized_urls:
            return

        with cls._schema_init_lock:
            if url_key in cls._schema_initialized_urls:
                return
            try:
                with engine.begin() as conn:
                    Base.metadata.create_all(bind=conn)
                cls._schema_initialized_urls.add(url_key)
            except NotImplementedError as e:
                if "Snowflake" in str(e) or "index" in str(e).lower():
                    logger.warning(
                        f"Skipping index creation (not supported on this dialect): {e}"
                    )
                    cls._schema_initialized_urls.add(url_key)
                else:
                    raise
            except Exception as e:
                # If schema check fails due a transient/aborted DuckDB transaction,
                # avoid retrying on every request (which causes repeated 500 noise).
                # Core tables are typically already present after initial bootstrap.
                logger.warning(
                    "Skipping repeated schema initialization for %s due to error: %s",
                    url_key,
                    e,
                )
                cls._schema_initialized_urls.add(url_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session(self) -> Session:
        """Return a new session (caller must close/commit).

        When using the shared ``DatabaseManager`` (no url_override), the
        session factory is re-fetched on every call so that credential
        rotations are transparently picked up without restarting the process.
        """
        if self._url_override:
            return self._SessionLocal()
        # Re-fetch from the DatabaseManager so rotation events are observed.
        from semabridge.repository.orm.session_factory import db_manager as _dm
        return _dm.get_session_factory()()

    def _get_connection(self):
        """Backward-compat shim: return a connection with DuckDB-like execute().

        Allows legacy test code that used DuckDBManager._get_connection()
        to do low-level queries with positional '?' placeholders.

        When the backing engine is PostgreSQL/psycopg2, the raw DBAPI
        connection does not expose ``execute`` on the connection object, so we
        wrap it in a small compatibility adapter.
        """
        if self._url_override:
            raw_connection = self._engine.raw_connection()
        else:
            from semabridge.repository.orm.session_factory import db_manager as _dm
            raw_connection = _dm.get_engine().raw_connection()

        if hasattr(raw_connection, "execute"):
            return raw_connection

        return _CompatDBAPIConnection(raw_connection)

    @staticmethod
    def _row_to_snapshot(row: SnapshotRow) -> Snapshot:
        return Snapshot(
            snapshot_id=row.snapshot_id,
            project_id=row.project_id,
            timestamp=str(row.timestamp),
            version_tag=row.version_tag,
            sml_blob=json.loads(row.sml_blob) if row.sml_blob else {},
            status=row.status,
            duration_ms=row.duration_ms,
            error_message=row.error_message,
            initiated_by=row.initiated_by,
            run_id=row.run_id,
        )

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------

    def ensure_project(
        self,
        project_id: str,
        name: str,
        workspace_id: str,
        adapter: str = "fabric",
        source_connection: Optional[str] = None,
    ) -> None:
        """Ensure the project row exists (upsert)."""
        now = datetime.utcnow()
        with self._session() as session:
            existing = session.get(Project, project_id)
            if existing:
                existing.name = name
                existing.adapter = adapter
                existing.source_connection = source_connection
                existing.last_updated = now
            else:
                session.add(
                    Project(
                        project_id=project_id,
                        name=name,
                        workspace_id=workspace_id,
                        adapter=adapter,
                        source_connection=source_connection,
                        last_updated=now,
                    )
                )
            session.commit()

    # ------------------------------------------------------------------
    # Snapshots / version history
    # ------------------------------------------------------------------

    def get_head(self, project_id: str) -> Optional[Snapshot]:
        """Get the latest snapshot for a project."""
        with self._session() as session:
            row = (
                session.execute(
                    select(SnapshotRow)
                    .where(SnapshotRow.project_id == project_id)
                    .order_by(SnapshotRow.timestamp.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            return self._row_to_snapshot(row) if row else None

    def get_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        """Get a specific snapshot by ID."""
        with self._session() as session:
            row = session.get(SnapshotRow, snapshot_id)
            return self._row_to_snapshot(row) if row else None

    def get_snapshot_by_tag(
        self, project_id: str, tag: str
    ) -> Optional[Snapshot]:
        """Get a snapshot by its version tag."""
        with self._session() as session:
            row = (
                session.execute(
                    select(SnapshotRow)
                    .where(
                        and_(
                            SnapshotRow.project_id == project_id,
                            SnapshotRow.version_tag == tag,
                        )
                    )
                    .order_by(SnapshotRow.timestamp.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            return self._row_to_snapshot(row) if row else None

    def list_snapshots(
        self, project_id: str, limit: int = 10
    ) -> List[Snapshot]:
        """List snapshots for a project, newest first."""
        with self._session() as session:
            rows = (
                session.execute(
                    select(SnapshotRow)
                    .where(SnapshotRow.project_id == project_id)
                    .order_by(SnapshotRow.timestamp.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [self._row_to_snapshot(r) for r in rows]

    def commit_model(
        self,
        project_id: str,
        sml_json: Dict[str, Any],
        tag: Optional[str] = None,
        status: str = "success",
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
        initiated_by: str = "cli",
        run_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Commit a new version of the model.

        Returns:
            ``(committed: bool, snapshot_id: str)``
        """
        head = self.get_head(project_id)

        changes: List[ModelChange] = []
        if head:
            changes = _compute_diff(head.sml_blob, sml_json)
            if not changes and not tag:
                logger.info("No changes detected. Skipping commit.")
                return False, head.snapshot_id
        else:
            logger.info("No previous history. Initial commit.")

        snapshot_id = str(uuid.uuid4())
        timestamp = datetime.utcnow()

        with self._session() as session:
            session.add(
                SnapshotRow(
                    snapshot_id=snapshot_id,
                    project_id=project_id,
                    timestamp=timestamp,
                    version_tag=tag,
                    sml_blob=json.dumps(sml_json),
                    status=status,
                    duration_ms=duration_ms,
                    error_message=error_message,
                    initiated_by=initiated_by,
                    run_id=run_id,
                )
            )

            for change in changes:
                session.add(
                    Change(
                        change_id=str(uuid.uuid4()),
                        snapshot_id=snapshot_id,
                        object_type=change.object_type,
                        object_name=change.object_name,
                        diff_type=change.diff_type,
                        old_value=json.dumps(change.old_value)
                        if change.old_value
                        else None,
                        new_value=json.dumps(change.new_value)
                        if change.new_value
                        else None,
                    )
                )

            session.commit()

        logger.info(
            "Committed snapshot %s with %d changes", snapshot_id, len(changes)
        )
        return True, snapshot_id

    def rollback(
        self,
        project_id: str,
        target_snapshot_id: str,
        tag: Optional[str] = None,
    ) -> Tuple[bool, str, List[ModelChange]]:
        """Non-destructive rollback to a previous snapshot.

        Returns:
            ``(success, new_snapshot_id, changes)``
        """
        head = self.get_head(project_id)
        if not head:
            logger.error("No HEAD found for project")
            return False, "", []

        target = self.get_snapshot(target_snapshot_id)
        if not target:
            logger.error("Target snapshot %s not found", target_snapshot_id)
            return False, "", []

        if target.project_id != project_id:
            logger.error(
                "Snapshot %s does not belong to project %s",
                target_snapshot_id,
                project_id,
            )
            return False, "", []

        changes = _compute_diff(head.sml_blob, target.sml_blob)
        rollback_tag = tag or f"rollback_to_{target.version_tag or target_snapshot_id[:8]}"
        committed, new_snapshot_id = self.commit_model(
            project_id, target.sml_blob, rollback_tag
        )

        if committed:
            logger.info(
                "Rolled back to snapshot %s (new snapshot: %s)",
                target_snapshot_id[:12],
                new_snapshot_id[:12],
            )

        return committed, new_snapshot_id, changes

    def compare_versions(
        self, project_id: str, old_snapshot_id: str, new_snapshot_id: str
    ) -> List[Dict[str, Any]]:
        """Compare two versions and return tabular diff rows."""
        old_snap = self.get_snapshot(old_snapshot_id)
        new_snap = self.get_snapshot(new_snapshot_id)

        if not old_snap or not new_snap:
            return []
        if old_snap.project_id != project_id or new_snap.project_id != project_id:
            return []

        changes = _compute_diff(old_snap.sml_blob, new_snap.sml_blob)
        return [
            {
                "object": f"{c.object_type}.{c.object_name}",
                "previous_version": json.dumps(c.old_value, indent=2)
                if c.old_value
                else "—",
                "new_version": json.dumps(c.new_value, indent=2)
                if c.new_value
                else "—",
            }
            for c in changes
        ]

    def compare_versions_markdown(
        self, project_id: str, old_snapshot_id: str, new_snapshot_id: str
    ) -> str:
        """Compare two versions and return a markdown table."""
        diff = self.compare_versions(project_id, old_snapshot_id, new_snapshot_id)
        if not diff:
            return "No changes detected."
        lines = [
            "| Object | Previous Version | New Version |",
            "|--------|------------------|-------------|",
        ]
        for row in diff:
            prev = row["previous_version"].replace("\n", " ").replace("|", "\\|")[:100]
            new = row["new_version"].replace("\n", " ").replace("|", "\\|")[:100]
            lines.append(f"| {row['object']} | {prev} | {new} |")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Source Artifacts
    # ------------------------------------------------------------------

    def persist_source_artifact(
        self,
        run_id: str,
        source_format: Any,
        raw_json: Optional[Dict[str, Any]] = None,
        artifact_type: Optional[str] = None,
    ) -> Optional[str]:
        """Persist a source format artifact."""
        if source_format is None and raw_json is None:
            return None

        artifact_id = str(uuid.uuid4())
        timestamp = datetime.utcnow()

        if raw_json is not None:
            content = raw_json
            source_type = artifact_type or "unknown"
        elif hasattr(source_format, "model_dump"):
            content = source_format.model_dump(mode="json")
            source_type = getattr(source_format, "source_type", "unknown")
        else:
            content = dict(source_format) if hasattr(source_format, "__iter__") else {}
            source_type = getattr(source_format, "source_type", "unknown")

        try:
            with self._session() as session:
                session.add(
                    SourceArtifact(
                        artifact_id=artifact_id,
                        run_id=run_id,
                        source_type=source_type,
                        content_json=json.dumps(content),
                        created_at=timestamp,
                    )
                )
                session.commit()

            logger.info(
                "Persisted source artifact %s for run %s",
                artifact_id[:12],
                run_id[:12],
            )
            return artifact_id
        except Exception as exc:
            logger.error("Failed to persist source artifact: %s", exc)
            return None

    def get_source_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Get a source artifact by ID."""
        with self._session() as session:
            row = session.get(SourceArtifact, artifact_id)
            if not row:
                return None
            return {
                "artifact_id": row.artifact_id,
                "run_id": row.run_id,
                "source_type": row.source_type,
                "content": json.loads(row.content_json),
                "created_at": str(row.created_at),
            }

    # ------------------------------------------------------------------
    # Run tracking
    # ------------------------------------------------------------------

    def record_run_start(
        self,
        run_id: str,
        project_id: str,
        source_type: str,
        target_type: Optional[str] = None,
    ) -> None:
        """Record the start of an execution run."""
        timestamp = datetime.utcnow()
        with self._session() as session:
            session.add(
                Run(
                    run_id=run_id,
                    project_id=project_id,
                    started_at=timestamp,
                    status="running",
                    source_type=source_type,
                    target_type=target_type,
                )
            )
            session.commit()

    def record_run_complete(
        self,
        run_id: str,
        status: str,
        final_step: int,
        duration_ms: int,
        error_message: Optional[str] = None,
    ) -> None:
        """Record the completion of an execution run."""
        timestamp = datetime.utcnow()
        with self._session() as session:
            session.execute(
                update(Run)
                .where(Run.run_id == run_id)
                .values(
                    completed_at=timestamp,
                    status=status,
                    final_step=final_step,
                    duration_ms=duration_ms,
                    error_message=error_message,
                )
            )
            session.commit()

    # ------------------------------------------------------------------
    # Per-Model Version Control  (REQ-VC-001 / REQ-VC-002 / REQ-VC-003)
    # ------------------------------------------------------------------

    def insert_model_version(
        self,
        model_id: str,
        workspace_id: str,
        snapshot: Dict[str, Any],
        author: str = "system",
        change_summary: Optional[str] = None,
        version_tag: Optional[str] = None,
        is_rollback: bool = False,
        rollback_from_version: Optional[str] = None,
    ) -> str:
        """Insert or upsert a model version row."""
        timestamp = datetime.utcnow()
        osi_snapshot = _try_convert_to_osi(snapshot)

        with self._session() as session:
            # Upsert: overwrite same tag if non-rollback
            if version_tag and not is_rollback:
                existing = session.execute(
                    select(ModelVersion)
                    .where(
                        and_(
                            ModelVersion.model_id == model_id,
                            ModelVersion.workspace_id == workspace_id,
                            ModelVersion.version_tag == version_tag,
                            ModelVersion.is_rollback == False,
                        )
                    )
                    .limit(1)
                ).scalars().first()

                if existing:
                    existing.snapshot = json.dumps(osi_snapshot)
                    existing.change_summary = change_summary
                    existing.author = author
                    existing.created_at = timestamp
                    session.commit()
                    logger.info(
                        "Updated existing version %s tag=%s for model=%s",
                        existing.version_id[:8],
                        version_tag,
                        model_id,
                    )
                    return existing.version_id

            version_id = str(uuid.uuid4())
            session.add(
                ModelVersion(
                    version_id=version_id,
                    model_id=model_id,
                    workspace_id=workspace_id,
                    author=author,
                    created_at=timestamp,
                    change_summary=change_summary,
                    snapshot=json.dumps(osi_snapshot),
                    version_tag=version_tag,
                    is_rollback=is_rollback,
                    rollback_from_version=rollback_from_version,
                )
            )
            session.commit()

        tag_str = f" tag={version_tag}" if version_tag else ""
        logger.info(
            "Committed model version %s%s for model=%s workspace=%s",
            version_id[:8],
            tag_str,
            model_id,
            workspace_id,
        )
        return version_id

    def list_model_versions(
        self,
        model_id: str,
        workspace_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List version history for a model, newest first."""
        with self._session() as session:
            stmt = (
                select(ModelVersion)
                .where(ModelVersion.model_id == model_id)
                .order_by(ModelVersion.created_at.desc())
                .limit(limit)
            )
            if workspace_id:
                stmt = stmt.where(ModelVersion.workspace_id == workspace_id)

            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "version_id": r.version_id,
                    "model_id": r.model_id,
                    "workspace_id": r.workspace_id,
                    "author": r.author,
                    "timestamp": str(r.created_at),
                    "description": r.change_summary,
                    "version_tag": r.version_tag,
                    "is_rollback": bool(r.is_rollback),
                    "rollback_from_version": r.rollback_from_version,
                }
                for r in rows
            ]

    def delete_model_versions(
        self,
        model_id: str,
        workspace_id: Optional[str] = None,
    ) -> int:
        """Delete all version rows for a model."""
        with self._session() as session:
            stmt = delete(ModelVersion).where(ModelVersion.model_id == model_id)
            if workspace_id:
                stmt = stmt.where(ModelVersion.workspace_id == workspace_id)
            result = session.execute(stmt)
            session.commit()
            deleted = result.rowcount or 0
            logger.info("Deleted %d version(s) for model=%s", deleted, model_id)
            return deleted

    def get_model_version_snapshot(
        self, version_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve the full snapshot dict for a specific version."""
        with self._session() as session:
            row = session.get(ModelVersion, version_id)
            return json.loads(row.snapshot) if row else None

    def compare_model_versions_tabular(
        self, version_id_old: str, version_id_new: str
    ) -> List[Dict[str, Any]]:
        """Compare two model versions and produce a tabular diff."""
        old_snap = self.get_model_version_snapshot(version_id_old)
        new_snap = self.get_model_version_snapshot(version_id_new)
        if not old_snap or not new_snap:
            return []

        old_tag = self._get_version_tag(version_id_old) or version_id_old[:8]
        new_tag = self._get_version_tag(version_id_new) or version_id_new[:8]

        results: List[Dict[str, Any]] = []
        for c in _compute_diff(old_snap, new_snap):
            results.append(
                {
                    "object_name": c.object_name,
                    "object_type": c.object_type,
                    "property": c.diff_type,
                    "old_value": json.dumps(c.old_value) if c.old_value else None,
                    "old_version_tag": old_tag,
                    "new_value": json.dumps(c.new_value) if c.new_value else None,
                    "new_version_tag": new_tag,
                    "change_type": c.diff_type,
                }
            )
        return results

    def _get_version_tag(self, version_id: str) -> Optional[str]:
        """Fetch the version_tag for a given version_id."""
        with self._session() as session:
            row = session.get(ModelVersion, version_id)
            return row.version_tag if row else None

    def rollback_model_version(
        self,
        model_id: str,
        target_version_id: str,
        workspace_id: str,
        author: str = "system",
    ) -> str:
        """Non-destructive rollback — creates a new version from target."""
        old_snapshot = self.get_model_version_snapshot(target_version_id)
        if old_snapshot is None:
            raise ValueError(f"Version {target_version_id} not found")

        target_tag = self._get_version_tag(target_version_id) or target_version_id[:8]
        new_version_id = self.insert_model_version(
            model_id=model_id,
            workspace_id=workspace_id,
            snapshot=old_snapshot,
            author=author,
            change_summary=f"Rollback to version {target_tag}",
            is_rollback=True,
            rollback_from_version=target_version_id,
        )
        logger.info(
            "Rolled back model=%s to version %s → new version %s",
            model_id,
            target_tag,
            new_version_id[:8],
        )
        return new_version_id

    # ------------------------------------------------------------------
    # Legacy compatibility: thread cursor (no-op under ORM)
    # ------------------------------------------------------------------

    def get_thread_cursor(self) -> "Session":
        """Return a new SQLAlchemy session (replaces DuckDB cursor API).

        .. deprecated::
            Direct session usage is preferred.  This shim exists so that
            code that called ``db_manager.get_thread_cursor()`` continues
            to work.
        """
        return self._session()


# =============================================================================
# Module-level pure-Python helpers (shared with DuckDBManager)
# =============================================================================

def _compute_diff(
    old_json: Dict[str, Any], new_json: Dict[str, Any]
) -> List[ModelChange]:
    """Compute diff between two SML JSON objects."""
    EXCLUDE_FIELDS = {
        "created_at",
        "modified_at",
        "confidence",
        "row_count",
        "run_id",
        "snapshot_id",
        "timestamp",
        "duration_ms",
    }

    def normalize_value(v: Any) -> Any:
        if isinstance(v, list):
            try:
                return sorted(v, key=str)
            except TypeError:
                return v
        return v

    def normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
        return {k: normalize_value(v) for k, v in item.items() if k not in EXCLUDE_FIELDS}

    def index_by_name(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return {item["unique_name"]: item for item in items}

    section_map = {
        "metrics": "metric",
        "dimensions": "dimension",
        "datasets": "dataset",
        "relationships": "relationship",
    }

    changes: List[ModelChange] = []
    for section, type_name in section_map.items():
        old_items = index_by_name(old_json.get(section, []))
        new_items = index_by_name(new_json.get(section, []))

        for key in set(old_items) | set(new_items):
            if key not in old_items:
                changes.append(
                    ModelChange(
                        object_type=type_name,
                        object_name=key,
                        diff_type="ADDED",
                        new_value=new_items[key],
                    )
                )
            elif key not in new_items:
                changes.append(
                    ModelChange(
                        object_type=type_name,
                        object_name=key,
                        diff_type="DELETED",
                        old_value=old_items[key],
                    )
                )
            else:
                old_n = normalize_item(old_items[key])
                new_n = normalize_item(new_items[key])
                if json.dumps(old_n, sort_keys=True) != json.dumps(new_n, sort_keys=True):
                    changes.append(
                        ModelChange(
                            object_type=type_name,
                            object_name=key,
                            diff_type="MODIFIED",
                            old_value=old_items[key],
                            new_value=new_items[key],
                        )
                    )
    return changes


def _try_convert_to_osi(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Attempt to serialise snapshot into OSI spec format."""
    if "datasets" in snapshot and "unique_name" in snapshot:
        return snapshot
    try:
        from semabridge.converter.sml_to_osi import SMLToOSIConverter
        from semabridge.sml.models import SMLModel

        sml = SMLModel(**snapshot)
        converter = SMLToOSIConverter()
        osi_model = converter.to_osi(sml)
        return osi_model.model_dump(mode="json")
    except Exception as exc:
        logger.debug("OSI conversion skipped (storing raw): %s", exc)
        return snapshot
