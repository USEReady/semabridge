"""
DuckDB-based Version Control System.

Manages semantic model history using DuckDB as an embedded metadata engine.
Treats SML JSON as versioned data, allowing diffs, rollbacks, and time-travel.
"""

from __future__ import annotations

import json
import uuid
import warnings
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import duckdb

from semabridge.repository.schemas import ModelChange, Snapshot  # noqa: F401 — re-exported
from semabridge.utils.logger import get_logger

warnings.warn(
    "semabridge.repository.duckdb_manager is deprecated and will be removed in a future "
    "release. Use semabridge.repository.model_repository.ModelRepository instead. "
    "Import Snapshot and ModelChange from semabridge.repository.schemas.",
    DeprecationWarning,
    stacklevel=2,
)

logger = get_logger(__name__)


class DuckDBManager:
    """
    Manages semantic model state in DuckDB.
    """
    
    def __init__(self, db_path: str = None):
        # Use centralized resolver for consistent DB location
        if db_path is None:
            from semabridge.core.db_resolver import get_default_db_path
            db_path = get_default_db_path()
        self.db_path = db_path
        self._persistent_conn: Optional[duckdb.DuckDBPyConnection] = None
        self._init_db()
    
    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get database connection."""
        conn = duckdb.connect(self.db_path)
        return conn
    
    def _get_persistent_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create the persistent main-thread connection.

        This connection stays open for the lifetime of the DuckDBManager
        and is used to generate thread-local cursors via get_thread_cursor().

        Returns:
            Persistent DuckDB connection for the main thread.
        """
        if self._persistent_conn is None:
            self._persistent_conn = duckdb.connect(self.db_path)
        return self._persistent_conn

    def get_thread_cursor(self) -> duckdb.DuckDBPyConnection:
        """Get a thread-local cursor for safe parallel database access.

        DuckDBPyConnection is NOT thread-safe. Calling .cursor() on a
        persistent connection creates a thread-local cursor bound to
        the same underlying database file, permitting safe simultaneous
        inserts without violating internal consistency.

        Each thread MUST use its own cursor. Never share cursors across
        threads.

        Returns:
            A thread-local DuckDB cursor.
        """
        return self._get_persistent_connection().cursor()
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    workspace_id VARCHAR,
                    adapter VARCHAR,
                    source_connection VARCHAR,
                    last_updated TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    version_tag VARCHAR,
                    sml_blob JSON,
                    status VARCHAR DEFAULT 'success',
                    duration_ms INTEGER,
                    error_message VARCHAR,
                    initiated_by VARCHAR DEFAULT 'cli',
                    run_id VARCHAR,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );
                
                CREATE TABLE IF NOT EXISTS changes (
                    change_id VARCHAR PRIMARY KEY,
                    snapshot_id VARCHAR NOT NULL,
                    object_type VARCHAR,
                    object_name VARCHAR,
                    diff_type VARCHAR,
                    old_value JSON,
                    new_value JSON,
                    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
                );
                
                -- Execution Run tracking (Step 2, 10)
                CREATE TABLE IF NOT EXISTS runs (
                    run_id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    status VARCHAR NOT NULL DEFAULT 'running',
                    final_step INTEGER,
                    source_type VARCHAR,
                    target_type VARCHAR,
                    error_message VARCHAR,
                    duration_ms INTEGER
                );
                
                -- Source Format artifacts (Step 4, 7)
                CREATE TABLE IF NOT EXISTS source_artifacts (
                    artifact_id VARCHAR PRIMARY KEY,
                    run_id VARCHAR NOT NULL,
                    source_type VARCHAR NOT NULL,
                    content_json JSON NOT NULL,
                    created_at TIMESTAMP NOT NULL
                );

                -- Per-model version control (REQ-VC-001)
                CREATE TABLE IF NOT EXISTS model_versions (
                    version_id    VARCHAR PRIMARY KEY,
                    model_id      VARCHAR NOT NULL,
                    workspace_id  VARCHAR NOT NULL,
                    author        VARCHAR,
                    created_at    TIMESTAMP DEFAULT current_timestamp,
                    change_summary VARCHAR,
                    snapshot      JSON NOT NULL,
                    version_tag   VARCHAR,
                    is_rollback   BOOLEAN DEFAULT FALSE,
                    rollback_from_version VARCHAR
                );
            """)


            # Migrations for existing tables
            # projects table
            cols = [c[1] for c in conn.execute("PRAGMA table_info('projects')").fetchall()]
            if 'adapter' not in cols:
                conn.execute("ALTER TABLE projects ADD COLUMN adapter VARCHAR")
            if 'source_connection' not in cols:
                conn.execute("ALTER TABLE projects ADD COLUMN source_connection VARCHAR")

            # snapshots table
            cols = [c[1] for c in conn.execute("PRAGMA table_info('snapshots')").fetchall()]
            if 'status' not in cols:
                conn.execute("ALTER TABLE snapshots ADD COLUMN status VARCHAR DEFAULT 'success'")
            if 'duration_ms' not in cols:
                conn.execute("ALTER TABLE snapshots ADD COLUMN duration_ms INTEGER")
            if 'error_message' not in cols:
                conn.execute("ALTER TABLE snapshots ADD COLUMN error_message VARCHAR")
            if 'initiated_by' not in cols:
                conn.execute("ALTER TABLE snapshots ADD COLUMN initiated_by VARCHAR DEFAULT 'cli'")
            if 'run_id' not in cols:
                conn.execute("ALTER TABLE snapshots ADD COLUMN run_id VARCHAR")

            # model_versions table migrations
            mv_cols = [c[1] for c in conn.execute("PRAGMA table_info('model_versions')").fetchall()]
            if 'version_tag' not in mv_cols:
                conn.execute("ALTER TABLE model_versions ADD COLUMN version_tag VARCHAR")
            if 'is_rollback' not in mv_cols:
                conn.execute("ALTER TABLE model_versions ADD COLUMN is_rollback BOOLEAN DEFAULT FALSE")
            if 'rollback_from_version' not in mv_cols:
                conn.execute("ALTER TABLE model_versions ADD COLUMN rollback_from_version VARCHAR")
        finally:
            conn.close()
    
    def ensure_project(self, project_id: str, name: str, workspace_id: str, adapter: str = "fabric", source_connection: str = None) -> None:
        """Ensure project exists."""
        conn = self._get_connection()
        try:
            now = datetime.utcnow()
            conn.execute("""
                INSERT INTO projects (project_id, name, workspace_id, adapter, source_connection, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id) DO UPDATE SET 
                    name = excluded.name,
                    adapter = excluded.adapter,
                    source_connection = excluded.source_connection,
                    last_updated = excluded.last_updated
            """, [project_id, name, workspace_id, adapter, source_connection, now])
        finally:
            conn.close()
    
    def get_head(self, project_id: str) -> Optional[Snapshot]:
        """Get the latest snapshot for a project."""
        conn = self._get_connection()
        try:
            result = conn.execute("""
                SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                       status, duration_ms, error_message, initiated_by, run_id
                FROM snapshots
                WHERE project_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, [project_id]).fetchone()
            
            if not result:
                return None
            
            return Snapshot(
                snapshot_id=result[0],
                project_id=result[1],
                timestamp=str(result[2]),
                version_tag=result[3],
                sml_blob=json.loads(result[4]),
                status=result[5],
                duration_ms=result[6],
                error_message=result[7],
                initiated_by=result[8],
                run_id=result[9]
            )
        finally:
            conn.close()
            
    def commit_model(self, project_id: str, sml_json: Dict[str, Any], tag: Optional[str] = None,
                    status: str = "success", duration_ms: Optional[int] = None,
                    error_message: Optional[str] = None, initiated_by: str = "cli",
                    run_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Commit a new version of the model.
        
        Returns:
            (committed: bool, snapshot_id: str)
        """
        head = self.get_head(project_id)
        
        # Check for changes if HEAD exists
        changes = []
        if head:
            changes = self._compute_diff(head.sml_blob, sml_json)
            if not changes and not tag:
                logger.info("No changes detected. Skipping commit.")
                return False, head.snapshot_id
        else:
             logger.info("No previous history. Initial commit.")
        
        # Create new snapshot
        snapshot_id = str(uuid.uuid4())
        timestamp = datetime.utcnow()
        
        conn = self._get_connection()
        try:
            conn.begin()
            
            # Insert Snapshot
            conn.execute("""
                INSERT INTO snapshots (snapshot_id, project_id, timestamp, version_tag, sml_blob,
                                     status, duration_ms, error_message, initiated_by, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [snapshot_id, project_id, timestamp, tag, json.dumps(sml_json),
                  status, duration_ms, error_message, initiated_by, run_id])
            
            # Insert Changes
            if changes:
                for change in changes:
                    change_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO changes (change_id, snapshot_id, object_type, object_name, diff_type, old_value, new_value)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, [
                        change_id,
                        snapshot_id,
                        change.object_type,
                        change.object_name,
                        change.diff_type,
                        json.dumps(change.old_value) if change.old_value else None,
                        json.dumps(change.new_value) if change.new_value else None
                    ])
            
            conn.commit()
            logger.info(f"Committed snapshot {snapshot_id} with {len(changes)} changes")
            return True, snapshot_id
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Commit failed: {e}")
            raise e
        finally:
            conn.close()
            
    def _compute_diff(self, old_json: Dict[str, Any], new_json: Dict[str, Any]) -> List[ModelChange]:
        """
        Compute diff between two SML JSON objects using simple Python comparison for now.
        Can be upgraded to DuckDB SQL-based JSON diff for massive models.
        """
        changes = []
        
        # Fields to exclude from comparison (volatile/metadata fields that change between extractions)
        EXCLUDE_FIELDS = {
            'created_at', 'modified_at', 'confidence', 'row_count',
            'run_id', 'snapshot_id', 'timestamp', 'duration_ms'
        }
        
        def normalize_value(v):
            """Normalize a value for comparison - sort lists to ignore order."""
            if isinstance(v, list):
                # Sort list if all elements are comparable (strings, numbers)
                try:
                    return sorted(v, key=str)
                except TypeError:
                    return v
            return v
        
        def normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
            """Remove volatile fields and normalize lists for comparison."""
            return {k: normalize_value(v) for k, v in item.items() if k not in EXCLUDE_FIELDS}
        
        # Compare objects: Metrics, Dimensions, Datasets
        # Helper to index lists by unique_name
        def index_by_name(items):
            return {item["unique_name"]: item for item in items}
        
        section_map = {
            "metrics": "metric",
            "dimensions": "dimension",
            "datasets": "dataset",
            "relationships": "relationship"
        }
        
        for section, type_name in section_map.items():
            old_items = index_by_name(old_json.get(section, []))
            new_items = index_by_name(new_json.get(section, []))
            
            all_keys = set(old_items.keys()) | set(new_items.keys())
            
            for key in all_keys:
                if key not in old_items:
                    changes.append(ModelChange(
                        object_type=type_name,
                        object_name=key,
                        diff_type="ADDED",
                        new_value=new_items[key]
                    ))
                elif key not in new_items:
                    changes.append(ModelChange(
                        object_type=type_name,
                        object_name=key,
                        diff_type="DELETED",
                        old_value=old_items[key]
                    ))
                else:
                    # Normalize both items before comparing (exclude volatile fields)
                    old_normalized = normalize_item(old_items[key])
                    new_normalized = normalize_item(new_items[key])
                    
                    if json.dumps(old_normalized, sort_keys=True) != json.dumps(new_normalized, sort_keys=True):
                        changes.append(ModelChange(
                            object_type=type_name,
                            object_name=key,
                            diff_type="MODIFIED",
                            old_value=old_items[key],
                            new_value=new_items[key]
                        ))
                
        return changes
    
    def get_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        """Get a specific snapshot by ID."""
        conn = self._get_connection()
        try:
            result = conn.execute("""
                SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                       status, duration_ms, error_message, initiated_by, run_id
                FROM snapshots
                WHERE snapshot_id = ?
            """, [snapshot_id]).fetchone()
            
            if not result:
                return None
            
            return Snapshot(
                snapshot_id=result[0],
                project_id=result[1],
                timestamp=str(result[2]),
                version_tag=result[3],
                sml_blob=json.loads(result[4]),
                status=result[5],
                duration_ms=result[6],
                error_message=result[7],
                initiated_by=result[8],
                run_id=result[9]
            )
        finally:
            conn.close()
    
    def list_snapshots(self, project_id: str, limit: int = 10) -> List[Snapshot]:
        """List snapshots for a project, newest first."""
        conn = self._get_connection()
        try:
            results = conn.execute("""
                SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                       status, duration_ms, error_message, initiated_by, run_id
                FROM snapshots
                WHERE project_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, [project_id, limit]).fetchall()
            
            return [
                Snapshot(
                    snapshot_id=r[0],
                    project_id=r[1],
                    timestamp=str(r[2]),
                    version_tag=r[3],
                    sml_blob=json.loads(r[4]),
                    status=r[5],
                    duration_ms=r[6],
                    error_message=r[7],
                    initiated_by=r[8],
                    run_id=r[9]
                )
                for r in results
            ]
        finally:
            conn.close()
    
    def list_all_snapshots(self, limit: int = 10000) -> List[Snapshot]:
        """List snapshots from ALL projects, newest first.
        
        Used by Explore page to show all historical snapshots across all projects.
        Returns snapshots with project_id preserved in the result.
        """
        conn = self._get_connection()
        try:
            results = conn.execute("""
                SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                       status, duration_ms, error_message, initiated_by, run_id
                FROM snapshots
                ORDER BY timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
            
            return [
                Snapshot(
                    snapshot_id=r[0],
                    project_id=r[1],
                    timestamp=str(r[2]),
                    version_tag=r[3],
                    sml_blob=json.loads(r[4]),
                    status=r[5],
                    duration_ms=r[6],
                    error_message=r[7],
                    initiated_by=r[8],
                    run_id=r[9]
                )
                for r in results
            ]
        finally:
            conn.close()
    
    def rollback(self, project_id: str, target_snapshot_id: str, tag: str = None) -> Tuple[bool, str, List[ModelChange]]:
        """
        Rollback to a previous snapshot.
        
        This creates a NEW commit with the state from target_snapshot_id.
        The history is preserved (non-destructive rollback).
        
        Returns:
            (success: bool, new_snapshot_id: str, changes: List[ModelChange])
        """
        # Get current HEAD
        head = self.get_head(project_id)
        if not head:
            logger.error("No HEAD found for project")
            return False, "", []
        
        # Get target snapshot
        target = self.get_snapshot(target_snapshot_id)
        if not target:
            logger.error(f"Target snapshot {target_snapshot_id} not found")
            return False, "", []
        
        if target.project_id != project_id:
            logger.error(f"Snapshot {target_snapshot_id} does not belong to project {project_id}")
            return False, "", []
        
        # Compute changes (what we're reverting)
        changes = self._compute_diff(head.sml_blob, target.sml_blob)
        
        # Commit the target state as a new snapshot
        rollback_tag = tag or f"rollback_to_{target.version_tag or target_snapshot_id[:8]}"
        committed, new_snapshot_id = self.commit_model(project_id, target.sml_blob, rollback_tag)
        
        if committed:
            logger.info(f"Rolled back to snapshot {target_snapshot_id[:12]}... (new snapshot: {new_snapshot_id[:12]}...)")
        
        return committed, new_snapshot_id, changes
    
    def compare_versions(self, project_id: str, old_snapshot_id: str, new_snapshot_id: str) -> List[Dict[str, Any]]:
        """
        Compare two versions and return tabular diff.
        
        Returns a list of dicts with format:
        [
            {"object": "metric.Revenue", "previous_version": "SUM([Amount])", "new_version": "SUM([Revenue])"},
            {"object": "dataset.Customer", "previous_version": "—", "new_version": "{...}"},
            ...
        ]
        """
        old_snap = self.get_snapshot(old_snapshot_id)
        new_snap = self.get_snapshot(new_snapshot_id)
        
        if not old_snap or not new_snap:
            return []
        
        if old_snap.project_id != project_id or new_snap.project_id != project_id:
            return []
        
        changes = self._compute_diff(old_snap.sml_blob, new_snap.sml_blob)
        
        tabular_diff = []
        for change in changes:
            row = {
                "object": f"{change.object_type}.{change.object_name}",
                "previous_version": json.dumps(change.old_value, indent=2) if change.old_value else "—",
                "new_version": json.dumps(change.new_value, indent=2) if change.new_value else "—",
            }
            tabular_diff.append(row)
        
        return tabular_diff
    
    def compare_versions_markdown(self, project_id: str, old_snapshot_id: str, new_snapshot_id: str) -> str:
        """
        Compare two versions and return a markdown table.
        
        Format:
        | Object | Previous Version | New Version |
        |--------|------------------|-------------|
        | metric.Revenue | SUM([Amount]) | SUM([Revenue]) |
        """
        diff = self.compare_versions(project_id, old_snapshot_id, new_snapshot_id)
        
        if not diff:
            return "No changes detected."
        
        lines = ["| Object | Previous Version | New Version |", "|--------|------------------|-------------|"]
        
        for row in diff:
            # Escape newlines and pipes for markdown
            prev = row["previous_version"].replace("\n", " ").replace("|", "\\|")[:100]
            new = row["new_version"].replace("\n", " ").replace("|", "\\|")[:100]
            lines.append(f"| {row['object']} | {prev} | {new} |")
        
        return "\n".join(lines)
    
    def get_snapshot_by_tag(self, project_id: str, tag: str) -> Optional[Snapshot]:
        """Get a snapshot by its version tag."""
        conn = self._get_connection()
        try:
            result = conn.execute("""
                SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                       status, duration_ms, error_message, initiated_by, run_id
                FROM snapshots
                WHERE project_id = ? AND version_tag = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, [project_id, tag]).fetchone()
            
            if not result:
                return None
            
            return Snapshot(
                snapshot_id=result[0],
                project_id=result[1],
                timestamp=str(result[2]),
                version_tag=result[3],
                sml_blob=json.loads(result[4]),
                status=result[5],
                duration_ms=result[6],
                error_message=result[7],
                initiated_by=result[8],
                run_id=result[9]
            )
        finally:
            conn.close()
    
    def persist_source_artifact(
        self,
        run_id: str,
        source_format: Any,
        raw_json: Optional[Dict[str, Any]] = None,
        artifact_type: Optional[str] = None,
    ) -> Optional[str]:
        """
        Persist a Source Format artifact (Step 7).

        Can be called in two modes:
        1. Standard mode: pass ``source_format`` (SourceFormat object) — the existing behaviour.
        2. Raw mode: pass ``raw_json`` + ``artifact_type`` to persist arbitrary JSON blobs
           (e.g. the OSI intermediate snapshot).

        Args:
            run_id: The run ID this artifact belongs to.
            source_format: SourceFormat object to persist (mutually exclusive with raw_json).
            raw_json: Pre-serialised dict to persist directly (mutually exclusive with source_format).
            artifact_type: Source type label when using raw_json mode, e.g. ``"osi_intermediate"``.

        Returns:
            artifact_id if successful, None otherwise.
        """
        if source_format is None and raw_json is None:
            return None

        artifact_id = str(uuid.uuid4())
        timestamp = datetime.utcnow()

        conn = self._get_connection()
        try:
            if raw_json is not None:
                # Raw JSON mode — used for OSI intermediate snapshots
                content = raw_json
                source_type = artifact_type or "unknown"
            elif hasattr(source_format, 'model_dump'):
                content = source_format.model_dump(mode='json')
                source_type = getattr(source_format, 'source_type', 'unknown')
            else:
                content = dict(source_format) if hasattr(source_format, '__iter__') else {}
                source_type = getattr(source_format, 'source_type', 'unknown')
            
            conn.execute("""
                INSERT INTO source_artifacts (artifact_id, run_id, source_type, content_json, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, [artifact_id, run_id, source_type, json.dumps(content), timestamp])
            
            logger.info(f"Persisted source artifact {artifact_id[:12]}... for run {run_id[:12]}...")
            return artifact_id
            
        except Exception as e:
            logger.error(f"Failed to persist source artifact: {e}")
            return None
        finally:
            conn.close()
    
    def get_source_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Get a source artifact by ID."""
        conn = self._get_connection()
        try:
            result = conn.execute("""
                SELECT artifact_id, run_id, source_type, content_json, created_at
                FROM source_artifacts
                WHERE artifact_id = ?
            """, [artifact_id]).fetchone()
            
            if not result:
                return None
            
            return {
                "artifact_id": result[0],
                "run_id": result[1],
                "source_type": result[2],
                "content": json.loads(result[3]),
                "created_at": str(result[4]),
            }
        finally:
            conn.close()
    
    def record_run_start(self, run_id: str, project_id: str, source_type: str, target_type: Optional[str] = None) -> None:
        """Record the start of an execution run (Step 2)."""
        timestamp = datetime.utcnow()
        
        conn = self._get_connection()
        try:
            conn.execute("""
                INSERT INTO runs (run_id, project_id, started_at, status, source_type, target_type)
                VALUES (?, ?, ?, 'running', ?, ?)
            """, [run_id, project_id, timestamp, source_type, target_type])
        finally:
            conn.close()
    
    def record_run_complete(
        self,
        run_id: str,
        status: str,
        final_step: int,
        duration_ms: int,
        error_message: Optional[str] = None
    ) -> None:
        """Record the completion of an execution run (Step 10)."""
        timestamp = datetime.utcnow()
        
        conn = self._get_connection()
        try:
            conn.execute("""
                UPDATE runs
                SET completed_at = ?,
                    status = ?,
                    final_step = ?,
                    duration_ms = ?,
                    error_message = ?
                WHERE run_id = ?
            """, [timestamp, status, final_step, duration_ms, error_message, run_id])
        finally:
            conn.close()

    # ---------------------------------------------------------------
    # Per-Model Version Control  (REQ-VC-001 / REQ-VC-002 / REQ-VC-003)
    # ---------------------------------------------------------------

    def _try_convert_to_osi(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Attempt to serialize a snapshot dict into OSI spec format.

        If the snapshot already looks like an OSI model (has 'datasets' key)
        or can be converted from SML, returns the OSI dict.
        Falls back to storing the raw snapshot if conversion fails.

        Args:
            snapshot: Raw model definition dict.

        Returns:
            OSI-formatted dict, or original snapshot on failure.
        """
        # Already in OSI format (has datasets key from OSIModel)
        if "datasets" in snapshot and "unique_name" in snapshot:
            return snapshot

        try:
            from semabridge.converter.sml_to_osi import SMLToOSIConverter
            from semabridge.sml.models import SMLModel
            sml = SMLModel(**snapshot)
            converter = SMLToOSIConverter()
            osi_model = converter.to_osi(sml)
            return osi_model.model_dump(mode="json")
        except Exception as e:
            logger.debug(f"OSI conversion skipped (storing raw): {e}")
            return snapshot

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
        """
        Insert or update a version row for the given model.

        If a non-rollback version with the same model_id + workspace_id +
        version_tag already exists, that row is updated in place (upsert)
        so the history list does not accumulate duplicate tag entries.
        Rollback versions are always inserted as new rows.

        Args:
            model_id: Semantic model identifier.
            workspace_id: The Fabric workspace this version belongs to.
            snapshot: Full model definition (YAML/JSON) at point in time.
            author: Who initiated the save.
            change_summary: Human-readable description of the change.
            version_tag: Semantic version tag (e.g. "v1.0").
            is_rollback: Whether this version was created by a rollback.
            rollback_from_version: The version_id this was rolled back from.

        Returns:
            The version_id (existing if updated, new UUID if inserted).
        """
        timestamp = datetime.utcnow()

        # Attempt OSI conversion before storage
        osi_snapshot = self._try_convert_to_osi(snapshot)

        conn = self._get_connection()
        try:
            # --- Upsert: if same tag already exists for this model, overwrite it ---
            if version_tag and not is_rollback:
                row = conn.execute(
                    """
                    SELECT version_id FROM model_versions
                    WHERE model_id = ? AND workspace_id = ? AND version_tag = ?
                      AND (is_rollback = FALSE OR is_rollback IS NULL)
                    LIMIT 1
                    """,
                    [model_id, workspace_id, version_tag],
                ).fetchone()

                if row:
                    existing_id: str = row[0]
                    conn.execute(
                        """
                        UPDATE model_versions
                        SET snapshot       = ?,
                            change_summary = ?,
                            author         = ?,
                            created_at     = ?
                        WHERE version_id = ?
                        """,
                        [json.dumps(osi_snapshot), change_summary, author, timestamp, existing_id],
                    )
                    logger.info(
                        f"Updated existing version {existing_id[:8]}... tag={version_tag} "
                        f"for model={model_id} workspace={workspace_id}"
                    )
                    return existing_id

            # --- Insert new row ---
            version_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO model_versions
                    (version_id, model_id, workspace_id, author, created_at,
                     change_summary, snapshot, version_tag, is_rollback, rollback_from_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                version_id, model_id, workspace_id, author,
                timestamp, change_summary, json.dumps(osi_snapshot),
                version_tag, is_rollback, rollback_from_version,
            ])
            tag_str = f" tag={version_tag}" if version_tag else ""
            logger.info(
                f"Committed model version {version_id[:8]}...{tag_str} "
                f"for model={model_id} workspace={workspace_id}"
            )
            return version_id
        finally:
            conn.close()

    def list_model_versions(
        self,
        model_id: str,
        workspace_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        List version history for a model, newest first.

        Args:
            model_id: Semantic model identifier.
            workspace_id: Optional filter by workspace.
            limit: Maximum rows to return.

        Returns:
            List of version dicts.
        """
        conn = self._get_connection()
        try:
            if workspace_id:
                rows = conn.execute("""
                    SELECT version_id, model_id, workspace_id, author,
                           created_at, change_summary, version_tag,
                           is_rollback, rollback_from_version
                    FROM model_versions
                    WHERE model_id = ? AND workspace_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, [model_id, workspace_id, limit]).fetchall()
            else:
                rows = conn.execute("""
                    SELECT version_id, model_id, workspace_id, author,
                           created_at, change_summary, version_tag,
                           is_rollback, rollback_from_version
                    FROM model_versions
                    WHERE model_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, [model_id, limit]).fetchall()

            return [
                {
                    "version_id": r[0],
                    "model_id": r[1],
                    "workspace_id": r[2],
                    "author": r[3],
                    "timestamp": str(r[4]),
                    "description": r[5],
                    "version_tag": r[6],
                    "is_rollback": bool(r[7]) if r[7] is not None else False,
                    "rollback_from_version": r[8],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def delete_model_versions(
        self,
        model_id: str,
        workspace_id: Optional[str] = None,
    ) -> int:
        """
        Delete all version rows for a model.

        Args:
            model_id: Model whose history should be cleared.
            workspace_id: When set, only delete rows for that workspace.

        Returns:
            Number of rows deleted.
        """
        conn = self._get_connection()
        try:
            if workspace_id:
                result = conn.execute(
                    "DELETE FROM model_versions WHERE model_id = ? AND workspace_id = ?",
                    [model_id, workspace_id],
                )
            else:
                result = conn.execute(
                    "DELETE FROM model_versions WHERE model_id = ?",
                    [model_id],
                )
            deleted = result.rowcount if result.rowcount is not None else 0
            logger.info(f"Deleted {deleted} version(s) for model={model_id}")
            return deleted
        finally:
            conn.close()

    def get_model_version_snapshot(
        self, version_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve the full snapshot JSON for a specific version.

        Returns:
            The snapshot dict or None.
        """
        conn = self._get_connection()
        try:
            row = conn.execute("""
                SELECT snapshot FROM model_versions WHERE version_id = ?
            """, [version_id]).fetchone()
            if row:
                return json.loads(row[0])
            return None
        finally:
            conn.close()

    def compare_model_versions_tabular(
        self, version_id_old: str, version_id_new: str,
    ) -> List[Dict[str, Any]]:
        """
        Compare two model versions and produce a tabular diff.

        Returns:
            List of dicts with keys: object_name, object_type, property,
            old_value, old_version_tag, new_value, new_version_tag, change_type.
        """
        old_snap = self.get_model_version_snapshot(version_id_old)
        new_snap = self.get_model_version_snapshot(version_id_new)
        if not old_snap or not new_snap:
            return []

        # Fetch version tags for labeling
        old_tag = self._get_version_tag(version_id_old) or version_id_old[:8]
        new_tag = self._get_version_tag(version_id_new) or version_id_new[:8]

        changes = self._compute_diff(old_snap, new_snap)
        results: List[Dict[str, Any]] = []
        for c in changes:
            results.append({
                "object_name": c.object_name,
                "object_type": c.object_type,
                "property": c.diff_type,
                "old_value": json.dumps(c.old_value) if c.old_value else None,
                "old_version_tag": old_tag,
                "new_value": json.dumps(c.new_value) if c.new_value else None,
                "new_version_tag": new_tag,
                "change_type": c.diff_type,
            })
        return results

    def _get_version_tag(self, version_id: str) -> Optional[str]:
        """Fetch the version_tag for a given version_id."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT version_tag FROM model_versions WHERE version_id = ?",
                [version_id],
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def rollback_model_version(
        self,
        model_id: str,
        target_version_id: str,
        workspace_id: str,
        author: str = "system",
    ) -> str:
        """
        Rollback a model to a previous version.

        Non-destructive: creates a NEW version row whose snapshot
        is a copy of the target version's snapshot. The new row is
        clearly flagged as a rollback and references the source version.

        Args:
            model_id: The model to roll back.
            target_version_id: The version to restore.
            workspace_id: Current workspace context.
            author: Who initiated the rollback.

        Returns:
            The new version_id created by the rollback.
        """
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
            f"Rolled back model={model_id} to version {target_tag} "
            f"→ new version {new_version_id[:8]}..."
        )
        return new_version_id
