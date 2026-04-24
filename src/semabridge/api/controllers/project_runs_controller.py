import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Request

from semabridge.api.services.project_runs_service import (
    get_project_runs_compat,
    run_project_now_compat,
    tag_snapshot_compat,
    preview_restore_compat,
    get_audit_logs_compat,
    get_project_lineage_compat,
    toggle_snapshot_pin_compat,
    get_model_history_compat,
    get_project_stats_compat,
    get_snapshot_content_compat,
    manual_deploy_compat,
)

router = APIRouter()
router.get('/api/projects/{project_id}/runs')(get_project_runs_compat)
router.get('/api/projects/{project_id}/audit-logs')(get_audit_logs_compat)
router.get('/api/projects/{project_id}/lineage')(get_project_lineage_compat)
router.put('/api/projects/{project_id}/snapshots/{snapshot_id}/tag')(tag_snapshot_compat)
router.get('/api/projects/{project_id}/snapshots/{snapshot_id}/preview-restore')(preview_restore_compat)
router.put('/api/projects/{project_id}/snapshots/{snapshot_id}/pin')(toggle_snapshot_pin_compat)
router.get('/api/projects/{project_id}/models/{model_name}/history')(get_model_history_compat)
router.get('/api/projects/{project_id}/stats')(get_project_stats_compat)
router.get('/api/projects/{project_id}/snapshots/{snapshot_id}/content')(get_snapshot_content_compat)
router.post('/api/projects/{project_id}/snapshots/{snapshot_id}/deploy')(manual_deploy_compat)


@router.post('/api/projects/{project_id}/run')
async def run_project_now_with_user_context(
    project_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    payload: Optional[Dict[str, Any]] = None,
):
    body: Dict[str, Any] = dict(payload or {})
    if os.environ.get("AUTH_ENABLED", "").lower() == "true":
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            body["user_id"] = user_id
    return await run_project_now_compat(project_id, background_tasks, body)
