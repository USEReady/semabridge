"""
Pydantic request/response models for the semantic synchronization API.

These models cover three new endpoints:
- GET  /api/discovery/semantic
- POST /api/semantic/sync
- POST /api/semantic/refresh
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from semabridge.sync.models import ConflictResolution, SyncDirection


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class SemanticModelItem(BaseModel):
    """A single semantic model / view returned by discovery."""

    id: str = Field(..., description="Unique identifier (model GUID or view name)")
    name: str = Field(..., description="Display name")
    type: str = Field(..., description="'semantic_model' (Fabric) or 'semantic_view' (Snowflake)")
    platform: str = Field(..., description="'fabric' or 'snowflake'")
    description: Optional[str] = Field(default=None)
    status: str = Field(default="Available")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Platform-specific metadata")


class SemanticMappingItem(BaseModel):
    """Cross-platform mapping entry showing sync status between platforms."""

    fabric_name: Optional[str] = Field(default=None, description="Fabric model display name")
    fabric_id: Optional[str] = Field(default=None, description="Fabric model GUID")
    snowflake_view: Optional[str] = Field(
        default=None, description="Matching Snowflake semantic view name"
    )
    last_synced: Optional[str] = Field(default=None, description="Last sync timestamp (UTC)")
    in_sync: bool = Field(
        default=False,
        description="True when last_osi_hash matches on both sides",
    )
    osi_hash: Optional[str] = Field(default=None, description="OSI content hash")


class SemanticDiscoveryResponse(BaseModel):
    """Response for GET /api/discovery/semantic."""

    fabric: List[SemanticModelItem] = Field(
        default_factory=list, description="Fabric semantic models"
    )
    snowflake: List[SemanticModelItem] = Field(
        default_factory=list, description="Snowflake semantic views"
    )
    mappings: List[SemanticMappingItem] = Field(
        default_factory=list, description="Cross-platform mappings with sync status"
    )
    fabric_error: Optional[str] = Field(
        default=None, description="Error message if Fabric discovery failed"
    )
    snowflake_error: Optional[str] = Field(
        default=None, description="Error message if Snowflake discovery failed"
    )


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class SemanticSyncRequest(BaseModel):
    """Request body for POST /api/semantic/sync."""

    direction: SyncDirection = Field(
        ...,
        description=(
            "Sync direction. Use 'fabric_to_snowflake', 'snowflake_to_fabric', "
            "or 'fabric_snowflake_bidirectional'."
        ),
    )
    fabric_workspace_id: Optional[str] = Field(
        default=None, description="Override Fabric workspace ID"
    )
    snowflake_schema: Optional[str] = Field(
        default=None, description="Override Snowflake schema"
    )
    fabric_model_names: Optional[List[str]] = Field(
        default=None,
        description="Filter: only sync these Fabric model display names (None = all)",
    )
    snowflake_semantic_views: Optional[List[str]] = Field(
        default=None,
        description="Filter: only sync these Snowflake semantic view names (None = all)",
    )
    conflict_resolution: ConflictResolution = Field(
        default=ConflictResolution.FAIL_AND_APPROVE,
        description="How to handle schema conflicts",
    )
    max_workers: int = Field(default=5, ge=1, le=32)
    incremental: bool = Field(
        default=True, description="Skip models with unchanged OSI hash"
    )

    model_config = {"extra": "forbid"}


class SemanticSyncResponse(BaseModel):
    """Response for POST /api/semantic/sync."""

    job_id: str
    direction: str
    status: str
    total_items: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class SemanticRefreshRequest(BaseModel):
    """Request body for POST /api/semantic/refresh."""

    fabric_workspace_id: Optional[str] = Field(default=None)
    snowflake_schema: Optional[str] = Field(default=None)
    fabric_model_names: Optional[List[str]] = Field(default=None)
    snowflake_semantic_views: Optional[List[str]] = Field(default=None)

    model_config = {"extra": "forbid"}


class SemanticRefreshSummary(BaseModel):
    """Summary of changes detected during metadata refresh."""

    model_name: str
    platform: str
    added_datasets: List[str] = Field(default_factory=list)
    removed_datasets: List[str] = Field(default_factory=list)
    added_metrics: List[str] = Field(default_factory=list)
    removed_metrics: List[str] = Field(default_factory=list)
    added_relationships: List[str] = Field(default_factory=list)
    removed_relationships: List[str] = Field(default_factory=list)
    osi_hash: Optional[str] = None


class SemanticRefreshResponse(BaseModel):
    """Response for POST /api/semantic/refresh."""

    refreshed_fabric: int = 0
    refreshed_snowflake: int = 0
    summaries: List[SemanticRefreshSummary] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
