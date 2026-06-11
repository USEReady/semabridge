"""
Sync Domain Models.

Pydantic v2 models defining the core data structures for the
bidirectional synchronization engine. These models are persisted
in DuckDB as JSON columns and used for API request/response schemas.

Design Decisions:
- All IDs are UUIDs generated at creation time.
- Timestamps use UTC ISO-8601 strings for DuckDB JSON compatibility.
- Enums use lowercase string values for CLI/API ergonomics.
- Models are strict (extra fields forbidden) to catch schema drift early.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# =============================================================================
# Enumerations
# =============================================================================


class SyncDirection(str, Enum):
    """Direction of a synchronization job."""

    PBIX_TO_SNOWFLAKE = "pbix_to_snowflake"
    SNOWFLAKE_TO_PBI = "snowflake_to_pbi"
    BIDIRECTIONAL = "bidirectional"
    # ── Fabric ↔ Snowflake semantic model sync ─────────────────────────
    FABRIC_TO_SNOWFLAKE = "fabric_to_snowflake"
    SNOWFLAKE_TO_FABRIC = "snowflake_to_fabric"
    FABRIC_SNOWFLAKE_BIDIRECTIONAL = "fabric_snowflake_bidirectional"


class SyncJobStatus(str, Enum):
    """Lifecycle status of a sync job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"  # Paused awaiting conflict resolution


class SyncItemStatus(str, Enum):
    """Status of an individual item within a sync job."""

    QUEUED = "queued"
    EXTRACTING = "extracting"
    CONVERTING = "converting"
    DEPLOYING = "deploying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ConflictResolution(str, Enum):
    """Strategy for resolving schema/data conflicts."""

    FAIL_AND_APPROVE = "fail_and_approve"  # Default: pause and require manual approval
    SOURCE_WINS = "source_wins"            # Source overwrites target
    TARGET_WINS = "target_wins"            # Target kept, source discarded
    MERGE = "merge"                        # Attempt structural merge


class SchemaChangeType(str, Enum):
    """Type of schema evolution event."""

    COLUMN_ADDED = "column_added"
    COLUMN_REMOVED = "column_removed"
    COLUMN_TYPE_CHANGED = "column_type_changed"
    TABLE_ADDED = "table_added"
    TABLE_REMOVED = "table_removed"
    MEASURE_ADDED = "measure_added"
    MEASURE_REMOVED = "measure_removed"
    MEASURE_MODIFIED = "measure_modified"
    RELATIONSHIP_ADDED = "relationship_added"
    RELATIONSHIP_REMOVED = "relationship_removed"
    RELATIONSHIP_MODIFIED = "relationship_modified"


class ConflictSeverity(str, Enum):
    """Severity level of a detected conflict."""

    INFO = "info"          # Non-breaking; can auto-resolve
    WARNING = "warning"    # Potentially breaking; review recommended
    CRITICAL = "critical"  # Breaking change; blocks sync


# =============================================================================
# Utility
# =============================================================================


def _utc_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


# =============================================================================
# Sync Job & Items
# =============================================================================


class SyncJobItem(BaseModel):
    """
    A single model being synchronized within a SyncJob.

    Each item tracks its own lifecycle through the extraction→conversion→deploy
    pipeline independently, enabling partial success within a batch job.
    """

    item_id: str = Field(default_factory=_new_id, description="Unique item identifier")
    job_id: str = Field(..., description="Parent sync job ID")
    model_name: str = Field(..., description="Source model/PBIX filename")
    source_path: Optional[str] = Field(default=None, description="Source file path or artifact ID")
    status: SyncItemStatus = Field(default=SyncItemStatus.QUEUED, description="Current status")
    started_at: Optional[str] = Field(default=None, description="Processing start time")
    completed_at: Optional[str] = Field(default=None, description="Processing end time")
    error_message: Optional[str] = Field(default=None, description="Error details if failed")
    osi_snapshot: Optional[Dict[str, Any]] = Field(
        default=None, description="OSI model snapshot after conversion"
    )
    target_artifact_id: Optional[str] = Field(
        default=None, description="ID of artifact created in target system"
    )
    duration_ms: Optional[int] = Field(default=None, description="Processing duration")

    model_config = {"extra": "forbid"}


