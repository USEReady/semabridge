from semabridge.api.services.project_shared import *

async def get_project_runs_compat(project_id: str):
    _compat_ensure_loaded()
    return _compat_project_runs.get(project_id, [])


def _create_project_run(project_id: str, schedule_label: str = "Manual") -> tuple[dict, str, float]:
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    started = _time.time()
    run_id = f"run-{int(_time.time() * 1000)}"
    run = {
        "run_id": run_id,
        "id": run_id,
        "project_id": project_id,
        "project_name": _compat_projects[project_id].get("name", project_id),
        "schedule": schedule_label,
        "status": "running",
        "duration_ms": 0,
        "started_at": _compat_now_iso(),
    }
    _compat_project_runs.setdefault(project_id, []).insert(0, run)
    _compat_save_store()
    project_cfg = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(_compat_projects[project_id])
    return run, project_cfg, started


async def _perform_project_run(run: dict, project_cfg: str, started: float) -> dict:
    try:
        # Project runs intentionally call the same sync service used by
        # /api/sync so manual, scheduled, and job-triggered executions stay aligned.
        from semabridge.api.services.core_domain_service import sync_models

        sync_result = await sync_models({"content": project_cfg})
        elapsed_ms = int((_time.time() - started) * 1000)
        run["duration_ms"] = elapsed_ms
        overall = str((sync_result or {}).get("status") or "").lower()
        if overall == "success":
            run["status"] = "success"
        elif overall == "partial":
            run["status"] = "warning"
        else:
            run["status"] = "failed"
        run["summary"] = (sync_result or {}).get("summary") or {}
        run["results"] = (sync_result or {}).get("results") or []
        run["models_synced"] = int((sync_result or {}).get("models_synced") or 0)
        run["total_models"] = int((sync_result or {}).get("total_models") or 0)
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
    _compat_save_store()
    return run


async def _execute_project_run(project_id: str, schedule_label: str = "Manual") -> dict:
    run, project_cfg, started = _create_project_run(project_id, schedule_label)
    return await _perform_project_run(run, project_cfg, started)


def _run_project_background(run: dict, project_cfg: str, started: float) -> None:
    import asyncio

    # APScheduler/background tasks are sync entrypoints, so we bridge back
    # into the async project execution flow here.
    asyncio.run(_perform_project_run(run, project_cfg, started))



async def run_project_now_compat(project_id: str, background_tasks: BackgroundTasks):
    run, project_cfg, started = _create_project_run(project_id, "Manual")
    background_tasks.add_task(_run_project_background, run, project_cfg, started)
    return {"run_id": run["id"], "status": "running", "message": "Sync started in background"}


async def list_folders_compat():
    """Compatibility: newfrontend expects a folders collection."""
    return list(_compat_folders.values())


async def create_folder_compat(payload: dict):
    folder_id = str((payload or {}).get("id") or (payload or {}).get("folder_id") or f"folder-{int(_time.time() * 1000)}")
    folder = {
        "id": folder_id,
        "folder_id": folder_id,
        "name": (payload or {}).get("name") or f"Folder {folder_id[-4:]}",
        "color": (payload or {}).get("color") or "#6366f1",
    }
    _compat_folders[folder_id] = folder
    return folder


async def rename_folder_compat(folder_id: str, payload: dict):
    folder = _compat_folders.get(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if "name" in (payload or {}):
        folder["name"] = (payload or {}).get("name")
    if "color" in (payload or {}):
        folder["color"] = (payload or {}).get("color")
    _compat_folders[folder_id] = folder
    return folder


async def delete_folder_compat(folder_id: str):
    _compat_folders.pop(folder_id, None)
    for project in _compat_projects.values():
        if project.get("folder_id") == folder_id:
            project["folder_id"] = None
            project["updated_at"] = _compat_now_iso()
    return Response(status_code=204)


async def move_project_to_folder_compat(project_id: str, payload: dict):
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    folder_id = (payload or {}).get("folder_id")
    if folder_id is not None and folder_id not in _compat_folders:
        raise HTTPException(status_code=404, detail="Folder not found")
    project["folder_id"] = folder_id
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    return project


async def list_job_runs_compat():
    """Compatibility: return empty runs when scheduler APIs are absent."""
    all_runs: List[Dict[str, Any]] = []
    for runs in _compat_project_runs.values():
        all_runs.extend(runs)
    all_runs.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return all_runs


async def get_jobs_config_compat():
    """Compatibility: return non-failing default scheduler config."""
    return _compat_job_config


async def list_job_schedules_compat():
    _compat_ensure_loaded()
    items: List[Dict[str, Any]] = []
    for project_id, schedule in _compat_project_schedules.items():
        project = _compat_projects.get(project_id) or {}
        items.append({
            **schedule,
            "project_id": project_id,
            "project_name": project.get("name") or project_id,
        })
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items


async def get_project_schedule_compat(project_id: str):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    schedule = scheduler_service.get_project_schedule(project_id) or _compat_project_schedules.get(project_id)
    if schedule:
        return schedule
    return {
        "project_id": project_id,
        "schedule_type": "manual",
        "enabled": False,
    }


async def save_project_schedule_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        schedule = scheduler_service.save_project_schedule(project_id, payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    })
    _compat_save_store()
    return schedule


