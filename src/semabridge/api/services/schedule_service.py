"""Schedule service — project job scheduling, schedule CRUD, and job triggering.

Provides get/save/delete project schedule, trigger job, list job schedules,
list job runs, and jobs config management.
"""
from typing import Any, Dict, List

from fastapi import BackgroundTasks

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

from semabridge.api.services.project_shared import (
    _compat_ensure_loaded,
    _compat_job_config,
    _compat_now_iso,
    _compat_project_schedules,
    _compat_projects,
    _compat_save_store,
    scheduler_service,
)
from semabridge.domain.exceptions import NotFoundError, ValidationError


async def list_job_runs_compat():
    """Aggregate run history across every known project.

    Delegates to get_project_runs_compat() per project rather than reading
    _compat_project_runs directly. That in-memory dict is only ever
    populated from the JSON compat-store cache file at process startup, or
    by a run triggered during this same process's lifetime -- it has no
    database fallback. get_project_runs_compat() does (it queries the
    `runs` ORM table and warms the cache when empty), so a run that's
    safely recorded in the database but not yet warm in memory -- e.g.
    right after a server restart, before anyone has opened that specific
    project's own run-history view -- would otherwise be silently missing
    from this aggregate list even though it exists.
    """
    from semabridge.api.services.run_service import get_project_runs_compat

    _compat_ensure_loaded()
    all_runs: List[Dict[str, Any]] = []
    for project_id in list(_compat_projects.keys()):
        all_runs.extend(await get_project_runs_compat(project_id))
    all_runs.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return all_runs


async def get_jobs_config_compat():
    return _compat_job_config


async def list_job_schedules_compat():
    _compat_ensure_loaded()
    items: List[Dict[str, Any]] = []
    for project_id, schedule in _compat_project_schedules.items():
        project = _compat_projects.get(project_id) or {}
        items.append({**schedule, "project_id": project_id, "project_name": project.get("name") or project_id})
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items


async def get_project_schedule_compat(project_id: str):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise NotFoundError("Project not found")
    schedule = scheduler_service.get_project_schedule(project_id) or _compat_project_schedules.get(project_id)
    if schedule:
        return schedule
    return {"project_id": project_id, "schedule_type": "manual", "enabled": False}


async def save_project_schedule_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise NotFoundError("Project not found")
    try:
        schedule = scheduler_service.save_project_schedule(project_id, payload or {})
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if str(schedule.get("schedule_type") or "manual") == "manual":
        _compat_project_schedules.pop(project_id, None)
    else:
        _compat_project_schedules[project_id] = {
            "project_id": project_id,
            "schedule_type": schedule.get("schedule_type"),
            "cron": schedule.get("cron") or "",
            "date": schedule.get("date") or "",
            "time": schedule.get("time") or "",
            "timezone": schedule.get("timezone") or "UTC",
            "enabled": bool(schedule.get("enabled")),
            "scheduled_time": schedule.get("scheduled_time") or "",
            "next_run_at": schedule.get("next_run_at"),
            "created_at": schedule.get("created_at") or _compat_now_iso(),
        }
    _compat_job_config.update({
        "project_id": project_id,
        "schedule_type": str(schedule.get("schedule_type") or "manual"),
        "cron": schedule.get("cron") or "",
        "timezone": schedule.get("timezone") or "UTC",
        "enabled": bool(schedule.get("enabled")),
        "date": schedule.get("date") or "",
        "time": schedule.get("time") or "",
        "scheduled_time": schedule.get("scheduled_time") or "",
    })
    _compat_save_store()
    return schedule


async def delete_project_schedule_compat(project_id: str):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise NotFoundError("Project not found")
    removed = scheduler_service.delete_project_schedule(project_id)
    _compat_project_schedules.pop(project_id, None)
    if not removed:
        _compat_save_store()
        return {"status": "deleted", "project_id": project_id, "schedule_type": "manual", "enabled": False}
    _compat_save_store()
    return {"status": "deleted", **removed}


async def update_jobs_config_compat(payload: dict):
    merged = {**_compat_job_config, **(payload or {})}
    _compat_job_config.update(merged)
    project_id = str((payload or {}).get("project_id") or "").strip()
    schedule_type = str((payload or {}).get("schedule_type") or "").strip()
    if project_id and schedule_type and project_id in _compat_projects:
        try:
            scheduler_service.save_project_schedule(project_id, payload or {})
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
    _compat_save_store()
    return {"status": "saved", **merged}


async def trigger_job_compat(payload: dict, background_tasks: BackgroundTasks):
    _compat_ensure_loaded()
    from semabridge.api.services.run_service import _create_project_run, _run_project_background

    project_id = str((payload or {}).get("project_id") or "").strip()

    if project_id:
        # Trigger a specific project
        run, project_cfg, started = _create_project_run(project_id, "Manual")
        run["message"] = "Job trigger accepted."
        background_tasks.add_task(_run_project_background, run, project_cfg, started)
        return run

    # No project_id — trigger all scheduled projects
    scheduled_ids = [
        pid for pid, proj in _compat_projects.items()
        if proj.get("schedule") or proj.get("schedule_type")
    ]
    if not scheduled_ids:
        # Fall back to all projects if none are explicitly scheduled
        scheduled_ids = list(_compat_projects.keys())

    if not scheduled_ids:
        return {"status": "no_projects", "message": "No projects available to trigger.", "triggered": 0}

    triggered = []
    for pid in scheduled_ids:
        try:
            run, project_cfg, started = _create_project_run(pid, "Manual")
            background_tasks.add_task(_run_project_background, run, project_cfg, started)
            triggered.append(run)
        except Exception as exc:
            logger.warning("Failed to trigger project %s: %s", pid, exc)

    return {"status": "triggered", "triggered": len(triggered), "runs": triggered}