class SyncJob(BaseModel):
    """
    Top-level synchronization job.

    A job represents one batch of models being synced in a single direction.
    It contains multiple SyncJobItems and tracks aggregate status.
    """

    job_id: str = Field(default_factory=_new_id, description="Unique job identifier")
    direction: SyncDirection = Field(..., description="Sync direction")
    status: SyncJobStatus = Field(default=SyncJobStatus.PENDING, description="Job status")
    conflict_resolution: ConflictResolution = Field(
        default=ConflictResolution.FAIL_AND_APPROVE,
        description="Conflict handling strategy",
    )
    created_at: str = Field(default_factory=_utc_now, description="Job creation time")
    started_at: Optional[str] = Field(default=None, description="Execution start time")
    completed_at: Optional[str] = Field(default=None, description="Execution end time")
    initiated_by: str = Field(default="cli", description="Initiator (cli, api, scheduler)")
    source_folder: Optional[str] = Field(default=None, description="Source folder path (PBIX)")
    source_connection: Optional[str] = Field(
        default=None, description="Source connection string (Snowflake)"
    )
    target_workspace_id: Optional[str] = Field(
        default=None, description="Target Fabric workspace ID"
    )
    target_snowflake_schema: Optional[str] = Field(
        default=None, description="Target Snowflake schema"
    )
    items: List[SyncJobItem] = Field(default_factory=list, description="Items in this job")
    total_items: int = Field(default=0, description="Total item count")
    completed_items: int = Field(default=0, description="Successfully completed items")
    failed_items: int = Field(default=0, description="Failed items")
    duration_ms: Optional[int] = Field(default=None, description="Total job duration")
    error_message: Optional[str] = Field(default=None, description="Job-level error")

    model_config = {"extra": "forbid"}

    def update_counts(self) -> None:
        """Recalculate item counts from the items list."""
        self.total_items = len(self.items)
        self.completed_items = sum(
            1 for i in self.items if i.status == SyncItemStatus.COMPLETED
        )
        self.failed_items = sum(
            1 for i in self.items if i.status == SyncItemStatus.FAILED
        )


# =============================================================================
# Model Mapping (PBIX ↔ Snowflake ↔ Power BI)
# =============================================================================


class ModelMapping(BaseModel):
    """
    Maps a source model to its target counterparts.

    Maintains the relationship between a PBIX file and its deployed
    Snowflake schema / Power BI dataset so incremental sync can detect
    which targets need updating.
    """

    mapping_id: str = Field(default_factory=_new_id, description="Unique mapping ID")
    source_type: str = Field(
        ...,
        description=(
            "Source platform: 'pbix', 'snowflake', 'fabric', "
            "'snowflake_semantic_view'"
        ),
    )
    source_identifier: str = Field(..., description="Source path or artifact ID")
    target_type: str = Field(
        ...,
        description=(
            "Target platform: 'snowflake', 'fabric', "
            "'snowflake_semantic_view'"
        ),
    )
    target_identifier: str = Field(
        ..., description="Target schema, dataset ID, or semantic view name"
    )
    model_name: str = Field(..., description="Canonical model name")
    last_synced_at: Optional[str] = Field(default=None, description="Last successful sync")
    last_osi_hash: Optional[str] = Field(
        default=None, description="Hash of last synced OSI model for change detection"
    )
    # Semantic-model sync metadata
    fabric_model_id: Optional[str] = Field(
        default=None, description="Fabric semantic model GUID (for Fabric↔Snowflake)"
    )
    snowflake_semantic_view: Optional[str] = Field(
        default=None,
        description="Fully-qualified Snowflake semantic view name mapped to this model",
    )
    created_at: str = Field(default_factory=_utc_now, description="Mapping creation time")
    is_active: bool = Field(default=True, description="Whether mapping is active")

    model_config = {"extra": "forbid"}


# =============================================================================
# Schema Evolution
# =============================================================================


class SchemaVersion(BaseModel):
    """
    A versioned snapshot of a model's schema for evolution tracking.

    Each time a model is synced, its schema fingerprint is stored so that
    subsequent syncs can detect drift (columns added/removed, type changes).
    """

    version_id: str = Field(default_factory=_new_id, description="Unique version ID")
    model_name: str = Field(..., description="Model this version belongs to")
    version_number: int = Field(..., description="Monotonically increasing version")
    schema_hash: str = Field(..., description="SHA-256 of canonical schema JSON")
    schema_snapshot: Dict[str, Any] = Field(
        ..., description="Full schema as JSON (datasets, columns, types)"
    )
    changes_from_previous: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of SchemaChangeType entries from previous version",
    )
    created_at: str = Field(default_factory=_utc_now, description="Version creation time")
    created_by_job_id: Optional[str] = Field(
        default=None, description="Sync job that created this version"
    )

    model_config = {"extra": "forbid"}


# =============================================================================
# Conflicts
# =============================================================================


