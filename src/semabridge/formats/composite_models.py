"""
Composite Model Resolution.

Handles the many-to-many relationship between reports and semantic models
in composite Power BI models. A single .pbix report can establish Live
Connections or DirectQuery links to multiple upstream semantic models.

Architecture:
    - Parses Connections.json from the PBIX connector output.
    - Maintains a relational schema in DuckDB:
        reports → report_model_links → semantic_models
    - Enables impact analysis: if an upstream model changes, which reports break?
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class CompositeModelResolver:
    """Track and resolve composite model dependencies.

    Creates bridging tables in DuckDB to map the many-to-many relationship
    between reports and their underlying semantic models.

    Args:
        db_manager: DuckDBManager instance for database operations.
    """

    def __init__(self, db_manager: Any) -> None:
        self._db = db_manager
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the composite model schema tables if they don't exist."""
        conn = self._db._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    report_id VARCHAR PRIMARY KEY,
                    report_name VARCHAR NOT NULL,
                    source_path VARCHAR,
                    workspace_id VARCHAR,
                    last_synced TIMESTAMP,
                    model_count INTEGER DEFAULT 0,
                    metadata JSON
                );

                CREATE TABLE IF NOT EXISTS semantic_models_registry (
                    model_guid VARCHAR PRIMARY KEY,
                    model_name VARCHAR,
                    workspace_id VARCHAR,
                    tenant_id VARCHAR,
                    connection_type VARCHAR,
                    last_seen TIMESTAMP,
                    metadata JSON
                );

                CREATE TABLE IF NOT EXISTS report_model_links (
                    link_id VARCHAR PRIMARY KEY,
                    report_id VARCHAR NOT NULL,
                    model_guid VARCHAR NOT NULL,
                    connection_string VARCHAR,
                    link_type VARCHAR DEFAULT 'live_connect',
                    created_at TIMESTAMP DEFAULT current_timestamp,
                    FOREIGN KEY (report_id) REFERENCES reports(report_id),
                    FOREIGN KEY (model_guid) REFERENCES semantic_models_registry(model_guid)
                );
            """)
            logger.info("Composite model schema initialized")
        finally:
            conn.close()

    def register_report(
        self,
        report_name: str,
        source_path: str,
        workspace_id: str = "local",
        connections: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Register a report and its composite model connections.

        Args:
            report_name: Human-readable report name.
            source_path: Path to the source .pbix file or Fabric URI.
            workspace_id: The workspace owning this report.
            connections: List of external connection definitions from PBIX parsing.
            metadata: Additional report metadata.

        Returns:
            The generated report_id.
        """
        report_id = str(uuid.uuid4())
        now = datetime.utcnow()
        connections = connections or []

        conn = self._db._get_connection()
        try:
            conn.begin()

            # 1. Insert or update the report
            conn.execute("""
                INSERT INTO reports (report_id, report_name, source_path,
                                     workspace_id, last_synced, model_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (report_id) DO UPDATE SET
                    report_name = excluded.report_name,
                    source_path = excluded.source_path,
                    last_synced = excluded.last_synced,
                    model_count = excluded.model_count
            """, [
                report_id, report_name, source_path,
                workspace_id, now, len(connections),
                json.dumps(metadata) if metadata else None,
            ])

            # 2. Register each upstream semantic model and create links
            for ext_conn in connections:
                model_guid = ext_conn.get("external_model_id")
                if not model_guid:
                    logger.warning(
                        f"Skipping connection with no model GUID: {ext_conn.get('name', '?')}"
                    )
                    continue

                # Upsert the semantic model reference
                conn.execute("""
                    INSERT INTO semantic_models_registry
                        (model_guid, model_name, connection_type, last_seen)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (model_guid) DO UPDATE SET
                        last_seen = excluded.last_seen
                """, [
                    model_guid,
                    ext_conn.get("name", ""),
                    ext_conn.get("type", "unknown"),
                    now,
                ])

                # Create the bridging link
                link_id = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO report_model_links
                        (link_id, report_id, model_guid, connection_string, link_type)
                    VALUES (?, ?, ?, ?, ?)
                """, [
                    link_id, report_id, model_guid,
                    ext_conn.get("connection_string", ""),
                    ext_conn.get("type", "live_connect"),
                ])

            conn.commit()
            logger.info(
                f"Registered report '{report_name}' with "
                f"{len(connections)} upstream model link(s)"
            )
            return report_id

        except Exception as exc:
            conn.rollback()
            logger.error(f"Failed to register report: {exc}")
            raise
        finally:
            conn.close()

    def get_report_dependencies(self, report_id: str) -> List[Dict[str, Any]]:
        """Get all semantic models linked to a report.

        Args:
            report_id: The report to query.

        Returns:
            List of upstream semantic model records.
        """
        conn = self._db._get_connection()
        try:
            rows = conn.execute("""
                SELECT sm.model_guid, sm.model_name, sm.connection_type,
                       rml.link_type, rml.connection_string
                FROM report_model_links rml
                JOIN semantic_models_registry sm ON sm.model_guid = rml.model_guid
                WHERE rml.report_id = ?
            """, [report_id]).fetchall()

            return [
                {
                    "model_guid": r[0],
                    "model_name": r[1],
                    "connection_type": r[2],
                    "link_type": r[3],
                    "connection_string": r[4],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_impacted_reports(self, model_guid: str) -> List[Dict[str, Any]]:
        """Impact analysis: find all reports that depend on a semantic model.

        If an upstream semantic model is modified (e.g., a field is renamed
        or removed), this method identifies which downstream reports are
        at risk of breaking.

        Args:
            model_guid: The semantic model GUID to check for dependents.

        Returns:
            List of report records that depend on this model.
        """
        conn = self._db._get_connection()
        try:
            rows = conn.execute("""
                SELECT r.report_id, r.report_name, r.source_path,
                       r.workspace_id, r.last_synced, r.model_count
                FROM reports r
                JOIN report_model_links rml ON rml.report_id = r.report_id
                WHERE rml.model_guid = ?
            """, [model_guid]).fetchall()

            return [
                {
                    "report_id": r[0],
                    "report_name": r[1],
                    "source_path": r[2],
                    "workspace_id": r[3],
                    "last_synced": str(r[4]),
                    "model_count": r[5],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def list_all_links(self) -> List[Dict[str, Any]]:
        """List all report → model dependency links.

        Returns:
            Full join of reports, links, and semantic models.
        """
        conn = self._db._get_connection()
        try:
            rows = conn.execute("""
                SELECT r.report_name, sm.model_name, sm.model_guid,
                       rml.link_type, r.workspace_id
                FROM report_model_links rml
                JOIN reports r ON r.report_id = rml.report_id
                JOIN semantic_models_registry sm ON sm.model_guid = rml.model_guid
                ORDER BY r.report_name
            """).fetchall()

            return [
                {
                    "report_name": r[0],
                    "model_name": r[1],
                    "model_guid": r[2],
                    "link_type": r[3],
                    "workspace_id": r[4],
                }
                for r in rows
            ]
        finally:
            conn.close()
