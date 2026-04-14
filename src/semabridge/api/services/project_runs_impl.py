import time as _time
import uuid

from semabridge.api.services.project_shared import *
from semabridge.api.services.project_shared import (
    _compat_default_project_yaml,
    _compat_ensure_loaded,
    _compat_folders,
    _compat_job_config,
    _compat_load_repo_yaml_text,
    _compat_mappings,
    _compat_now_iso,
    _compat_project_configs,
    _compat_project_runs,
    _compat_project_schedules,
    _compat_project_snapshots,
    _compat_projects,
    _compat_save_store,
    _compat_snapshot_groups,
)
from semabridge.api.services.project_mapping_engine import build_entity_mappings


def _compat_parse_project_cfg_dict(project_cfg: str) -> Dict[str, Any]:
    try:
        parsed = yaml.safe_load(project_cfg) or {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _compat_source_model_names_from_project_cfg(project_cfg: str) -> List[str]:
    parsed = _compat_parse_project_cfg_dict(project_cfg)
    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    model_names: List[str] = []

    source_models = source_cfg.get("models") if isinstance(source_cfg.get("models"), list) else []
    for raw_name in source_models:
        name = str(raw_name or "").strip()
        if name:
            model_names.append(name)

    if not model_names:
        source_model = source_cfg.get("model")
        if isinstance(source_model, str):
            source_model = source_model.strip()
            if source_model and source_model != "*":
                model_names.append(source_model)

    if not model_names:
        top_level_models = parsed.get("models") if isinstance(parsed.get("models"), list) else []
        for raw_name in top_level_models:
            name = str(raw_name or "").strip()
            if name:
                model_names.append(name)

    return model_names


def _extract_source_type_from_project_cfg(project_cfg: str, fallback: str = "fabric") -> str:
    parsed = _compat_parse_project_cfg_dict(project_cfg)
    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    source_type = str(source_cfg.get("type") or fallback).strip().lower()
    return source_type or fallback


def _compat_selected_intermediate_format(project_cfg: str) -> str:
    parsed = _compat_parse_project_cfg_dict(project_cfg)
    ui_cfg = parsed.get("ui") if isinstance(parsed.get("ui"), dict) else {}
    requested = str(
        ui_cfg.get("output_format")
        or ui_cfg.get("intermediate_format")
        or parsed.get("output_format")
        or "sml"
    ).strip().lower()
    return "osi" if requested == "osi" else "sml"


def _compat_connector_identifier(connector_cfg: Dict[str, Any], fallback: str) -> str:
    if not isinstance(connector_cfg, dict):
        return fallback
    for key in ("workspace_id", "database", "schema", "catalog", "dataset_id", "model_id", "name"):
        val = connector_cfg.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return fallback


def _compat_connector_descriptors(project_cfg: str) -> Dict[str, Any]:
    parsed = _compat_parse_project_cfg_dict(project_cfg)
    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    source_type = str(source_cfg.get("type") or "repository").strip().lower() or "repository"
    source_descriptor = {
        "connector_type": source_type,
        "connector_identifier": _compat_connector_identifier(source_cfg, source_type),
    }

    target_descriptors: List[Dict[str, Any]] = []
    targets = parsed.get("targets") if isinstance(parsed.get("targets"), list) else []
    for idx, target_cfg in enumerate(targets):
        if not isinstance(target_cfg, dict):
            continue
        target_type = str(target_cfg.get("type") or "target").strip().lower() or "target"
        target_descriptors.append({
            "target_id": f"target-{idx + 1}",
            "connector_type": target_type,
            "connector_identifier": _compat_connector_identifier(target_cfg, f"{target_type}-{idx + 1}"),
        })

    if not target_descriptors:
        single_target = parsed.get("target") if isinstance(parsed.get("target"), dict) else {}
        single_type = str(single_target.get("type") or "target").strip().lower() or "target"
        target_descriptors.append({
            "target_id": "target-1",
            "connector_type": single_type,
            "connector_identifier": _compat_connector_identifier(single_target, single_type),
        })

    return {"source": source_descriptor, "targets": target_descriptors}


def _compat_latest_sml_state(project_id: str, preferred_snapshot_id: str = "") -> Dict[str, Any]:
    sid = str(preferred_snapshot_id or "").strip()
    if sid:
        try:
            snap = db_manager.get_snapshot(sid)
            if snap and isinstance(getattr(snap, "sml_blob", None), dict):
                return snap.sml_blob
        except Exception:
            pass

    for run in _compat_project_runs.get(project_id, []):
        if not isinstance(run, dict):
            continue
        summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
        sid = str(summary.get("sml_snapshot_id") or "").strip()
        if not sid:
            continue
        try:
            snap = db_manager.get_snapshot(sid)
            if snap and isinstance(getattr(snap, "sml_blob", None), dict):
                return snap.sml_blob
        except Exception:
            continue
    return {}


def _compat_state_in_selected_format(state_sml: Dict[str, Any], selected_format: str) -> Dict[str, Any]:
    if not isinstance(state_sml, dict) or not state_sml or selected_format != "osi":
        return state_sml if isinstance(state_sml, dict) else {}
    try:
        from semabridge.formats.sml.models import SMLModel
        from semabridge.converter.sml_to_osi import SMLToOSIConverter

        return SMLToOSIConverter().to_osi(SMLModel.model_validate(state_sml)).model_dump(mode="json")
    except Exception:
        return state_sml


def _compat_create_snapshot_group(project_id: str, created_by: str, label: str, origin: str, run_id: Optional[str] = None) -> str:
    _compat_snapshot_groups.setdefault(project_id, [])
    group_id = f"sg-{uuid.uuid4().hex}"
    _compat_snapshot_groups[project_id].insert(0, {
        "id": group_id,
        "project_id": project_id,
        "created_by": created_by,
        "label": label,
        "snapshot_origin": origin,
        "run_id": run_id,
        "created_at": _compat_now_iso(),
    })
    return group_id


def _compat_version_metadata() -> Dict[str, Any]:
    return {"semabridge_version": "unknown", "connector_versions": {}, "rule_pack_version": "unknown"}


def _compat_capture_snapshots_for_run(
    *,
    project_id: str,
    run: Dict[str, Any],
    project_cfg: str,
    stage: str,
    preferred_snapshot_id: str = "",
) -> None:
    _compat_project_snapshots.setdefault(project_id, [])
    selected_format = _compat_selected_intermediate_format(project_cfg)
    connector_descriptors = _compat_connector_descriptors(project_cfg)
    state_blob = _compat_state_in_selected_format(_compat_latest_sml_state(project_id, preferred_snapshot_id), selected_format)
    run_id = str(run.get("run_id") or run.get("id") or "")
    origin = "RUN_BEFORE" if stage == "before" else "RUN_AFTER"
    timing = "before" if stage == "before" else "after"
    group_id = _compat_create_snapshot_group(project_id, "system", f"{origin.lower()}-{run_id[:8]}", origin, run_id)

    source_row = {
        "snapshot_id": f"psnap-{uuid.uuid4().hex}",
        "project_id": project_id,
        "run_id": run_id,
        "stage": stage,
        "role": "source",
        "timing": timing,
        "target_index": None,
        "target_id": None,
        "connector": connector_descriptors["source"].get("connector_type"),
        "intermediate_format": selected_format,
        "state": state_blob,
        "snapshot_origin": origin,
        "snapshot_group_id": group_id,
        "system_role": "SOURCE",
        "connector_type": connector_descriptors["source"].get("connector_type"),
        "connector_identifier": connector_descriptors["source"].get("connector_identifier"),
        "format_type": selected_format.upper(),
        "artifact": state_blob,
        "version_metadata": _compat_version_metadata(),
        "project_config_yaml": project_cfg,
        "created_at": _compat_now_iso(),
    }
    _compat_project_snapshots[project_id].insert(0, source_row)
    run[f"{stage}_src_snapshot_id"] = source_row["snapshot_id"]

    target_ids: List[str] = []
    for idx, target_descriptor in enumerate(connector_descriptors.get("targets") or []):
        target_id = f"psnap-{uuid.uuid4().hex}"
        target_ids.append(target_id)
        _compat_project_snapshots[project_id].insert(0, {
            "snapshot_id": target_id,
            "project_id": project_id,
            "run_id": run_id,
            "stage": stage,
            "role": "target",
            "timing": timing,
            "target_index": idx,
            "target_id": target_descriptor.get("target_id") or f"target-{idx + 1}",
            "connector": target_descriptor.get("connector_type") or "target",
            "intermediate_format": selected_format,
            "state": state_blob,
            "snapshot_origin": origin,
            "snapshot_group_id": group_id,
            "system_role": "TARGET",
            "connector_type": target_descriptor.get("connector_type") or "target",
            "connector_identifier": target_descriptor.get("connector_identifier") or f"target-{idx + 1}",
            "format_type": selected_format.upper(),
            "artifact": state_blob,
            "version_metadata": _compat_version_metadata(),
            "project_config_yaml": project_cfg,
            "created_at": _compat_now_iso(),
        })
    run[f"{stage}_target_snapshot_ids"] = target_ids
    run[f"{stage}_targ1_snapshot_id"] = target_ids[0] if len(target_ids) > 0 else None
    run[f"{stage}_targ2_snapshot_id"] = target_ids[1] if len(target_ids) > 1 else None


def _compat_apply_restore_overrides(config_yaml: str, overrides: Dict[str, Any]) -> str:
    parsed = _compat_parse_project_cfg_dict(config_yaml) or {}
    for section in ("source", "target", "ui"):
        value = overrides.get(section)
        if isinstance(value, dict):
            existing = parsed.get(section) if isinstance(parsed.get(section), dict) else {}
            existing.update(value)
            parsed[section] = existing
    if isinstance(overrides.get("targets"), list):
        parsed["targets"] = overrides.get("targets") or []
    return yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)


