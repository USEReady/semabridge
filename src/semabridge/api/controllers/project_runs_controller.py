import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Request

from semabridge.api.services.project_runs_service import get_project_runs_compat, run_project_now_compat

router = APIRouter()
router.get('/api/projects/{project_id}/runs')(get_project_runs_compat)


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
