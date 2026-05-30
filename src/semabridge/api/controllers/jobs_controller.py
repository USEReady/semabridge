import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel


class SaveScheduleRequest(BaseModel):
    cron: Optional[str] = None
    enabled: Optional[bool] = None
    timezone: Optional[str] = None

    class Config:
        extra = "allow"


class UpdateJobsConfigRequest(BaseModel):
    max_concurrent_jobs: Optional[int] = None
    default_timeout_seconds: Optional[int] = None

    class Config:
        extra = "allow"


class TriggerJobRequest(BaseModel):
    project_id: str
    user_id: Optional[str] = None
    sync_mode: Optional[str] = None

    class Config:
        extra = "allow"

from semabridge.api.services.jobs_service import (
    clear_job_runs_compat,
    delete_project_schedule_compat,
    get_jobs_config_compat,
    get_project_schedule_compat,
    list_job_runs_compat,
    list_job_schedules_compat,
    save_project_schedule_compat,
    trigger_job_compat,
    update_jobs_config_compat,
)
from semabridge.api.services.project_ownership_service import (
    auth_is_enabled,
    is_project_owned_by_user,
    require_request_user_id,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _assert_project_access(project_id: str, user_id: str | None) -> None:
    if auth_is_enabled() and not is_project_owned_by_user(project_id, user_id, log_prefix="JobProjectAuth"):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")


@router.get("/api/jobs/runs")
async def list_job_runs(request: Request):
    user_id = require_request_user_id(request)
    runs = await list_job_runs_compat()
    if auth_is_enabled():
        return [
            run
            for run in runs
            if str(run.get("project_id") or "").strip()
            and is_project_owned_by_user(str(run.get("project_id") or "").strip(), user_id, log_denied=False, log_prefix="JobProjectAuth")
        ]
    return runs


@router.delete("/api/jobs/runs")
async def clear_job_runs(request: Request):
    user_id = require_request_user_id(request)
    if auth_is_enabled():
        from semabridge.api.services.project_shared import _compat_project_runs, _compat_save_store

        for project_id in list(_compat_project_runs.keys()):
            if is_project_owned_by_user(project_id, user_id, log_denied=False, log_prefix="JobProjectAuth"):
                _compat_project_runs[project_id] = []
        _compat_save_store()
        return {"status": "success", "message": "Scoped job runs cleared."}

    return await clear_job_runs_compat()


@router.get("/api/jobs/config")
async def get_jobs_config(request: Request):
    return await get_jobs_config_compat()


@router.get("/api/jobs/schedules")
async def list_job_schedules(request: Request):
    user_id = require_request_user_id(request)
    schedules = await list_job_schedules_compat()
    if auth_is_enabled():
        return [
            schedule
            for schedule in schedules
            if str(schedule.get("project_id") or "").strip()
            and is_project_owned_by_user(str(schedule.get("project_id") or "").strip(), user_id, log_denied=False, log_prefix="JobProjectAuth")
        ]
    return schedules


@router.get("/api/projects/{project_id}/schedule")
async def get_project_schedule(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await get_project_schedule_compat(project_id)


@router.post("/api/projects/{project_id}/schedule")
async def save_project_schedule(project_id: str, payload: SaveScheduleRequest, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await save_project_schedule_compat(project_id, payload.model_dump(exclude_none=False))


@router.delete("/api/projects/{project_id}/schedule")
async def delete_project_schedule(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await delete_project_schedule_compat(project_id)


@router.put("/api/jobs/config")
async def update_jobs_config(payload: UpdateJobsConfigRequest, request: Request):
    return await update_jobs_config_compat(payload.model_dump(exclude_none=False))


@router.post("/api/jobs/trigger")
async def trigger_job(payload: TriggerJobRequest, background_tasks: BackgroundTasks, request: Request):
    user_id = require_request_user_id(request)
    body = payload.model_dump(exclude_none=False)
    project_id = str(body.get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    _assert_project_access(project_id, user_id)
    if user_id:
        body["user_id"] = user_id
    return await trigger_job_compat(body, background_tasks)