def _compat_diff_states(left: Any, right: Any, *, max_changes: int = 200) -> Dict[str, Any]:
    changes: List[Dict[str, Any]] = []
    summary = {"added": 0, "removed": 0, "modified": 0}

    def record(change_type: str, path: str) -> None:
        summary[change_type] += 1
        if len(changes) < max_changes:
            changes.append({"type": change_type, "path": path or "$"})

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a.keys()) - set(b.keys())):
                record("removed", f"{path}.{key}" if path else str(key))
            for key in sorted(set(b.keys()) - set(a.keys())):
                record("added", f"{path}.{key}" if path else str(key))
            for key in sorted(set(a.keys()) & set(b.keys())):
                walk(a.get(key), b.get(key), f"{path}.{key}" if path else str(key))
            return
        if isinstance(a, list) and isinstance(b, list):
            min_len = min(len(a), len(b))
            for idx in range(min_len):
                walk(a[idx], b[idx], f"{path}[{idx}]" if path else f"[{idx}]")
            for idx in range(min_len, len(a)):
                record("removed", f"{path}[{idx}]" if path else f"[{idx}]")
            for idx in range(min_len, len(b)):
                record("added", f"{path}[{idx}]" if path else f"[{idx}]")
            return
        if a != b:
            record("modified", path)

    walk(left, right, "")
    total = summary["added"] + summary["removed"] + summary["modified"]
    return {"exact_match": total == 0, "summary": {**summary, "total_changes": total}, "changes": changes}


