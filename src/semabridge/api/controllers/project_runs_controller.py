from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query, Request

from semabridge.api.services.project_ownership_service import (
    auth_is_enabled,
    is_project_owned_by_user,
    require_request_user_id,
)
from semabridge.api.services.project_domain_service import (
    get_audit_logs_compat,
    get_model_history_compat,
    get_project_lineage_compat,
    get_project_runs_compat,
    get_project_stats_compat,
    get_run_conflicts_compat,
    get_snapshot_content_compat,
    get_snapshot_report_compat,
    manual_deploy_compat,
    preview_restore_compat,
    run_project_now_compat,
    tag_snapshot_compat,
    toggle_snapshot_pin_compat,
)
from semabridge.api.services.run_preview_service import get_run_preview_compat

router = APIRouter()


def _assert_project_access(project_id: str, user_id: str | None) -> None:
    if auth_is_enabled() and not is_project_owned_by_user(project_id, user_id, log_prefix="ProjectRunAuth"):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")


@router.get("/api/projects/{project_id}/runs")
async def get_project_runs(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_project_runs_compat(project_id)


@router.get("/api/projects/{project_id}/audit-logs")
async def get_audit_logs(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_audit_logs_compat(project_id)


@router.get("/api/projects/{project_id}/lineage")
async def get_project_lineage(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_project_lineage_compat(project_id)


@router.put("/api/projects/{project_id}/snapshots/{snapshot_id}/tag")
async def tag_snapshot(
    project_id: str,
    snapshot_id: str,
    request: Request,
    tag: str = Query(default=""),
    comment: str = Query(default=""),
):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await tag_snapshot_compat(project_id, snapshot_id, tag, comment)


@router.get("/api/projects/{project_id}/snapshots/{snapshot_id}/preview-restore")
async def preview_restore(project_id: str, snapshot_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await preview_restore_compat(project_id, snapshot_id)


@router.put("/api/projects/{project_id}/snapshots/{snapshot_id}/pin")
async def toggle_snapshot_pin(
    project_id: str,
    snapshot_id: str,
    request: Request,
    is_pinned: bool = Query(...),
):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await toggle_snapshot_pin_compat(project_id, snapshot_id, is_pinned)


@router.get("/api/projects/{project_id}/models/{model_name}/history")
async def get_model_history(project_id: str, model_name: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_model_history_compat(project_id, model_name)


@router.get("/api/projects/{project_id}/stats")
async def get_project_stats(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_project_stats_compat(project_id)


@router.get("/api/projects/{project_id}/snapshots/{snapshot_id}/content")
async def get_snapshot_content(project_id: str, snapshot_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_snapshot_content_compat(project_id, snapshot_id)


@router.get("/api/projects/{project_id}/snapshots/{snapshot_id}/report")
async def get_snapshot_report(project_id: str, snapshot_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_snapshot_report_compat(project_id, snapshot_id)


@router.post("/api/projects/{project_id}/snapshots/{snapshot_id}/deploy")
async def manual_deploy(
    project_id: str,
    snapshot_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    comment: str = Query(default=""),
):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await manual_deploy_compat(project_id, snapshot_id, background_tasks, comment)


@router.get("/api/runs/{run_id}/conflicts")
async def get_run_conflicts(run_id: str, request: Request):
    require_request_user_id(request)
    return await get_run_conflicts_compat(run_id)


@router.get("/api/projects/{project_id}/run-preview")
async def get_run_preview(project_id: str, request: Request):
    """Return a fast snapshot diff summary so the UI can show what changed
    since the last run before the user confirms a re-run."""
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_run_preview_compat(project_id)


@router.post("/api/projects/{project_id}/run")
async def run_project_now_with_user_context(
    project_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    payload: Optional[Dict[str, Any]] = Body(None),
):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    body: Dict[str, Any] = dict(payload or {})
    if user_id:
        body["user_id"] = user_id
    return await run_project_now_compat(project_id, background_tasks, body)


@router.post("/api/runs/{run_id}/resolve")
async def resolve_run(run_id: str, request: Request):
    require_request_user_id(request)
    raise HTTPException(status_code=501, detail="Run conflict resolution is not implemented on this route")
