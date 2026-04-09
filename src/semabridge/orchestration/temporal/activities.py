"""
Temporal Activity Implementations for Semabridge.

Activities are the *side-effectful* units of work invoked by workflows.
Each activity is:
    • Idempotent – safe to retry without duplication.
    • Explicit  – receives all context via dataclass inputs (no globals).
    • Timeout-aware – designed for the timeout budgets defined in workflows.py.

The four core activities mirror the legacy SyncOrchestrator phases:
    1. extract_metadata   → query Snowflake via SnowflakeExtractor
    2. compute_diff       → compute SML delta (delegates to Ray when available)
    3. emit_to_fabric     → deploy TMSL via FabricPublisher (handles 429)
    4. persist_snapshot    → store snapshot in the repository ORM
"""

from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from temporalio import activity

from semabridge.connectors.fabric_publisher import FabricPublisher, PublishError
from semabridge.core.settings import FabricConfig


# ---------------------------------------------------------------------------
# Activity I/O contracts
# ---------------------------------------------------------------------------


@dataclass
class ExtractInput:
    tenant_id: str
    model_id: str
    database: str
    schema: str
    snowflake_account: str
    warehouse_size: str = "SMALL"


@dataclass
class ExtractOutput:
    sml_json: str = ""
    table_count: int = 0
    relationship_count: int = 0
    duration_ms: int = 0
    error: str = ""


@dataclass
class DiffInput:
    tenant_id: str
    model_id: str
    current_sml_json: str
    force_full: bool = False


@dataclass
class DiffOutput:
    diff_json: str = ""
    total_changes: int = 0
    added: int = 0
    modified: int = 0
    removed: int = 0
    duration_ms: int = 0
    error: str = ""


@dataclass
class EmitInput:
    tenant_id: str
    model_id: str
    sml_json: str
    diff_json: str = ""
    fabric_workspace_id: str = ""
    version_tag: str = ""


@dataclass
class EmitOutput:
    deployed: bool = False
    fabric_model_id: str = ""
    duration_ms: int = 0
    retry_after: int = 0
    error: str = ""


@dataclass
class SnapshotInput:
    tenant_id: str
    model_id: str
    sml_json: str
    version_tag: str = ""
    run_id: str = ""
    status: str = "success"


@dataclass
class SnapshotOutput:
    snapshot_id: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# 1. Extract metadata from Snowflake
# ---------------------------------------------------------------------------


