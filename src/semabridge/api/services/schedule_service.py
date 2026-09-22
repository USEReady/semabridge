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
    _compat_project_runs,
    _compat_project_schedules,
    _compat_projects,
    _compat_save_store,
    scheduler_service,
)
from semabridge.domain.exceptions import NotFoundError, ValidationError


async def list_job_runs_compat():
    all_runs: List[Dict[str, Any]] = []
    for runs in _compat_project_runs.values():
        all_runs.extend(runs)
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
    from semabridge.api.services.project_ownership_service import is_project_owned_by_user

    project_id = str((payload or {}).get("project_id") or "").strip()
    user_id = (payload or {}).get("user_id")

    if project_id:
        # Trigger a specific project
        run, project_cfg, started = _create_project_run(project_id, "Manual")
        run["message"] = "Job trigger accepted."
        background_tasks.add_task(_run_project_background, run, project_cfg, started)
        return run

    # No project_id — trigger every currently scheduled project. Deliberately
    # does NOT fall back to "all projects" when nothing is scheduled: that
    # fallback used to fire every project in the system (55+, plus preview
    # entries) from a single "Run Now" click with no project context, which
    # is exactly how a single click produced 310+ concurrent project runs in
    # the incident this was fixed for (see incident_run_now_fires_every_
    # project in project memory). An empty schedule list means there is
    # genuinely nothing due to run right now — the caller gets a clear
    # no-op instead.
    scheduled_ids = [
        pid for pid, proj in _compat_projects.items()
        if proj.get("schedule") or proj.get("schedule_type")
    ]

    if not scheduled_ids:
        return {"status": "no_projects", "message": "No scheduled jobs to run.", "triggered": 0}

    triggered = []
    for pid in scheduled_ids:
        # The single-project branch above gets its ownership check from the
        # caller (jobs_controller.trigger_job's _assert_project_access,
        # before this function is ever reached) — this branch has no single
        # project_id for the controller to check ahead of time, so ownership
        # must be enforced per project here instead. Skip (never raise) a
        # project the requesting user doesn't own, so one forbidden project
        # can't block triggering the rest the user does own.
        if not is_project_owned_by_user(pid, user_id, log_prefix="JobProjectAuth"):
            logger.warning("Skipping scheduled trigger for %s: not owned by requesting user", pid)
            continue
        try:
            run, project_cfg, started = _create_project_run(pid, "Manual")
            background_tasks.add_task(_run_project_background, run, project_cfg, started)
            triggered.append(run)
        except Exception as exc:
            logger.warning("Failed to trigger project %s: %s", pid, exc)

    return {"status": "triggered", "triggered": len(triggered), "runs": triggered}
