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
    POST   /sync/conflicts/{id}/resolve — Resolve a single conflict (bulk)
    PATCH  /sync/jobs/{id}/conflicts/{cid} — Approve/reject/escalate a single conflict
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
from semabridge.sync.artifact_exporter import ArtifactExportService

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
        repo = _get_repo()
        exporter = ArtifactExportService(root_output_dir="output")
        _orchestrator = SyncOrchestrator(repository=repo, artifact_exporter=exporter)
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
    """Request to resolve a single conflict (bulk endpoint)."""

    resolution: ConflictResolution
    resolved_by: str = "user"


class PatchConflictRequest(BaseModel):
    """Per-conflict approval/rejection request (fine-grained PATCH endpoint).

    resolution must be one of: source_wins, target_wins, merge, human_review.
    human_review blocks the deploy until a user with admin access clears it.
    """

    resolution: ConflictResolution
    resolved_by: str = "user"
    resolution_note: Optional[str] = Field(
        default=None, description="Optional explanation for audit trail"
    )


class ResumeJobRequest(BaseModel):
    """Request to resolve all conflicts and resume a job."""

    resolution: ConflictResolution
    resolved_by: str = "user"


# =============================================================================
# Job Endpoints
# =============================================================================


@router.post("/jobs")
def start_sync_job(request: StartSyncRequest) -> Dict[str, Any]:
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
def list_sync_jobs(
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
def get_sync_job(job_id: str) -> Dict[str, Any]:
    """Get detailed status of a sync job."""
    try:
        orchestrator = _get_orchestrator()
        return orchestrator.get_status(job_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/jobs/{job_id}/cancel")
def cancel_sync_job(job_id: str) -> Dict[str, Any]:
    """Cancel a running or paused sync job."""
    try:
        orchestrator = _get_orchestrator()
        job = orchestrator.cancel(job_id)
        return {"job_id": job.job_id, "status": job.status.value}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/jobs/{job_id}/resume")
def resume_sync_job(
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
def get_conflicts(job_id: str) -> List[Dict[str, Any]]:
    """Get all conflicts for a sync job."""
    repo = _get_repo()
    conflicts = repo.get_conflicts_for_job(job_id)
    return [c.model_dump() for c in conflicts]


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: str, request: ResolveConflictRequest
) -> Dict[str, str]:
    """Resolve a single conflict (legacy bulk-style endpoint)."""
    try:
        repo = _get_repo()
        repo.resolve_conflict(
            conflict_id, request.resolution, request.resolved_by
        )
        return {"conflict_id": conflict_id, "status": "resolved"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/jobs/{job_id}/conflicts/{conflict_id}")
def patch_conflict(
    job_id: str,
    conflict_id: str,
    request: PatchConflictRequest,
) -> Dict[str, Any]:
    """Approve, reject, or escalate a single conflict.

    - resolution=source_wins / target_wins / merge  →  resolve and potentially auto-resume
    - resolution=human_review  →  escalate (sets escalated=True); blocks deploy until cleared

    Returns:
        conflict_id, status, auto_resumed (True if all criticals are now resolved).
    """
    from semabridge.sync.models import ConflictResolution, ConflictSeverity

    try:
        repo = _get_repo()

        # Resolve with note + escalated flag
        is_escalated = request.resolution == ConflictResolution.HUMAN_REVIEW
        repo.resolve_conflict_detailed(
            conflict_id=conflict_id,
            resolution=request.resolution,
            resolved_by=request.resolved_by,
            resolution_note=request.resolution_note,
            escalated=is_escalated,
        )

        # Check if all CRITICAL conflicts for this job are now resolved
        # (auto-resume when no more critical blockers remain)
        remaining_criticals = [
            c for c in repo.get_unresolved_conflicts(job_id)
            if c.severity == ConflictSeverity.CRITICAL
        ]
        auto_resumed = False
        if not remaining_criticals:
            # No critical blockers — check if job is paused and resume it
            try:
                job = repo.get_job(job_id)
                from semabridge.sync.models import SyncJobStatus
                if job and job.status == SyncJobStatus.PAUSED:
                    repo.update_job_status(job_id, SyncJobStatus.PENDING)
                    auto_resumed = True
                    logger.info(
                        f"Job {job_id} auto-resumed: all critical conflicts resolved"
                    )
            except Exception as resume_err:
                logger.warning(f"Could not auto-resume job {job_id}: {resume_err}")

        return {
            "conflict_id": conflict_id,
            "job_id": job_id,
            "status": "escalated" if is_escalated else "resolved",
            "auto_resumed": auto_resumed,
            "remaining_critical_conflicts": len(remaining_criticals),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
