import os
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends, BackgroundTasks

from semabridge.api.services.jobs_service import (
    delete_project_schedule_compat,
    get_jobs_config_compat,
    get_project_schedule_compat,
    list_job_runs_compat,
    clear_job_runs_compat,
    list_job_schedules_compat,
    save_project_schedule_compat,
    trigger_job_compat,
    update_jobs_config_compat,
)

router = APIRouter()

# ── Helper Ownership Scoping Functions ──────────────────────────────────

def _get_user_id(request: Request) -> str | None:
    if os.environ.get("AUTH_ENABLED", "").lower() == "true":
        return getattr(request.state, "user_id", None)
    return None


def _get_allowed_account_ids(user_id: str | None) -> set[str] | None:
    if not user_id:
        return None
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.models import Account
    from sqlalchemy import select
    with db_manager.get_session() as session:
        ids = session.execute(
            select(Account.id).where(Account.owner_id == int(user_id))
        ).scalars().all()
        return {str(i) for i in ids}


def _is_project_owned(project_id: str, allowed_accounts: set[str] | None) -> bool:
    if allowed_accounts is None:
        return True
    from semabridge.api.services.project_shared import _compat_projects, _compat_ensure_loaded
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        return False
    
    account_id = project.get("account_id")
    if not account_id:
        src = project.get("source")
        if isinstance(src, dict):
            account_id = src.get("identity_id") or src.get("account_id")
    if not account_id:
        tgt = project.get("target")
        if isinstance(tgt, dict):
            account_id = tgt.get("identity_id") or tgt.get("account_id")
            
    return account_id and str(account_id) in allowed_accounts


# ── Jobs Route Handlers with Server-Side Ownership Enforcement ──────────

@router.get('/api/jobs/runs')
async def list_job_runs(request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    
    runs = await list_job_runs_compat()
    if allowed_accounts is not None:
        filtered = []
        for r in runs:
            pid = r.get("project_id")
            if pid and _is_project_owned(pid, allowed_accounts):
                filtered.append(r)
        return filtered
    return runs


@router.delete('/api/jobs/runs')
async def clear_job_runs(request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None:
        # User is only allowed to clear runs belonging to their projects
        # Since clear_job_runs_compat clears all project runs, we filter it or raise forbidden
        # If user has RLS/scoping active, we only permit if they own the runs they clear
        # But clear_job_runs_compat wipes the log store. Let's adapt it to only delete the user's runs:
        from semabridge.api.services.project_shared import _compat_project_runs, _compat_save_store
        for pid in list(_compat_project_runs.keys()):
            if _is_project_owned(pid, allowed_accounts):
                _compat_project_runs[pid] = []
        _compat_save_store()
        return {"status": "success", "message": "Scoped job runs cleared."}
        
    return await clear_job_runs_compat()


@router.get('/api/jobs/config')
async def get_jobs_config(request: Request):
    # Global scheduler jobs config is public or admin-only, let's return normally
    return await get_jobs_config_compat()


@router.get('/api/jobs/schedules')
async def list_job_schedules(request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    
    schedules = await list_job_schedules_compat()
    if allowed_accounts is not None:
        filtered = []
        for s in schedules:
            pid = s.get("project_id")
            if pid and _is_project_owned(pid, allowed_accounts):
                filtered.append(s)
        return filtered
    return schedules


@router.get('/api/projects/{project_id}/schedule')
async def get_project_schedule(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await get_project_schedule_compat(project_id)


@router.post('/api/projects/{project_id}/schedule')
async def save_project_schedule(project_id: str, payload: dict, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await save_project_schedule_compat(project_id, payload)


@router.delete('/api/projects/{project_id}/schedule')
async def delete_project_schedule(project_id: str, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    if allowed_accounts is not None and not _is_project_owned(project_id, allowed_accounts):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await delete_project_schedule_compat(project_id)


@router.put('/api/jobs/config')
async def update_jobs_config(payload: dict, request: Request):
    # Global scheduler jobs config updates
    return await update_jobs_config_compat(payload)


@router.post('/api/jobs/trigger')
async def trigger_job(payload: dict, background_tasks: BackgroundTasks, request: Request):
    user_id = _get_user_id(request)
    allowed_accounts = _get_allowed_account_ids(user_id)
    
    project_id = str((payload or {}).get("project_id") or "").strip()
    if allowed_accounts is not None:
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id is required")
        if not _is_project_owned(project_id, allowed_accounts):
            raise HTTPException(status_code=403, detail="Forbidden: project access denied")
            
    return await trigger_job_compat(payload, background_tasks)