@activity.defn(name="extract_metadata")
async def extract_metadata(inp: ExtractInput) -> ExtractOutput:
    """
    Query Snowflake INFORMATION_SCHEMA for a single database/schema
    and return the extracted SML model as a JSON string.
    """
    start = time.monotonic()
    try:
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.core.settings import SnowflakeConfig
        from semabridge.formats.sml.assembler import SMLAssembler
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.connectors.hierarchy_detector import HierarchyDetector

        activity.heartbeat(f"Connecting to Snowflake: {inp.snowflake_account}")

        config = SnowflakeConfig(
            account=inp.snowflake_account,
            database=inp.database,
            schema_name=inp.schema,
            warehouse_size=inp.warehouse_size,
        )
        extractor = SnowflakeExtractor(config=config)

        activity.heartbeat("Extracting metadata")
        # extract_all handles the connection internal context
        metadata = extractor.extract_all()

        activity.heartbeat("Assembling SML model")
        assembler = SMLAssembler(
            model_name=inp.model_id,
            description=f"Auto-generated model for {inp.model_id}",
            source_database=inp.database,
            source_schema=inp.schema,
            normalize_names=False,
        )

        # Add tables
        for table_name, table_info in metadata.get("tables", {}).items():
            columns = metadata.get("columns", {}).get(table_name, [])
            assembler.add_table(
                table_name=table_name,
                columns=columns,
                description=table_info.get("description", ""),
                row_count=table_info.get("row_count"),
            )

        # Detect relationships
        rel_detector = RelationshipDetector(
            tables=metadata.get("tables", {}),
            columns=metadata.get("columns", {}),
            primary_keys=metadata.get("primary_keys", {}),
            explicit_fks=metadata.get("foreign_keys", []),
            include_inferred=False,
        )
        relationships = rel_detector.detect_all()
        for rel in relationships:
            from_table = str(
                rel.get("from_table")
                or rel.get("source_table")
                or rel.get("left_table")
                or ""
            ).strip()
            from_column = str(
                rel.get("from_column")
                or rel.get("source_column")
                or rel.get("left_column")
                or ""
            ).strip()
            to_table = str(
                rel.get("to_table")
                or rel.get("target_table")
                or rel.get("right_table")
                or ""
            ).strip()
            to_column = str(
                rel.get("to_column")
                or rel.get("target_column")
                or rel.get("right_column")
                or ""
            ).strip()
            rel_name = str(rel.get("name") or "").strip() or f"REL_{from_table}_{to_table}"

            if not (from_table and from_column and to_table and to_column):
                logger.warning("Skipping malformed relationship during temporal conversion: %s", rel)
                continue

            assembler.add_relationship(
                name=rel_name,
                from_table=from_table,
                from_column=from_column,
                to_table=to_table,
                to_column=to_column,
            )

        # Detect hierarchies
        hier_detector = HierarchyDetector(
            tables=metadata.get("tables", {}),
            columns=metadata.get("columns", {}),
        )
        hierarchies = hier_detector.detect_all()
        for table_name, table_hierarchies in hierarchies.items():
            attributes = [
                {"name": col["name"], "column": col["name"]}
                for col in metadata.get("columns", {}).get(table_name, [])
            ]
            for h in table_hierarchies:
                assembler.add_dimension(
                    name=h["name"],
                    dataset=table_name,
                    attributes=attributes,
                    hierarchies=[h],
                )

        # Strict Snowflake policy: do not auto-generate heuristic measures

        sml_model = assembler.build()
        elapsed = int((time.monotonic() - start) * 1000)

        return ExtractOutput(
            sml_json=sml_model.model_dump_json(),
            table_count=sml_model.dataset_count,
            relationship_count=sml_model.relationship_count,
            duration_ms=elapsed,
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return ExtractOutput(
            duration_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# 2. Compute incremental diff (delegates to Ray when available)
# ---------------------------------------------------------------------------


@activity.defn(name="compute_diff")
async def compute_diff(inp: DiffInput) -> DiffOutput:
    """
    Compare the current extracted SML against the last persisted
    baseline snapshot and return a structured diff.

    When Ray is available the diff is dispatched to a remote actor;
    otherwise falls back to the local SemanticDiffEngine.
    """
    start = time.monotonic()
    try:
        activity.heartbeat("Loading previous snapshot")

        from semabridge.storage.orm import get_session, SnapshotRow
        from semabridge.repository.semantic_diff_engine import SemanticDiffEngine

        current = json.loads(inp.current_sml_json)

        # Fetch previous baseline
        previous_json: Optional[str] = None
        with get_session() as session:
            row = (
                session.query(SnapshotRow)
                .filter_by(
                    tenant_id=inp.tenant_id,
                    model_id=inp.model_id,
                    status="success",
                )
                .order_by(SnapshotRow.timestamp.desc())
                .first()
            )
            if row and row.sml_blob:
                previous_json = row.sml_blob

        if previous_json is None or inp.force_full:
            # No baseline → treat everything as new
            elapsed = int((time.monotonic() - start) * 1000)
            return DiffOutput(
                diff_json="{}",
                total_changes=-1,  # sentinel: full sync
                duration_ms=elapsed,
            )

        activity.heartbeat("Computing diff")

        # Attempt Ray-accelerated diff
        try:
            from semabridge.distributed.ray.coordinator import submit_diff
            diff_result = await submit_diff(current, json.loads(previous_json))
        except ImportError:
            # Ray not installed – local fallback
            diff_engine = SemanticDiffEngine()
            diff_result = diff_engine.compute_diff_dicts(
                json.loads(previous_json), current
            )

        total = (
            diff_result.get("added", 0)
            + diff_result.get("modified", 0)
            + diff_result.get("removed", 0)
        )
        elapsed = int((time.monotonic() - start) * 1000)
        return DiffOutput(
            diff_json=json.dumps(diff_result, default=str),
            total_changes=total,
            added=diff_result.get("added", 0),
            modified=diff_result.get("modified", 0),
            removed=diff_result.get("removed", 0),
            duration_ms=elapsed,
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return DiffOutput(
            duration_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )


# ---------------------------------------------------------------------------
# 3. Emit to Fabric (handles 429 via Temporal retry)
# ---------------------------------------------------------------------------


class FabricThrottledError(Exception):
    """Raised when Fabric returns 429 so Temporal can back off."""

    def __init__(self, retry_after: int, message: str = ""):
        self.retry_after = retry_after
        super().__init__(message or f"Fabric throttled – retry after {retry_after}s")


class PermanentDeploymentError(Exception):
    """Non-retryable deployment error (bad model, auth revoked, etc.)."""
    pass


@activity.defn(name="emit_to_fabric")
async def emit_to_fabric(inp: EmitInput) -> EmitOutput:
    """
    Convert the SML JSON into a TMSL .bim payload and publish it
    to the specified Fabric workspace.

    On HTTP 429 responses the activity raises ``FabricThrottledError``
    which Temporal retries after the ``Retry-After`` period without
    holding the worker slot.
    """
    start = time.monotonic()
    try:
        activity.heartbeat("Preparing TMSL payload")

        config = FabricConfig(workspace_id=inp.fabric_workspace_id)
        publisher = FabricPublisher(config=config)

        from semabridge.formats.sml.models import SMLModel
        sml_model = SMLModel.model_validate_json(inp.sml_json)

        activity.heartbeat("Publishing to Fabric")

        try:
            result = publisher.publish(sml_model, model_name=inp.model_id)
        except PublishError as pe:
            error_msg = str(pe)
            # Detect 429 throttling
            if "429" in error_msg or "Too Many Requests" in error_msg:
                # Try to parse Retry-After from error context
                retry_after = _parse_retry_after(error_msg)
                raise FabricThrottledError(
                    retry_after=retry_after,
                    message=f"Fabric 429 for {inp.model_id}: retry in {retry_after}s",
                )
            # Permanent errors (auth, bad model)
            if any(k in error_msg.lower() for k in ("unauthorized", "forbidden", "bad request")):
                raise PermanentDeploymentError(error_msg)
            raise  # transient – let Temporal retry

        elapsed = int((time.monotonic() - start) * 1000)
        return EmitOutput(
            deployed=True,
            fabric_model_id=result.get("id", "") if isinstance(result, dict) else "",
            duration_ms=elapsed,
        )
    except (FabricThrottledError, PermanentDeploymentError):
        raise  # propagate typed errors to Temporal
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return EmitOutput(
            duration_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


def _parse_retry_after(msg: str) -> int:
    """Best-effort extraction of Retry-After seconds from error text."""
    import re
    match = re.search(r"[Rr]etry-?[Aa]fter[:\s]+(\d+)", msg)
    if match:
        return int(match.group(1))
    return 30  # conservative default


# ---------------------------------------------------------------------------
# 4. Persist snapshot to the repository
# ---------------------------------------------------------------------------


@activity.defn(name="persist_snapshot")
async def persist_snapshot(inp: SnapshotInput) -> SnapshotOutput:
    """
    Write the current SML state as a new snapshot row in the ORM-backed
    repository (DuckDB by default, PostgreSQL when configured).
    """
    try:
        import uuid
        from datetime import datetime, timezone

        from semabridge.storage.orm import get_session, SnapshotRow

        snapshot_id = str(uuid.uuid4())[:12]

        with get_session() as session:
            row = SnapshotRow(
                snapshot_id=snapshot_id,
                tenant_id=inp.tenant_id,
                model_id=inp.model_id,
                timestamp=datetime.now(timezone.utc),
                version_tag=inp.version_tag or None,
                sml_blob=inp.sml_json,
                status=inp.status,
                run_id=inp.run_id or None,
            )
            session.add(row)
            session.commit()

        return SnapshotOutput(snapshot_id=snapshot_id)
    except Exception as exc:
        return SnapshotOutput(error=f"{type(exc).__name__}: {exc}")