class SyncConflict(BaseModel):
    """
    A detected conflict during synchronization.

    When fail_and_approve strategy is active, conflicts are persisted
    and the sync job pauses until all conflicts are resolved.
    """

    conflict_id: str = Field(default_factory=_new_id, description="Unique conflict ID")
    job_id: str = Field(..., description="Sync job that detected this conflict")
    item_id: Optional[str] = Field(default=None, description="Specific item involved")
    model_name: str = Field(..., description="Model with the conflict")
    change_type: SchemaChangeType = Field(..., description="Type of schema change")
    severity: ConflictSeverity = Field(..., description="Conflict severity")
    description: str = Field(..., description="Human-readable conflict description")
    source_value: Optional[Dict[str, Any]] = Field(
        default=None, description="Value from source"
    )
    target_value: Optional[Dict[str, Any]] = Field(
        default=None, description="Value currently in target"
    )
    resolution: Optional[ConflictResolution] = Field(
        default=None, description="How conflict was resolved (None if unresolved)"
    )
    resolved_at: Optional[str] = Field(default=None, description="Resolution timestamp")
    resolved_by: Optional[str] = Field(default=None, description="Who resolved it")
    created_at: str = Field(default_factory=_utc_now, description="Detection time")

    model_config = {"extra": "forbid"}

    @property
    def is_resolved(self) -> bool:
        """Check if conflict has been resolved."""
        return self.resolution is not None


# =============================================================================
# Checkpoints (for resumable sync)
# =============================================================================


class SyncCheckpoint(BaseModel):
    """
    Progress checkpoint for resumable synchronization.

    Allows a failed sync job to restart from the last successfully
    processed item rather than re-processing everything.
    """

    checkpoint_id: str = Field(default_factory=_new_id, description="Unique checkpoint ID")
    job_id: str = Field(..., description="Sync job this checkpoint belongs to")
    last_processed_item_id: str = Field(..., description="Last successfully processed item")
    last_processed_index: int = Field(..., description="Index of last processed item")
    state_snapshot: Dict[str, Any] = Field(
        default_factory=dict, description="Serialised orchestrator state"
    )
    created_at: str = Field(default_factory=_utc_now, description="Checkpoint time")

    model_config = {"extra": "forbid"}


# =============================================================================
# Sync Configuration
# =============================================================================


class SyncConfig(BaseModel):
    """
    Runtime configuration for a sync operation.

    Combines direction, paths, credentials references, and tuning
    knobs into a single validated structure.
    """

    direction: SyncDirection = Field(..., description="Sync direction")
    conflict_resolution: ConflictResolution = Field(
        default=ConflictResolution.FAIL_AND_APPROVE,
        description="Conflict handling strategy",
    )

    # PBIX source/target
    pbix_folder: Optional[str] = Field(default=None, description="Folder with PBIX files")
    pbix_pattern: str = Field(default="*.pbix", description="Glob pattern for PBIX files")
    source_path: Optional[str] = Field(
        default=None,
        description="Single PBIX file path for PBIX_TO_SNOWFLAKE runs",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Alias for source_path",
    )

    # Snowflake target/source
    snowflake_database: Optional[str] = Field(default=None, description="Target Snowflake DB")
    snowflake_schema: Optional[str] = Field(default=None, description="Target Snowflake schema")
    target_snowflake_schema: Optional[str] = Field(
        default=None,
        description="Alias for snowflake_schema used by external callers",
    )

    # Power BI / Fabric target
    fabric_workspace_id: Optional[str] = Field(
        default=None, description="Target Fabric workspace ID"
    )
    # Fabric↔Snowflake: filter which Fabric models to sync (None = all)
    fabric_model_names: Optional[List[str]] = Field(
        default=None,
        description="Fabric semantic model display-names to include (None = all)",
    )
    # Fabric↔Snowflake: filter which Snowflake semantic views to sync
    snowflake_semantic_views: Optional[List[str]] = Field(
        default=None,
        description="Snowflake semantic view names to include (None = all)",
    )

    # Parallel execution
    max_workers: int = Field(default=5, ge=1, le=32, description="Worker thread count")
    enable_parallel: bool = Field(default=True, description="Enable parallel model processing")

    # Incremental sync
    incremental: bool = Field(
        default=True,
        description="Skip models whose OSI hash hasn't changed since last sync",
    )

    # Data tier
    include_data: bool = Field(
        default=False,
        description="Extract and sync table data (best-effort, only for small tables)",
    )
    max_data_rows: int = Field(
        default=10_000,
        ge=0,
        description="Maximum rows to extract per table when include_data is True",
    )

    # CSM Pipeline Architecture
    use_csm: bool = Field(
        default=False,
        description="Use the new Canonical Semantic Model (CSM) intermediate layer",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def normalize_aliases_and_requirements(self) -> "SyncConfig":
        """Normalize legacy/new aliases and validate direction-specific requirements."""
        if self.file_path and not self.source_path:
            self.source_path = self.file_path
        if self.target_snowflake_schema and not self.snowflake_schema:
            self.snowflake_schema = self.target_snowflake_schema

        if self.direction == SyncDirection.PBIX_TO_SNOWFLAKE:
            if not (self.source_path or self.pbix_folder):
                raise ValueError(
                    "PBIX_TO_SNOWFLAKE requires either source_path/file_path or pbix_folder"
                )
        return self