def _collect_step_logs(summary: Dict[str, Any], prefix: str = "") -> List[str]:
    lines: List[str] = []
    for step in (summary.get("steps_completed") or []):
        if not isinstance(step, dict):
            continue
        detail = f" - {step.get('message')}" if step.get("message") else ""
        prefix_text = f"{prefix} " if prefix else ""
        lines.append(f"{str(step.get('status') or 'info').upper()} {prefix_text}Stage {step.get('step_number', '?')}: {step.get('step_name') or 'Unknown'}{detail}")
    for err in (summary.get("errors") or []):
        if not isinstance(err, dict):
            continue
        prefix_text = f"{prefix} " if prefix else ""
        lines.append(f"ERROR {prefix_text}Stage {err.get('step_number', '?')} ({err.get('step_name') or 'Execution'}) - {err.get('message') or 'Unknown error'}")
    return lines


def _build_run_logs(sync_result: Dict[str, Any]) -> List[str]:
    logs = _collect_step_logs(sync_result.get("summary") if isinstance(sync_result.get("summary"), dict) else {})
    for result in (sync_result.get("results") or []):
        if not isinstance(result, dict):
            continue
        logs.extend(_collect_step_logs(result.get("summary") if isinstance(result.get("summary"), dict) else {}, f"[{str(result.get('model') or 'Model')}]"))
    return logs


def _build_stage_states(sync_result: Dict[str, Any]) -> List[Dict[str, str]]:
    defaults = [
        {"id": "extraction", "label": "Extraction", "status": "pending"},
        {"id": "osi_conversion", "label": "OSI Conversion", "status": "pending"},
        {"id": "sml_generation", "label": "SML Generation", "status": "pending"},
        {"id": "snowflake_deployment", "label": "Snowflake Deployment", "status": "pending"},
    ]
    steps = sync_result.get("summary", {}).get("steps_completed", []) if isinstance(sync_result.get("summary"), dict) else []
    if not isinstance(steps, list):
        return defaults
    status_by_stage = {item["id"]: item["status"] for item in defaults}
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_number = int(step.get("step_number") or 0)
        normalized = str(step.get("status") or "pending").lower()
        if step_number == 4:
            status_by_stage["extraction"] = normalized
        elif step_number == 6:
            status_by_stage["osi_conversion"] = normalized
            status_by_stage["sml_generation"] = normalized
        elif step_number in (8, 9):
            status_by_stage["snowflake_deployment"] = normalized
    return [{"id": item["id"], "label": item["label"], "status": status_by_stage.get(item["id"], item["status"])} for item in defaults]


async def list_project_snapshots_compat(
    project_id: str,
    role: Optional[str] = Query(default=None),
    stage: Optional[str] = Query(default=None),
    origin: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
    group_id: Optional[str] = Query(default=None),
    include_state: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
):
    _compat_ensure_loaded()
    rows: List[Dict[str, Any]] = []
    for row in _compat_project_snapshots.get(project_id, []):
        if not isinstance(row, dict):
            continue
        if role and str(row.get("role") or row.get("system_role") or "").lower() != str(role).lower():
            continue
        if stage and str(row.get("stage") or row.get("timing") or "").lower() != str(stage).lower():
            continue
        if origin and str(row.get("snapshot_origin") or "").upper() != str(origin).upper():
            continue
        if run_id and str(row.get("run_id") or "") != str(run_id):
            continue
        if group_id and str(row.get("snapshot_group_id") or "") != str(group_id):
            continue
        out = dict(row)
        if not include_state:
            out.pop("state", None)
            out.pop("project_config_yaml", None)
        rows.append(out)
    return {"project_id": project_id, "count": len(rows[:limit]), "snapshots": rows[:limit]}


