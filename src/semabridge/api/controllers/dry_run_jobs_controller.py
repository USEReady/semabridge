"""Multi-PBIX background dry-run jobs — background-job-plus-polling endpoints.

Mirrors the shape project_runs_controller.py already uses for real deploys
(POST creates + schedules a BackgroundTasks job and returns immediately;
GET polls status), applied to dry-run so an N-file batch can run in parallel
without risking an HTTP timeout. The actual per-file pipeline logic is
unchanged — see mappings_controller._run_dry_run_pipeline(), which this
reuses via dry_run_job_service.py rather than reimplementing it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from semabridge.api.controllers.mappings_controller import _assert_project_access
from semabridge.api.services.dry_run_job_service import (
    create_dry_run_job,
    get_dry_run_job_file_result,
    get_dry_run_job_status,
    reset_dry_run_job_file_for_rerun,
    run_dry_run_job,
)
from semabridge.api.services.project_ownership_service import (
    require_request_user_id,
    validate_project_connector_accounts_belong_to_user,
)
from semabridge.domain.exceptions import ValidationError

logger = logging.getLogger(__name__)

router = APIRouter()


class DryRunJobRequest(BaseModel):
    source_config: Dict[str, Any]
    target_config: Dict[str, Any]
    selected_sources: List[str]


@router.post("/api/projects/{project_id}/dry-run-jobs")
async def create_dry_run_job_endpoint(
    project_id: str,
    http_request: Request,
    request: DryRunJobRequest,
    background_tasks: BackgroundTasks,
):
    """Creates a multi-file dry-run job and returns immediately with a
    job_id + per-file pending status — the actual pipeline work is scheduled
    as a background task and polled via the GET endpoints below, exactly the
    pattern project_runs_controller.py's run endpoint already uses for real
    deploys (see run_service.py's _create_project_run() +
    background_tasks.add_task()).
    """
    request_user_id = require_request_user_id(http_request)
    validate_project_connector_accounts_belong_to_user(
        request_user_id,
        {
            "source": request.source_config,
            "target": request.target_config,
            "targets": [request.target_config],
        },
    )
    if project_id not in ("preview", ""):
        _assert_project_access(project_id, request_user_id)

    job = create_dry_run_job(
        project_id=project_id,
        source_config=request.source_config,
        target_config=request.target_config,
        selected_sources=request.selected_sources,
        requested_by_user_id=request_user_id,
    )
    background_tasks.add_task(run_dry_run_job, job["job_id"])
    job["status"] = "running"
    return job


@router.get("/api/projects/{project_id}/dry-run-jobs/{job_id}")
async def get_dry_run_job_status_endpoint(project_id: str, job_id: str, http_request: Request):
    """Lightweight per-file status list — what the per-file list view (Part
    C) polls every few seconds."""
    user_id = require_request_user_id(http_request)
    if project_id not in ("preview", ""):
        _assert_project_access(project_id, user_id)
    return get_dry_run_job_status(job_id)


@router.get("/api/projects/{project_id}/dry-run-jobs/{job_id}/files/{file_id}")
async def get_dry_run_job_file_endpoint(project_id: str, job_id: str, file_id: str, http_request: Request):
    """Full dry-run payload for ONE file, same shape the single-file
    /dry-run endpoint has always returned — this is what the drill-in view
    (Part C) fetches to feed the existing DryRunMappingTable component."""
    user_id = require_request_user_id(http_request)
    if project_id not in ("preview", ""):
        _assert_project_access(project_id, user_id)
    return get_dry_run_job_file_result(job_id, file_id)


@router.post("/api/projects/{project_id}/dry-run-jobs/{job_id}/files/{file_id}/rerun")
async def rerun_dry_run_job_file_endpoint(
    project_id: str,
    job_id: str,
    file_id: str,
    http_request: Request,
    background_tasks: BackgroundTasks,
):
    """Re-runs exactly one file's dry-run without touching any sibling file's
    already-completed state — resets just this file's row to pending, then
    re-schedules run_dry_run_job(job_id), which only reprocesses
    pending/running files.
    """
    user_id = require_request_user_id(http_request)
    if project_id not in ("preview", ""):
        _assert_project_access(project_id, user_id)
    result = reset_dry_run_job_file_for_rerun(job_id, file_id)
    background_tasks.add_task(run_dry_run_job, job_id)
    return result