async def delete_project_schedule_compat(project_id: str):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    removed = scheduler_service.delete_project_schedule(project_id)
    _compat_project_schedules.pop(project_id, None)
    if not removed:
        _compat_save_store()
        return {"status": "deleted", "project_id": project_id, "schedule_type": "manual", "enabled": False}
    _compat_save_store()
    return {"status": "deleted", **removed}


async def update_jobs_config_compat(payload: dict):
    """Compatibility: accept schedule config updates without failing."""
    merged = {**_compat_job_config, **(payload or {})}
    _compat_job_config.update(merged)
    project_id = str((payload or {}).get("project_id") or "").strip()
    schedule_type = str((payload or {}).get("schedule_type") or "").strip()
    if project_id and schedule_type and project_id in _compat_projects:
        try:
            scheduler_service.save_project_schedule(project_id, payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    _compat_save_store()
    return {"status": "saved", **merged}


async def trigger_job_compat(payload: dict, background_tasks: BackgroundTasks):
    """Compatibility: trigger a project job using the shared execution flow."""
    _compat_ensure_loaded()
    project_id = str((payload or {}).get("project_id") or "").strip()
    if not project_id and _compat_projects:
        project_id = next(iter(_compat_projects.keys()))
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    run, project_cfg, started = _create_project_run(project_id, "Manual")
    run["message"] = "Job trigger accepted."
    background_tasks.add_task(_run_project_background, run, project_cfg, started)
    return run


async def list_mappings_compat(project_id: Optional[str] = None):
    pid = str(project_id or "").strip()

    def _seed_for_project(seed_project_id: str) -> Dict[str, Any]:
        source_fields = [
            {"name": "transaction_id", "type": "uuid"},
            {"name": "amount", "type": "decimal"},
            {"name": "customer_ref", "type": "string"},
            {"name": "created_at", "type": "timestamp"},
            {"name": "status_code", "type": "integer"},
            {"name": "contact_email", "type": "string"},
            {"name": "region_id", "type": "integer"},
        ]
        target_fields = [
            {"name": "txn_id", "type": "varchar"},
            {"name": "sale_amount", "type": "float"},
            {"name": "customer_key", "type": "varchar"},
            {"name": "sale_date", "type": "date"},
            {"name": "order_status", "type": "integer"},
            {"name": "email_address", "type": "varchar"},
            {"name": "region_key", "type": "integer"},
        ]

        seeded = []
        for idx, src in enumerate(source_fields):
            tgt = target_fields[idx] if idx < len(target_fields) else None
            mapping_id = f"{seed_project_id}-map-{idx + 1}"
            existing = _compat_mappings.get(mapping_id, {})
            mapping = {
                "id": mapping_id,
                "project_id": seed_project_id,
                "source_field": src["name"],
                "source_type": src["type"],
                "target_field": tgt["name"] if tgt else None,
                "target_type": tgt["type"] if tgt else None,
                "status": existing.get("status") or "auto",
                "transform": existing.get("transform") or "",
                "validation": existing.get("validation") or "None",
            }
            _compat_mappings[mapping_id] = mapping
            seeded.append(mapping)

        return {
            "source_fields": source_fields,
            "target_fields": target_fields,
            "mappings": seeded,
        }

    if pid:
        mappings = [
            m for m in _compat_mappings.values()
            if str(m.get("project_id") or "") == pid
        ]
        if not mappings:
            return _seed_for_project(pid)
        return {
            "source_fields": [],
            "target_fields": [],
            "mappings": mappings,
        }

    # No project filter: ensure at least one dataset exists for UI usability.
    if not _compat_mappings:
        return _seed_for_project("default")

    return {
        "source_fields": [],
        "target_fields": [],
        "mappings": list(_compat_mappings.values()),
    }


async def auto_map_compat(payload: dict):
    project_id = str((payload or {}).get("project_id") or "default")
    data = await list_mappings_compat(project_id=project_id)
    mappings = data.get("mappings", []) if isinstance(data, dict) else []

    # Auto-map marks all available mappings as auto when triggered.
    for mapping in mappings:
        mapping_id = str(mapping.get("id") or "")
        if not mapping_id:
            continue
        mapping["status"] = "auto"
        _compat_mappings[mapping_id] = mapping

    return {
        "source_fields": data.get("source_fields", []) if isinstance(data, dict) else [],
        "target_fields": data.get("target_fields", []) if isinstance(data, dict) else [],
        "mappings": mappings,
        "status": "ok",
    }


async def update_mapping_compat(mapping_id: str, payload: dict):
    existing = _compat_mappings.get(mapping_id, {"id": mapping_id, "project_id": (payload or {}).get("project_id")})
    existing.update(payload or {})
    existing["id"] = mapping_id
    _compat_mappings[mapping_id] = existing
    return existing


async def delete_mappings_compat(project_id: Optional[str] = None):
    if project_id:
        for mapping_id in [k for k, v in _compat_mappings.items() if str(v.get("project_id") or "") == str(project_id)]:
            _compat_mappings.pop(mapping_id, None)
    else:
        _compat_mappings.clear()
    return Response(status_code=204)