async def list_snapshot_groups_compat(project_id: str, limit: int = Query(default=200, ge=1, le=500)):
    _compat_ensure_loaded()
    groups = list(_compat_snapshot_groups.get(project_id, []))
    return {"project_id": project_id, "count": len(groups[:limit]), "groups": groups[:limit]}


async def capture_manual_snapshots_compat(project_id: str, payload: Dict[str, Any]):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    # Older compat-store files may have a project row without initialized
    # snapshot/group collections. Seed them defensively before inserts.
    _compat_project_snapshots.setdefault(project_id, [])
    _compat_snapshot_groups.setdefault(project_id, [])

    project_cfg = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(_compat_projects[project_id])
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    include_source = bool(scope.get("source", True))
    include_targets = scope.get("targets", "all")
    if not include_source and not include_targets:
        raise HTTPException(status_code=400, detail="At least one scope must be selected")

    selected_format = (str(payload.get("format") or "").strip().lower() or _compat_selected_intermediate_format(project_cfg))
    selected_format = "osi" if selected_format == "osi" else "sml"
    connector_descriptors = _compat_connector_descriptors(project_cfg)
    state_blob = _compat_state_in_selected_format(_compat_latest_sml_state(project_id), selected_format)
    group_id = _compat_create_snapshot_group(project_id, "user", str(payload.get("label") or "").strip() or f"manual-{_compat_now_iso()[:19]}", "MANUAL", None)

    created_snapshot_ids: List[str] = []
    if include_source:
        sid = f"psnap-{uuid.uuid4().hex}"
        created_snapshot_ids.append(sid)
        _compat_project_snapshots[project_id].insert(0, {"snapshot_id": sid, "project_id": project_id, "run_id": None, "stage": "manual", "role": "source", "timing": None, "target_index": None, "target_id": None, "connector": connector_descriptors["source"].get("connector_type"), "intermediate_format": selected_format, "state": state_blob, "snapshot_origin": "MANUAL", "snapshot_group_id": group_id, "system_role": "SOURCE", "project_config_yaml": project_cfg, "created_at": _compat_now_iso()})
    if include_targets:
        for idx, target_descriptor in enumerate(connector_descriptors.get("targets") or []):
            sid = f"psnap-{uuid.uuid4().hex}"
            created_snapshot_ids.append(sid)
            _compat_project_snapshots[project_id].insert(0, {"snapshot_id": sid, "project_id": project_id, "run_id": None, "stage": "manual", "role": "target", "timing": None, "target_index": idx, "target_id": target_descriptor.get("target_id") or f"target-{idx + 1}", "connector": target_descriptor.get("connector_type") or "target", "intermediate_format": selected_format, "state": state_blob, "snapshot_origin": "MANUAL", "snapshot_group_id": group_id, "system_role": "TARGET", "project_config_yaml": project_cfg, "created_at": _compat_now_iso()})
    _compat_save_store()
    return {"project_id": project_id, "snapshot_group_id": group_id, "snapshot_ids": created_snapshot_ids, "count": len(created_snapshot_ids), "intermediate_format": selected_format}


async def compare_project_snapshots_compat(
    project_id: str,
    from_snapshot_id: str,
    to_snapshot_id: str,
    max_changes: int = Query(default=200, ge=1, le=1000),
    include_states: bool = Query(default=False),
):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    rows = [row for row in _compat_project_snapshots.get(project_id, []) if isinstance(row, dict)]
    from_row = next((r for r in rows if str(r.get("snapshot_id") or "") == str(from_snapshot_id)), None)
    to_row = next((r for r in rows if str(r.get("snapshot_id") or "") == str(to_snapshot_id)), None)
    if not from_row:
        raise HTTPException(status_code=404, detail="from_snapshot_id not found")
    if not to_row:
        raise HTTPException(status_code=404, detail="to_snapshot_id not found")
    left_state = from_row.get("state") if isinstance(from_row.get("state"), dict) else {}
    right_state = to_row.get("state") if isinstance(to_row.get("state"), dict) else {}
    diff = _compat_diff_states(left_state, right_state, max_changes=max_changes)
    payload: Dict[str, Any] = {
        "project_id": project_id,
        "from_snapshot": {
            "snapshot_id": from_snapshot_id,
            "project_id": project_id,
            "run_id": from_row.get("run_id"),
            "role": from_row.get("role"),
            "stage": from_row.get("stage"),
            "timing": from_row.get("timing"),
            "origin": from_row.get("snapshot_origin"),
            "group_id": from_row.get("snapshot_group_id"),
            "created_at": from_row.get("created_at"),
            "format": from_row.get("intermediate_format"),
            "connector": from_row.get("connector"),
            "connector_type": from_row.get("connector_type") or from_row.get("connector"),
            "connector_identifier": from_row.get("connector_identifier"),
            "target_id": from_row.get("target_id"),
            "target_index": from_row.get("target_index"),
            "system_role": from_row.get("system_role"),
            "project_config_yaml": from_row.get("project_config_yaml") or "",
        },
        "to_snapshot": {
            "snapshot_id": to_snapshot_id,
            "project_id": project_id,
            "run_id": to_row.get("run_id"),
            "role": to_row.get("role"),
            "stage": to_row.get("stage"),
            "timing": to_row.get("timing"),
            "origin": to_row.get("snapshot_origin"),
            "group_id": to_row.get("snapshot_group_id"),
            "created_at": to_row.get("created_at"),
            "format": to_row.get("intermediate_format"),
            "connector": to_row.get("connector"),
            "connector_type": to_row.get("connector_type") or to_row.get("connector"),
            "connector_identifier": to_row.get("connector_identifier"),
            "target_id": to_row.get("target_id"),
            "target_index": to_row.get("target_index"),
            "system_role": to_row.get("system_role"),
            "project_config_yaml": to_row.get("project_config_yaml") or "",
        },
        **diff,
    }
    if include_states:
        payload["from_state"] = left_state
        payload["to_state"] = right_state
    return payload


