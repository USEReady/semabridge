"""
Sync API Router.

FastAPI endpoints for model mappings and schema evolution.

Endpoints:
    GET    /sync/mappings      — List model mappings
    GET    /sync/schema/{model}/history — Schema version history
    POST   /sync/schema/add-missing-column — Auto-add missing dimension column
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from semabridge.sync.models import ConflictResolution, SyncDirection
from semabridge.sync.repository import SyncRepository

logger = logging.getLogger("semabridge.api.sync")

router = APIRouter(prefix="/sync", tags=["sync"])

# Lazy-initialised singleton
_repo: Optional[SyncRepository] = None


def _get_repo() -> SyncRepository:
    global _repo
    if _repo is None:
        _repo = SyncRepository()
    return _repo


class StartSyncRequest(BaseModel):
    """Request to start a new sync job."""

    direction: SyncDirection
    conflict_resolution: ConflictResolution = ConflictResolution.FAIL_AND_APPROVE
    pbix_folder: Optional[str] = None
    source_path: Optional[str] = None
    file_path: Optional[str] = None
    pbix_pattern: str = "*.pbix"
    snowflake_database: Optional[str] = None
    snowflake_schema: Optional[str] = None
    target_snowflake_schema: Optional[str] = None
    fabric_workspace_id: Optional[str] = None
    max_workers: int = Field(default=5, ge=1, le=32)
    enable_parallel: bool = True
    incremental: bool = True
    include_data: bool = False
    max_data_rows: int = Field(default=10_000, ge=0)


def _gone_response(removed_path: str, replacement: str) -> JSONResponse:
    """Return 410 Gone with a body pointing to the replacement endpoint."""
    return JSONResponse(
        status_code=410,
        content={
            "detail": f"Endpoint {removed_path} has been removed. Use {replacement} instead.",
            "replacement": replacement,
        },
    )


@router.post("/jobs")
@router.get("/jobs")
def gone_sync_jobs() -> JSONResponse:
    return _gone_response("/sync/jobs", "/api/sync")


@router.get("/jobs/{job_id}")
@router.post("/jobs/{job_id}/cancel")
@router.post("/jobs/{job_id}/resume")
@router.get("/jobs/{job_id}/conflicts")
def gone_sync_job_detail(job_id: str) -> JSONResponse:
    return _gone_response(f"/sync/jobs/{job_id}", "/api/runs")


@router.post("/conflicts/{conflict_id}/resolve")
@router.patch("/jobs/{job_id}/conflicts/{conflict_id}")
def gone_conflicts(conflict_id: str, job_id: str = "") -> JSONResponse:
    return _gone_response("/sync/conflicts/resolve", "/api/runs/resolve")


# =============================================================================
# Mapping & Schema Endpoints
# =============================================================================


@router.get("/mappings")
def list_mappings(active_only: bool = True) -> List[Dict[str, Any]]:
    """List all model mappings."""
    repo = _get_repo()
    mappings = repo.list_mappings(active_only=active_only)
    return [m.model_dump() for m in mappings]


@router.get("/schema/{model_name}/history")
def get_schema_history(
    model_name: str, limit: int = 20
) -> List[Dict[str, Any]]:
    """Get schema version history for a model."""
    from semabridge.sync.schema_evolution import SchemaEvolutionTracker

    repo = _get_repo()
    tracker = SchemaEvolutionTracker(repo)
    versions = tracker.get_history(model_name, limit=limit)
    return [v.model_dump() for v in versions]


# =============================================================================
# Schema Fix Endpoints — auto-add missing dimension columns
# =============================================================================


class AddMissingColumnRequest(BaseModel):
    project_id: str = Field(..., description="Project that owns the Snowflake target")
    dataset_name: str = Field(..., description="Dataset/table to ALTER")
    column_name: str = Field(..., description="Column to add")
    column_type: str = Field("VARCHAR", description="Snowflake SQL type for the new column (default VARCHAR)")


@router.post("/schema/add-missing-column")
async def add_missing_dimension_column(body: AddMissingColumnRequest) -> Dict[str, Any]:
    """Add a missing dimension column to the Snowflake physical table.

    Called by the frontend when the user clicks "Auto-add to Snowflake" for a
    DIMENSION_MISSING conflict surfaced during dry run.  Executes
    ALTER TABLE ... ADD COLUMN IF NOT EXISTS, then returns the ALTER SQL so the
    caller can trigger a re-sync to include the column in DIMENSIONS.
    """
    from semabridge.api.services.project_shared import db_manager
    from semabridge.connectors.connection_manager import ConnectionManager

    try:
        # Load project config to get Snowflake connection details
        session = db_manager.get_session().__enter__()
        try:
            from semabridge.repository.orm.models import Project
            project_row = session.query(Project).filter(Project.id == body.project_id).first()
            if not project_row:
                raise HTTPException(status_code=404, detail=f"Project '{body.project_id}' not found")
            config_yaml = str(project_row.config_yaml or "")
        finally:
            session.__exit__(None, None, None)

        if not config_yaml:
            raise HTTPException(status_code=400, detail="Project has no stored configuration")

        from semabridge.core.config_loader import load_config_from_yaml
        config = load_config_from_yaml(config_yaml)
        sf_cfg = getattr(config, "target", None) or getattr(config, "snowflake", None)
        if sf_cfg is None:
            raise HTTPException(status_code=400, detail="Project has no Snowflake target configuration")

        conn_mgr = ConnectionManager(sf_cfg)
        safe_col = body.column_name.replace('"', '""')
        safe_tbl = body.dataset_name.rsplit(".", 1)[-1].replace('"', '""')
        db_name = str(getattr(sf_cfg, "database", "") or "").strip()
        schema_name = str(getattr(sf_cfg, "schema_name", "") or "").strip()

        alter_sql = (
            f'ALTER TABLE "{db_name}"."{schema_name}"."{safe_tbl}" '
            f'ADD COLUMN IF NOT EXISTS "{safe_col}" {body.column_type}'
        )

        with conn_mgr.get_cursor() as cur:
            conn_mgr._execute_sql(cur, alter_sql, context="add-missing-dim-column")

        logger.info(
            "Added missing dimension column '%s' to '%s.%s.%s' for project %s",
            body.column_name, db_name, schema_name, safe_tbl, body.project_id,
        )
        return {
            "status": "ok",
            "message": f"Column '{body.column_name}' added to table '{safe_tbl}'. Re-run the sync to include it in DIMENSIONS.",
            "alter_sql": alter_sql,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("add-missing-column failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
