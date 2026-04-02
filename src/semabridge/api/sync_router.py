"""
Sync API Router.

FastAPI endpoints for the bidirectional synchronization engine.

Endpoints:
    POST   /sync/jobs          — Start a new sync job
    GET    /sync/jobs          — List sync jobs
    GET    /sync/jobs/{id}     — Get sync job status
    POST   /sync/jobs/{id}/cancel   — Cancel a running job
    POST   /sync/jobs/{id}/resume   — Resolve conflicts and resume
    GET    /sync/jobs/{id}/conflicts — Get conflicts for a job
    POST   /sync/conflicts/{id}/resolve — Resolve a single conflict
    GET    /sync/mappings      — List model mappings
    GET    /sync/schema/{model}/history — Schema version history
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from semabridge.sync.models import (
    ConflictResolution,
    SyncConfig,
    SyncDirection,
    SyncJobStatus,
)
from semabridge.sync.orchestrator import SyncOrchestrator
from semabridge.sync.repository import SyncRepository

logger = logging.getLogger("semabridge.api.sync")

router = APIRouter(prefix="/sync", tags=["sync"])

# Lazy-initialised singleton
_repo: Optional[SyncRepository] = None
_orchestrator: Optional[SyncOrchestrator] = None


def _get_repo() -> SyncRepository:
    global _repo
    if _repo is None:
        _repo = SyncRepository()
    return _repo


def _get_orchestrator() -> SyncOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SyncOrchestrator(repository=_get_repo())
    return _orchestrator


# =============================================================================
# Request / Response schemas
# =============================================================================


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


class ResolveConflictRequest(BaseModel):
    """Request to resolve a single conflict."""

    resolution: ConflictResolution
    resolved_by: str = "user"


class ResumeJobRequest(BaseModel):
    """Request to resolve all conflicts and resume a job."""

    resolution: ConflictResolution
    resolved_by: str = "user"


# =============================================================================
# Job Endpoints
# =============================================================================


@router.post("/jobs")
async def start_sync_job(request: StartSyncRequest) -> Dict[str, Any]:
    """Start a new synchronization job."""
    config = SyncConfig(
        direction=request.direction,
        conflict_resolution=request.conflict_resolution,
        pbix_folder=request.pbix_folder,
        source_path=request.source_path or request.file_path,
        pbix_pattern=request.pbix_pattern,
        snowflake_database=request.snowflake_database,
        snowflake_schema=request.snowflake_schema or request.target_snowflake_schema,
        fabric_workspace_id=request.fabric_workspace_id,
        max_workers=request.max_workers,
        enable_parallel=request.enable_parallel,
        incremental=request.incremental,
        include_data=request.include_data,
        max_data_rows=request.max_data_rows,
    )

    try:
        orchestrator = _get_orchestrator()
        job = orchestrator.run(config, initiated_by="api")
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "total_items": job.total_items,
            "completed_items": job.completed_items,
            "failed_items": job.failed_items,
            "duration_ms": job.duration_ms,
        }
    except Exception as e:
        logger.error(f"Failed to start sync job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs")
async def list_sync_jobs(
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List sync jobs, optionally filtered by status."""
    repo = _get_repo()
    filter_status = SyncJobStatus(status) if status else None
    jobs = repo.list_jobs(status=filter_status, limit=limit)
    return [
        {
            "job_id": j.job_id,
            "direction": j.direction.value,
            "status": j.status.value,
            "created_at": j.created_at,
            "total_items": j.total_items,
            "completed_items": j.completed_items,
            "failed_items": j.failed_items,
            "duration_ms": j.duration_ms,
        }
        for j in jobs
    ]


@router.get("/jobs/{job_id}")
async def get_sync_job(job_id: str) -> Dict[str, Any]:
    """Get detailed status of a sync job."""
    try:
        orchestrator = _get_orchestrator()
        return orchestrator.get_status(job_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/jobs/{job_id}/cancel")
async def cancel_sync_job(job_id: str) -> Dict[str, Any]:
    """Cancel a running or paused sync job."""
    try:
        orchestrator = _get_orchestrator()
        job = orchestrator.cancel(job_id)
        return {"job_id": job.job_id, "status": job.status.value}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/jobs/{job_id}/resume")
async def resume_sync_job(
    job_id: str, request: ResumeJobRequest
) -> Dict[str, Any]:
    """Resolve all conflicts and resume a paused sync job."""
    try:
        orchestrator = _get_orchestrator()
        job = orchestrator.resolve_and_resume(
            job_id, request.resolution, request.resolved_by
        )
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "total_items": job.total_items,
            "completed_items": job.completed_items,
            "failed_items": job.failed_items,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# Conflict Endpoints
# =============================================================================


@router.get("/jobs/{job_id}/conflicts")
async def get_conflicts(job_id: str) -> List[Dict[str, Any]]:
    """Get all conflicts for a sync job."""
    repo = _get_repo()
    conflicts = repo.get_conflicts_for_job(job_id)
    return [c.model_dump() for c in conflicts]


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    conflict_id: str, request: ResolveConflictRequest
) -> Dict[str, str]:
    """Resolve a single conflict."""
    try:
        repo = _get_repo()
        repo.resolve_conflict(
            conflict_id, request.resolution, request.resolved_by
        )
        return {"conflict_id": conflict_id, "status": "resolved"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# Mapping & Schema Endpoints
# =============================================================================


@router.get("/mappings")
async def list_mappings(active_only: bool = True) -> List[Dict[str, Any]]:
    """List all model mappings."""
    repo = _get_repo()
    mappings = repo.list_mappings(active_only=active_only)
    return [m.model_dump() for m in mappings]


@router.get("/schema/{model_name}/history")
async def get_schema_history(
    model_name: str, limit: int = 20
) -> List[Dict[str, Any]]:
    """Get schema version history for a model."""
    from semabridge.sync.schema_evolution import SchemaEvolutionTracker

    repo = _get_repo()
    tracker = SchemaEvolutionTracker(repo)
    versions = tracker.get_history(model_name, limit=limit)
    return [v.model_dump() for v in versions]