async def get_project_runs_compat(project_id: str):
    _compat_ensure_loaded()
    return _compat_project_runs.get(project_id, [])


def _create_project_run(
    project_id: str,
    schedule_label: str = "Manual",
    run_type: str = "SYNC",
    project_cfg_override: Optional[str] = None,
    restore_snapshot_id: Optional[str] = None,
) -> tuple[dict, str, float]:
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    started = _time.time()
    run_id = f"run-{int(_time.time() * 1000)}"
    project_cfg = project_cfg_override or _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(_compat_projects[project_id])
    run = {
        "run_id": run_id,
        "id": run_id,
        "project_id": project_id,
        "project_name": _compat_projects[project_id].get("name", project_id),
        "run_type": str(run_type or "SYNC").upper(),
        "schedule": schedule_label,
        "status": "running",
        "source_type": _extract_source_type_from_project_cfg(project_cfg, fallback=str(_compat_projects[project_id].get("source") or "fabric").lower()),
        "message": "Execution started.",
        "logs": ["LIVE Run queued. Waiting for execution engine..."],
        "stage_states": _build_stage_states({}),
        "duration_ms": 0,
        "started_at": _compat_now_iso(),
        "completed_at": None,
    }
    if restore_snapshot_id:
        run["restore_snapshot_id"] = restore_snapshot_id
    _compat_project_runs.setdefault(project_id, []).insert(0, run)
    try:
        _compat_capture_snapshots_for_run(project_id=project_id, run=run, project_cfg=project_cfg, stage="before")
    except Exception as exc:
        logger.debug("Pre-sync snapshot capture skipped for %s: %s", project_id, exc)
    _compat_save_store()
    return run, project_cfg, started


async def _perform_project_run(run: dict, project_cfg: str, started: float) -> dict:
    project_id = str(run.get("project_id") or "")
    try:
        from semabridge.api.services.core_domain_service import sync_models

        sync_result = await sync_models({"content": project_cfg})
        run["duration_ms"] = int((_time.time() - started) * 1000)
        run["completed_at"] = _compat_now_iso()
        overall = str((sync_result or {}).get("status") or "").lower()
        run["status"] = "success" if overall == "success" else "warning" if overall == "partial" else "failed"
        run["summary"] = (sync_result or {}).get("summary") or {}
        run["results"] = (sync_result or {}).get("results") or []
        run["models_synced"] = int((sync_result or {}).get("models_synced") or 0)
        run["total_models"] = int((sync_result or {}).get("total_models") or 0)
        run["logs"] = _build_run_logs(sync_result or {})
        run["stage_states"] = _build_stage_states(sync_result or {})
        run["message"] = "Execution completed successfully." if run["status"] == "success" else "Execution completed with warnings." if run["status"] == "warning" else str((((run["summary"].get("errors") or [{}])[0]).get("message")) or "Execution failed.")
        try:
            preferred_snapshot_id = str((run.get("summary") or {}).get("sml_snapshot_id") or "")
            _compat_capture_snapshots_for_run(project_id=project_id, run=run, project_cfg=project_cfg, stage="after", preferred_snapshot_id=preferred_snapshot_id)
        except Exception as exc:
            logger.debug("Post-sync snapshot capture skipped for %s: %s", project_id, exc)
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
        run["message"] = str(exc)
        run["completed_at"] = _compat_now_iso()
        run["logs"] = [f"ERROR Execution failed: {exc}"]
        try:
            _compat_capture_snapshots_for_run(project_id=project_id, run=run, project_cfg=project_cfg, stage="after")
        except Exception as snap_exc:
            logger.debug("Post-failure snapshot capture skipped for %s: %s", project_id, snap_exc)
    _compat_save_store()
    return run


async def _execute_project_run(project_id: str, schedule_label: str = "Manual") -> dict:
    run, project_cfg, started = _create_project_run(project_id, schedule_label)
    return await _perform_project_run(run, project_cfg, started)


def _run_project_background(run: dict, project_cfg: str, started: float) -> None:
    import asyncio

    asyncio.run(_perform_project_run(run, project_cfg, started))


async def restore_project_version_compat(project_id: str, payload: Dict[str, Any], background_tasks: BackgroundTasks):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    snapshot_id = str((payload or {}).get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise HTTPException(status_code=400, detail="snapshot_id is required")
    matches = [row for row in _compat_project_snapshots.get(project_id, []) if isinstance(row, dict) and str(row.get("snapshot_id") or "") == snapshot_id]
    if not matches:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    snapshot_row = matches[0]
    config_yaml = str(snapshot_row.get("project_config_yaml") or _compat_project_configs.get(project_id) or _compat_default_project_yaml(_compat_projects[project_id]))
    overrides = (payload or {}).get("overrides")
    if isinstance(overrides, dict) and overrides:
        config_yaml = _compat_apply_restore_overrides(config_yaml, overrides)
    _compat_project_configs[project_id] = config_yaml
    _compat_projects[project_id]["updated_at"] = _compat_now_iso()
    restore_run, restore_cfg, restore_started = _create_project_run(project_id, "Manual", run_type="RESTORE", project_cfg_override=config_yaml, restore_snapshot_id=snapshot_id)
    background_tasks.add_task(_run_project_background, restore_run, restore_cfg, restore_started)
    _compat_save_store()
    return {"status": "restored", "project_id": project_id, "snapshot_id": snapshot_id, "run_id": restore_run.get("id"), "run_type": "RESTORE", "intermediate_format": snapshot_row.get("intermediate_format") or "sml", "message": "Project configuration restored and restore run started.", "config_yaml": config_yaml}


async def run_project_now_compat(project_id: str, background_tasks: BackgroundTasks, payload: Optional[Dict[str, Any]] = None):
    if bool((payload or {}).get("dry_run", False)):
        preview_payload = dict(payload or {})
        preview_payload["project_id"] = project_id
        preview_payload["dry_run"] = True
        preview_result = await auto_map_compat(preview_payload)
        return {
            **preview_result,
            "status": "ok",
            "run_type": "DRY_RUN",
            "message": "Dry run preview generated using the shared run pipeline.",
        }

    run_type = str((payload or {}).get("run_type") or "SYNC").upper()
    if run_type not in {"SYNC", "RESTORE"}:
        run_type = "SYNC"
    restore_snapshot_id = str((payload or {}).get("restore_snapshot_id") or "").strip() or None
    run, project_cfg, started = _create_project_run(project_id, "Manual", run_type=run_type, restore_snapshot_id=restore_snapshot_id)
    background_tasks.add_task(_run_project_background, run, project_cfg, started)
    return {"run_id": run["id"], "status": "running", "run_type": run_type, "message": "Sync started in background"}


async def list_folders_compat():
    return list(_compat_folders.values())


async def create_folder_compat(payload: dict):
    folder_id = str((payload or {}).get("id") or (payload or {}).get("folder_id") or f"folder-{int(_time.time() * 1000)}")
    folder = {"id": folder_id, "folder_id": folder_id, "name": (payload or {}).get("name") or f"Folder {folder_id[-4:]}", "color": (payload or {}).get("color") or "#6366f1"}
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
        raise HTTPException(status_code=404, detail="Project not found")
    schedule = scheduler_service.get_project_schedule(project_id) or _compat_project_schedules.get(project_id)
    if schedule:
        return schedule
    return {"project_id": project_id, "schedule_type": "manual", "enabled": False}


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
        raise HTTPException(status_code=404, detail="Project not found")
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
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    _compat_save_store()
    return {"status": "saved", **merged}


async def trigger_job_compat(payload: dict, background_tasks: BackgroundTasks):
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


def _compat_mapping_project_ids(project_id: str = "") -> List[str]:
    pid = str(project_id or "").strip()
    if pid:
        return [pid]
    return [str(row.get("project_id") or row.get("id") or "").strip() for row in _compat_projects.values() if str(row.get("project_id") or row.get("id") or "").strip()]


def _compat_existing_entity_mappings(project_id: str) -> Dict[str, Dict[str, Any]]:
    existing: Dict[str, Dict[str, Any]] = {}
    for mapping in _compat_mappings.values():
        if not isinstance(mapping, dict):
            continue
        if str(mapping.get("project_id") or "") != str(project_id):
            continue
        source_path = str(mapping.get("source_path") or "").strip()
        if not source_path:
            continue
        existing[source_path] = dict(mapping)
    return existing


def _compat_build_project_entity_mappings(
    project_id: str,
    save_store: bool = True,
    target_connector: Optional[str] = None,
) -> Dict[str, Any]:
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    latest_model = _compat_latest_sml_state(project_id)
    if not latest_model:
        project_cfg = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(_compat_projects[project_id])
        parsed_cfg = _compat_parse_project_cfg_dict(project_cfg)
        fallback_model_name = str(parsed_cfg.get("project_name") or _compat_projects[project_id].get("name") or "model")
        fallback_model_names = _compat_source_model_names_from_project_cfg(project_cfg)
        latest_model = {
            "unique_name": fallback_model_name,
            "datasets": [{"unique_name": model_name, "columns": []} for model_name in fallback_model_names],
            "metrics": [],
        }

    built = build_entity_mappings(
        project_id=project_id,
        model=latest_model,
        existing_mappings=_compat_existing_entity_mappings(project_id),
        session_key=f"{project_id}-mapping-session",
        target_connector=target_connector,
    )

    persisted: List[Dict[str, Any]] = []
    for mapping in built.get("mappings", []):
        mapping_id = str(mapping.get("id") or "")
        if not mapping_id:
            continue
        existing = _compat_mappings.get(mapping_id, {})
        merged = {**existing, **mapping}
        if existing.get("is_user_edited"):
            manual_target = str(existing.get("target_name") or "").strip()
            if manual_target:
                merged["target_name"] = manual_target
                merged["status"] = "manual"
                merged["is_user_edited"] = True
        if save_store:
            _compat_mappings[mapping_id] = merged
        persisted.append(merged)

    if save_store:
        _compat_save_store()
    return {
        "project_id": project_id,
        "session_key": built.get("session_key"),
        "model_name": built.get("model_name"),
        "source_fields": built.get("source_fields", []),
        "target_fields": built.get("target_fields", []),
        "mappings": persisted,
        "collisions": built.get("collisions", []),
    }


def _compat_preview_model_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    selected_model_names = payload.get("selected_model_names") if isinstance(payload.get("selected_model_names"), list) else []
    normalized_model_names = [str(item or "").strip() for item in selected_model_names if str(item or "").strip()]
    if not normalized_model_names:
        normalized_model_names = _compat_source_model_names_from_project_cfg(str(payload.get("config_yaml") or ""))
    config_project_name = _compat_parse_project_cfg_dict(str(payload.get("config_yaml") or "")).get("project_name")
    project_name = str(
        config_project_name
        or payload.get("project_name")
        or payload.get("name")
        or "model"
    ).strip() or "model"

    datasets = []
    for raw_name in normalized_model_names:
        datasets.append({
            "unique_name": raw_name,
            "columns": [
                {"unique_name": "id", "data_type": "integer"},
                {"unique_name": "name", "data_type": "string"},
                {"unique_name": "created_at", "data_type": "timestamp"},
                {"unique_name": "updated_at", "data_type": "timestamp"},
            ],
        })

    return {
        "unique_name": project_name,
        "datasets": datasets,
        "metrics": [],
    }


def _compat_format_mapping_groups(mapping_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in mapping_payload.get("mappings", []):
        if not isinstance(row, dict):
            continue
        if str(row.get("entity_kind") or "") == "table":
            source_path = str(row.get("source_path") or "")
            grouped[source_path] = {
                "id": row.get("id"),
                "source": row.get("source_name"),
                "target": row.get("target_name"),
                "source_path": source_path,
                "type": "table",
                "status": "manual" if row.get("is_user_edited") else "auto-detected",
                "collision_detected": bool(row.get("collision_detected")),
                "validation_status": row.get("validation_status"),
                "validation_code": row.get("validation_code"),
                "validation_message": row.get("validation_message"),
                "suggested_target_name": row.get("suggested_target_name"),
                "columns": [],
            }

    for row in mapping_payload.get("mappings", []):
        if not isinstance(row, dict):
            continue
        if str(row.get("entity_kind") or "") != "column":
            continue
        parent_key = str(row.get("parent_source_path") or "")
        parent = grouped.get(parent_key)
        if not parent:
            continue
        parent["columns"].append({
            "source": row.get("source_name"),
            "target": row.get("target_name"),
            "source_path": row.get("source_path"),
            "type": row.get("target_data_type") or row.get("source_data_type") or "unknown",
            "key": str(row.get("source_name") or "").lower() == "id",
            "collision_detected": bool(row.get("collision_detected")),
            "validation_status": row.get("validation_status"),
            "validation_code": row.get("validation_code"),
            "validation_message": row.get("validation_message"),
            "suggested_target_name": row.get("suggested_target_name"),
        })

    return list(grouped.values())


async def list_mappings_compat(project_id: Optional[str] = None):
    _compat_ensure_loaded()
    project_ids = _compat_mapping_project_ids(str(project_id or "").strip())
    if not project_ids:
        raise HTTPException(status_code=400, detail="project_id is required")

    if len(project_ids) == 1:
        data = _compat_build_project_entity_mappings(project_ids[0])
        return {
            "project_id": data.get("project_id"),
            "session_key": data.get("session_key"),
            "model_name": data.get("model_name"),
            "source_fields": data.get("source_fields", []),
            "target_fields": data.get("target_fields", []),
            "mappings": _compat_format_mapping_groups(data),
            "entity_mappings": data.get("mappings", []),
            "collisions": data.get("collisions", []),
        }

    combined_mappings: List[Dict[str, Any]] = []
    combined_source_fields: List[Dict[str, Any]] = []
    combined_target_fields: List[Dict[str, Any]] = []
    collisions: List[Dict[str, Any]] = []
    for pid in project_ids:
        data = _compat_build_project_entity_mappings(pid, save_store=False)
        combined_mappings.extend(data.get("mappings", []))
        combined_source_fields.extend(data.get("source_fields", []))
        combined_target_fields.extend(data.get("target_fields", []))
        collisions.extend(data.get("collisions", []))
    
    _compat_save_store()
    
    return {
        "project_id": None,
        "source_fields": combined_source_fields,
        "target_fields": combined_target_fields,
        "mappings": combined_mappings,
        "entity_mappings": combined_mappings,
        "collisions": collisions,
    }


async def auto_map_compat(payload: dict):
    project_id = str((payload or {}).get("project_id") or "").strip()
    selected_model_names = (payload or {}).get("selected_model_names") if isinstance((payload or {}).get("selected_model_names"), list) else []
    target_connectors = (payload or {}).get("target_connectors") if isinstance((payload or {}).get("target_connectors"), list) else []
    explicit_target = str((payload or {}).get("target_connector") or "").strip()
    target_connector = explicit_target or (str(target_connectors[0]).strip() if target_connectors else "")
    dry_run = bool((payload or {}).get("dry_run", False))
    config_yaml = str((payload or {}).get("config_yaml") or "").strip()

    if not project_id and not selected_model_names and _compat_projects:
        project_id = next(iter(_compat_projects.keys()))
    if not project_id and not selected_model_names:
        raise HTTPException(status_code=400, detail="project_id or selected_model_names is required")

    reset_manual = bool((payload or {}).get("reset_manual", False))
    preview_mode = dry_run and (bool(config_yaml) or bool(selected_model_names))
    if project_id and not preview_mode:
        if not dry_run:
            for mapping_id, mapping in list(_compat_mappings.items()):
                if str(mapping.get("project_id") or "") != project_id:
                    continue
                if reset_manual:
                    _compat_mappings.pop(mapping_id, None)
                    continue
                mapping["status"] = "auto"
                mapping["is_user_edited"] = False
                mapping["target_name"] = ""

        data = _compat_build_project_entity_mappings(
            project_id,
            save_store=not dry_run,
            target_connector=target_connector,
        )
    else:
        preview_seed = config_yaml or '|'.join(str(item) for item in selected_model_names)
        preview_project_id = f"preview-{uuid.uuid5(uuid.NAMESPACE_DNS, preview_seed or project_id or 'preview').hex[:12]}"
        preview_model = _compat_preview_model_from_payload(payload or {})
        data = build_entity_mappings(
            project_id=preview_project_id,
            model=preview_model,
            existing_mappings={},
            session_key=f"{preview_project_id}-mapping-session",
            target_connector=target_connector,
        )
        data = {
            "project_id": preview_project_id,
            "session_key": data.get("session_key"),
            "model_name": data.get("model_name"),
            "source_fields": data.get("source_fields", []),
            "target_fields": data.get("target_fields", []),
            "mappings": data.get("mappings", []),
            "collisions": data.get("collisions", []),
        }

    grouped_mappings = _compat_format_mapping_groups(data)
    return {
        "project_id": data.get("project_id") or project_id,
        "source_fields": data.get("source_fields", []),
        "target_fields": data.get("target_fields", []),
        "mappings": grouped_mappings,
        "entity_mappings": data.get("mappings", []),
        "collisions": data.get("collisions", []),
        "status": "ok",
    }


async def update_mapping_compat(mapping_id: str, payload: dict):
    _compat_ensure_loaded()
    existing = _compat_mappings.get(mapping_id, {"id": mapping_id, "project_id": (payload or {}).get("project_id")})
    existing.update(payload or {})
    existing["id"] = mapping_id
    if "target_name" in (payload or {}):
        existing["target_name"] = str((payload or {}).get("target_name") or "").strip()
        existing["status"] = "manual"
        existing["is_user_edited"] = True
    existing["updated_at"] = _compat_now_iso()
    _compat_mappings[mapping_id] = existing
    _compat_save_store()
    return existing


async def delete_mappings_compat(project_id: Optional[str] = None):
    if project_id:
        for mapping_id in [k for k, v in _compat_mappings.items() if str(v.get("project_id") or "") == str(project_id)]:
            _compat_mappings.pop(mapping_id, None)
    else:
        _compat_mappings.clear()
    _compat_save_store()
    return Response(status_code=204)
