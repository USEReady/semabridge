import json
import time as _time
import uuid
import re
from typing import Any, Dict, List, Optional

import yaml

import semabridge.api.services.project_shared as project_shared

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
from semabridge.api.services.project_mapping_engine import (
    _extract_metric_source_tables,
    build_entity_mappings,
    sanitize_identifier,
)
from semabridge.utils.identifiers import IdentifierSanitizer


AUTO_MAP_SNOWFLAKE_RESERVED = {
    "SELECT", "GROUP", "ORDER", "TABLE", "COLUMN", "DATE", "FROM", "WHERE",
    "BY", "JOIN", "VIEW", "UNION", "INSERT", "UPDATE", "DELETE", "CREATE",
    "DROP", "HAVING", "LIMIT", "OFFSET", "INTO", "PRIMARY", "FOREIGN",
    "KEY", "REFERENCES", "DATABASE", "SCHEMA", "WAREHOUSE", "ACCOUNT",
}
_SNOWFLAKE_SANITIZER = IdentifierSanitizer(force_uppercase=True, suppress_reserved=True)


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


def _compat_normalize_sync_mode(value: Any) -> Optional[str]:
    sync_mode = str(value or "").strip().lower()
    return sync_mode if sync_mode in {"copy", "upsert"} else None


def _compat_sync_mode_for_restore_snapshot(project_id: str, snapshot_id: str, payload: Optional[Dict[str, Any]] = None) -> str:
    explicit = _compat_normalize_sync_mode((payload or {}).get("sync_mode"))
    if explicit:
        return explicit

    snapshot_run_id = ""
    for snap in _compat_project_snapshots.get(project_id, []):
        if not isinstance(snap, dict) or str(snap.get("snapshot_id") or "") != snapshot_id:
            continue
        stored = _compat_normalize_sync_mode(snap.get("sync_mode"))
        if stored:
            return stored
        snapshot_run_id = str(snap.get("run_id") or "").strip()
        break

    if snapshot_run_id:
        for run in _compat_project_runs.get(project_id, []):
            if isinstance(run, dict) and str(run.get("run_id") or run.get("id") or "") == snapshot_run_id:
                stored = _compat_normalize_sync_mode(run.get("sync_mode"))
                if stored:
                    return stored

    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Run, SnapshotRow
        from semabridge.repository.orm.session_factory import db_manager

        session = db_manager._session()
        try:
            snapshot = session.get(SnapshotRow, snapshot_id)
            if snapshot is not None:
                stored = _compat_normalize_sync_mode(getattr(snapshot, "sync_mode", None))
                if stored:
                    return stored
                snapshot_run_id = str(getattr(snapshot, "run_id", "") or "").strip()

            if snapshot_run_id:
                run = session.get(Run, snapshot_run_id)
                stored = _compat_normalize_sync_mode(getattr(run, "sync_mode", None) if run else None)
                if stored:
                    return stored

            rows = session.execute(select(Run).where(Run.project_id == project_id)).scalars().all()
            for run in rows:
                for attr_name in ("before_target_snapshot_ids", "after_target_snapshot_ids"):
                    raw_value = getattr(run, attr_name, None)
                    if not raw_value:
                        continue
                    try:
                        ids = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
                    except Exception:
                        ids = []
                    if snapshot_id in (ids or []):
                        stored = _compat_normalize_sync_mode(getattr(run, "sync_mode", None))
                        if stored:
                            return stored
        finally:
            session.close()
    except Exception as exc:
        logger.debug("Could not infer restore sync_mode for %s/%s: %s", project_id, snapshot_id, exc)

    return "copy"


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
        "sync_mode": _compat_normalize_sync_mode(run.get("sync_mode")) or "copy",
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
            "sync_mode": _compat_normalize_sync_mode(run.get("sync_mode")) or "copy",
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


def _diff_models(left_state: Dict[str, Any], right_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compare models/datasets between two states and return status for each."""

    def _extract_models(state: Dict[str, Any]) -> Dict[str, Any]:
        raw = state.get("datasets") or state.get("models") or []
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, list):
            # Support both 'name' (legacy) and 'unique_name' (SML)
            return {
                (m.get("unique_name") or m.get("name") or f"model_{i}"): m 
                for i, m in enumerate(raw) if isinstance(m, dict)
            }
        return {}

    left_models = _extract_models(left_state)
    right_models = _extract_models(right_state)
    all_names = sorted(set(left_models.keys()) | set(right_models.keys()))
    results: List[Dict[str, Any]] = []

    for name in all_names:
        left_model = left_models.get(name)
        right_model = right_models.get(name)

        if not left_model:
            status = "ADDED"
            details = {"message": "New model detected in target snapshot"}
        elif not right_model:
            status = "REMOVED"
            details = {"message": "Model removed in target snapshot"}
        else:
            try:
                if json.dumps(left_model, sort_keys=True) == json.dumps(right_model, sort_keys=True):
                    status = "UNCHANGED"
                    details = {}
                else:
                    status = "MODIFIED"
                    details = {"message": "Structural or metadata changes detected"}
            except Exception:
                status = "MODIFIED"
                details = {"message": "Changes detected (failed to hash)"}

        results.append({"name": name, "status": status, "details": details})

    return results


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
    left_state = from_row.get("state")
    if isinstance(left_state, str):
        try: left_state = json.loads(left_state)
        except Exception: left_state = {}
        
    right_state = to_row.get("state")
    if isinstance(right_state, str):
        try: right_state = json.loads(right_state)
        except Exception: right_state = {}

    if not isinstance(left_state, dict): left_state = {}
    if not isinstance(right_state, dict): right_state = {}
    models_diff = _diff_models(left_state, right_state)

    payload: Dict[str, Any] = {
        "metadata_diff": {
            "snapshot_a": {
                "id": from_snapshot_id,
                "format": from_row.get("intermediate_format"),
                "model_count": len(left_state.get("datasets") or left_state.get("models") or []),
                "taken_at": from_row.get("created_at") or from_row.get("timestamp"),
                "connector_id": from_row.get("connector_identifier"),
                "trigger": str(from_row.get("snapshot_origin") or "").lower(),
            },
            "snapshot_b": {
                "id": to_snapshot_id,
                "format": to_row.get("intermediate_format"),
                "model_count": len(right_state.get("datasets") or right_state.get("models") or []),
                "taken_at": to_row.get("created_at") or to_row.get("timestamp"),
                "connector_id": to_row.get("connector_identifier"),
                "trigger": str(to_row.get("snapshot_origin") or "").lower(),
            },
        },
        "models": models_diff,
    }
    if include_states:
        payload["from_state"] = left_state
        payload["to_state"] = right_state
    return payload


async def get_project_runs_compat(project_id: str):
    """
    Retrieve run history for a project.
    Falls back to ORM queries if the in-memory compatibility store is empty.
    """
    _compat_ensure_loaded()
    pid = str(project_id).strip()

    runs = _compat_project_runs.get(pid, [])
    if runs:
        return runs

    try:
        from semabridge.repository.orm.models import Run
        from sqlalchemy import select
        from semabridge.repository.orm.session_factory import db_manager

        session = db_manager._session()
        try:
            stmt = (
                select(Run)
                .where(Run.project_id == pid)
                .order_by(Run.started_at.desc())
                .limit(100)
            )
            rows = session.execute(stmt).scalars().all()

            results: List[Dict[str, Any]] = []
            for row in rows:
                def _parse_list(attr_name: str) -> List[Any]:
                    val = getattr(row, attr_name, None)
                    if not val:
                        return []
                    try:
                        return json.loads(val) if isinstance(val, str) else val
                    except Exception:
                        return []

                before_ids = _parse_list("before_target_snapshot_ids")
                after_ids = _parse_list("after_target_snapshot_ids")

                run_data = {
                    "run_id": row.run_id,
                    "id": row.run_id,
                    "project_id": row.project_id,
                    "run_type": getattr(row, "run_type", "SYNC") or "SYNC",
                    "sync_mode": _compat_normalize_sync_mode(getattr(row, "sync_mode", None)) or "copy",
                    "status": row.status or "unknown",
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "before_target_snapshot_ids": before_ids,
                    "after_target_snapshot_ids": after_ids,
                    "after_tgt_snapshots": after_ids,
                }
                results.append(run_data)

            if results:
                _compat_project_runs[pid] = results

            return results
        finally:
            session.close()
    except Exception as exc:
        logger.error("Failed to retrieve runs from ORM: %s", exc)
        return []


async def delete_project_snapshots_compat(project_id: str, snapshot_ids: List[str]):
    """
    Soft-delete snapshots after ensuring they are not referenced by any project runs.
    """
    _compat_ensure_loaded()
    from semabridge.repository.orm.models import SnapshotRow, Run
    from sqlalchemy import select, update, or_
    from semabridge.repository.orm.session_factory import db_manager

    session = db_manager._session()
    try:
        stmt = (
            select(Run)
            .where(Run.project_id == project_id)
            .where(
                or_(
                    Run.before_src_snapshot_id.in_(snapshot_ids),
                    Run.restore_snapshot_id.in_(snapshot_ids),
                )
            )
        )
        active_runs = session.execute(stmt).scalars().all()

        blocked_ids: List[str] = []
        for run in active_runs:
            for snapshot_id in snapshot_ids:
                if snapshot_id == run.before_src_snapshot_id or snapshot_id == run.restore_snapshot_id:
                    blocked_ids.append(snapshot_id)

        to_delete = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id not in blocked_ids]

        if to_delete:
            stmt = (
                update(SnapshotRow)
                .where(SnapshotRow.snapshot_id.in_(to_delete))
                .values(deleted_at=_compat_now_iso())
            )
            session.execute(stmt)
            session.commit()

        return {
            "deleted_count": len(to_delete),
            "blocked_ids": list(set(blocked_ids)),
            "status": "success" if not blocked_ids else "partial",
        }
    finally:
        session.close()


def _create_project_run(
    project_id: str,
    schedule_label: str = "Manual",
    run_type: str = "SYNC",
    project_cfg_override: Optional[str] = None,
    restore_snapshot_id: Optional[str] = None,
    sync_mode: str = "copy",
) -> tuple[dict, str, float]:
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    started = _time.time()
    run_id = f"run-{int(_time.time() * 1000)}"
    modular_bundle = project_shared._compat_load_modular_project(project_id)
    project_cfg = (
        project_cfg_override
        or (str(modular_bundle.get("config_yaml") or "") if modular_bundle else "")
        or _compat_project_configs.get(project_id)
        or _compat_load_repo_yaml_text()
        or _compat_default_project_yaml(_compat_projects[project_id])
    )
    run = {
        "run_id": run_id,
        "id": run_id,
        "project_id": project_id,
        "project_name": _compat_projects[project_id].get("name", project_id),
        "run_type": str(run_type or "SYNC").upper(),
        "schedule": schedule_label,
        "status": "running",
        "sync_mode": sync_mode,
        "source_type": _extract_source_type_from_project_cfg(project_cfg, fallback=str(_compat_projects[project_id].get("source") or "fabric").lower()),
        "message": "Execution started.",
        "logs": ["LIVE Run queued. Waiting for execution engine..."],
        "stage_states": _build_stage_states({}),
        "duration_ms": 0,
        "started_at": _compat_now_iso(),
        "taken_at": _compat_now_iso(),  # Required for UI "Invalid Date" fix
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

        sync_payload: Dict[str, Any] = {
            "content": project_cfg,
            "project_id": project_id,
            # Explicitly enable deployment — the config YAML controls the target
            # connector details, but the deploy flag must be set here so
            # execute_sync_request does not skip Stage 8/9.
            "deploy": True,
        }
        user_id = run.get("user_id")
        if user_id is not None and str(user_id).strip():
            sync_payload["user_id"] = user_id
        # Forward sync_mode and force so the execution engine applies the correct strategy
        sync_mode = str(run.get("sync_mode") or "copy").lower()
        if sync_mode not in {"copy", "upsert"}:
            sync_mode = "copy"
        sync_payload["sync_mode"] = sync_mode
        sync_payload["force"] = bool(run.get("force", False))
        sync_result = await sync_models(sync_payload)
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
        
        # ── Apply Retention Policy (§2.3) ────────────────────────────────────
        # We'll use a thread for now since we are inside _perform_project_run
        # which can be called outside of a FastAPI request context.
        import threading
        threading.Thread(target=_run_retention_background, args=(project_id,), daemon=True).start()
        # ─────────────────────────────────────────────────────────────────────
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
    finally:
        _compat_save_store()
    return run


async def _execute_project_run(project_id: str, schedule_label: str = "Manual") -> dict:
    run, project_cfg, started = _create_project_run(project_id, schedule_label)
    return await _perform_project_run(run, project_cfg, started)


def _run_project_background(run: dict, project_cfg: str, started: float) -> None:
    """Run project execution in a dedicated background thread with its own event loop."""
    import threading
    def _run_loop():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_perform_project_run(run, project_cfg, started))
        finally:
            loop.close()
    
    threading.Thread(target=_run_loop, daemon=True).start()


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
    sync_mode = _compat_sync_mode_for_restore_snapshot(project_id, snapshot_id, payload)
    restore_run, restore_cfg, restore_started = _create_project_run(
        project_id,
        "Manual",
        run_type="RESTORE",
        project_cfg_override=config_yaml,
        restore_snapshot_id=snapshot_id,
        sync_mode=sync_mode,
    )
    background_tasks.add_task(_perform_project_run, restore_run, restore_cfg, restore_started)
    _compat_save_store()
    return {"status": "restored", "project_id": project_id, "snapshot_id": snapshot_id, "run_id": restore_run.get("id"), "run_type": "RESTORE", "intermediate_format": snapshot_row.get("intermediate_format") or "sml", "message": "Project configuration restored and restore run started.", "config_yaml": config_yaml}


async def run_project_now_compat(project_id: str, background_tasks: BackgroundTasks, payload: Optional[Dict[str, Any]] = None):
    _compat_ensure_loaded()
    if bool((payload or {}).get("dry_run", False)):
        preview_payload = dict(payload or {})
        preview_payload["project_id"] = project_id
        preview_payload["dry_run"] = True
        preview_payload["require_sync"] = True
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
    restore_snapshot_id = str(
        (payload or {}).get("restore_snapshot_id")
        or (payload or {}).get("snapshot_id")
        or ""
    ).strip() or None
    config_override = None
    if run_type == "SYNC":
        modular_bundle = project_shared._compat_load_modular_project(project_id)
        base_cfg = (
            (str(modular_bundle.get("config_yaml") or "") if modular_bundle else "")
            or _compat_project_configs.get(project_id)
            or _compat_load_repo_yaml_text()
            or _compat_default_project_yaml(_compat_projects.get(project_id, {}))
        )
        config_override = _compat_apply_manual_mapping_overrides_to_cfg(base_cfg, project_id)
        _compat_project_configs[project_id] = config_override
    run, project_cfg, started = _create_project_run(
        project_id,
        "Manual",
        run_type=run_type,
        project_cfg_override=config_override,
        restore_snapshot_id=restore_snapshot_id,
        sync_mode=(
            _compat_sync_mode_for_restore_snapshot(project_id, restore_snapshot_id, payload)
            if run_type == "RESTORE" and restore_snapshot_id
            else (_compat_normalize_sync_mode((payload or {}).get("sync_mode")) or "copy")
        ),
    )
    user_id = (payload or {}).get("user_id")
    force = bool((payload or {}).get("force", False))
    if user_id is not None and str(user_id).strip():
        run["user_id"] = user_id
    run["force"] = force
    background_tasks.add_task(_perform_project_run, run, project_cfg, started)
    return {"run_id": run["id"], "status": "running", "run_type": run_type, "message": "Sync started in background"}


async def get_run_conflicts_compat(run_id: str):
    """Retrieve sync conflicts for a specific run."""
    try:
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        return repo.get_sync_conflicts(run_id)
    except Exception as exc:
        logger.error("Failed to fetch conflicts for run %s: %s", run_id, exc)
        return []
 
 
def _run_retention_background(project_id: str):
    """Internal helper to run retention policy in a separate thread."""
    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.api.services.retention_service import apply_retention_policy
        with db_manager.get_session() as session:
            apply_retention_policy(session, project_id)
            logger.info("Background retention policy completed for project %s", project_id)
    except Exception as exc:
        logger.error("Background retention policy failed for %s: %s", project_id, exc)
 
 
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


def _compat_collect_manual_mapping_overrides(project_id: str) -> List[Dict[str, str]]:
    overrides: List[Dict[str, str]] = []
    for mapping in _compat_mappings.values():
        if not isinstance(mapping, dict):
            continue
        if str(mapping.get("project_id") or "") != str(project_id):
            continue
        if not bool(mapping.get("is_user_edited")):
            continue
        source_path = str(mapping.get("source_path") or "").strip()
        target_name = _compat_sanitize_target_name_for_project(
            project_id,
            str(mapping.get("target_name") or "").strip(),
        )
        if not source_path or not target_name:
            continue
        overrides.append({
            "source_path": source_path,
            "target_name": target_name,
            "entity_kind": str(mapping.get("entity_kind") or "").strip().lower(),
            "source_name": str(mapping.get("source_name") or "").strip(),
        })
    return overrides


def _compat_project_target_type(project_id: str) -> str:
    cfg = str(_compat_project_configs.get(project_id) or "").strip()
    if not cfg:
        return ""
    parsed = _compat_parse_project_cfg_dict(cfg)
    if not isinstance(parsed, dict):
        return ""
    target = parsed.get("target") if isinstance(parsed.get("target"), dict) else {}
    if not target:
        targets = parsed.get("targets")
        if isinstance(targets, list) and targets and isinstance(targets[0], dict):
            target = targets[0]
    return str((target or {}).get("type") or "").strip().lower()


def _compat_sanitize_target_name_for_project(project_id: str, target_name: str) -> str:
    raw = str(target_name or "").strip()
    if not raw:
        return raw
    if _compat_project_target_type(project_id) != "snowflake":
        return raw
    return _SNOWFLAKE_SANITIZER.sanitize_alias(raw)


def _compat_apply_manual_mapping_overrides_to_cfg(config_yaml: str, project_id: str) -> str:
    parsed = _compat_parse_project_cfg_dict(config_yaml) or {}
    overrides = _compat_collect_manual_mapping_overrides(project_id)
    if not overrides:
        return config_yaml
    parsed["mappings_overrides"] = overrides
    return yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)


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


def _compat_hydrate_missing_metrics_from_manual_mappings(
    latest_model: Dict[str, Any],
    existing_mappings: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    model = dict(latest_model or {})
    metrics = model.get("metrics") if isinstance(model.get("metrics"), list) else []
    metrics = [row for row in metrics if isinstance(row, dict)]
    present_metric_names = {
        str(row.get("unique_name") or row.get("name") or row.get("label") or "").strip()
        for row in metrics
    }
    present_metric_names.discard("")

    hydrated = list(metrics)
    added = 0
    for mapping in existing_mappings.values():
        if not isinstance(mapping, dict):
            continue
        if not bool(mapping.get("is_user_edited")):
            continue
        if str(mapping.get("entity_kind") or "").lower() != "metric":
            continue
        source_path = str(mapping.get("source_path") or "").strip()
        if not source_path.startswith("metrics."):
            continue
        metric_name = source_path[len("metrics."):].strip()
        if not metric_name or metric_name in present_metric_names:
            continue
        hydrated.append({
            "unique_name": metric_name,
            "data_type": mapping.get("source_data_type") or "decimal",
        })
        present_metric_names.add(metric_name)
        added += 1

    if added:
        logger.debug("Hydrated %s manual metric mapping(s) into latest model state", added)
    model["metrics"] = hydrated
    return model


def _compat_scope_model_for_dry_run(
    latest_model: Dict[str, Any],
    selected_model_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    model = dict(latest_model or {})
    selected = [str(item or "").strip() for item in (selected_model_names or []) if str(item or "").strip()]
    if not selected:
        return model

    selected_lookup = {name.lower() for name in selected}
    datasets = model.get("datasets") if isinstance(model.get("datasets"), list) else []
    filtered_datasets = [
        row for row in datasets
        if isinstance(row, dict) and str(row.get("unique_name") or "").strip().lower() in selected_lookup
    ]
    if not filtered_datasets:
        return model

    model["datasets"] = filtered_datasets

    # Keep metrics that can be attributed to the selected dataset scope.
    metrics = model.get("metrics") if isinstance(model.get("metrics"), list) else []
    if metrics:
        dataset_lookup = {
            str(row.get("unique_name") or "").strip().lower(): str(row.get("unique_name") or "").strip()
            for row in filtered_datasets
            if isinstance(row, dict) and str(row.get("unique_name") or "").strip()
        }
        scoped_metrics: List[Dict[str, Any]] = []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            source_tables = _extract_metric_source_tables(metric, dataset_lookup)
            if source_tables:
                scoped_metrics.append(metric)
        model["metrics"] = scoped_metrics
    else:
        model["metrics"] = []

    return model


def _compat_preferred_snapshot_id_from_sync_result(
    sync_result: Dict[str, Any],
    selected_model_names: Optional[List[str]] = None,
) -> str:
    if not isinstance(sync_result, dict):
        return ""

    selected_lookup = {
        str(item or "").strip().lower()
        for item in (selected_model_names or [])
        if str(item or "").strip()
    }

    results = sync_result.get("results") if isinstance(sync_result.get("results"), list) else []
    if selected_lookup and results:
        for row in results:
            if not isinstance(row, dict):
                continue
            model_name = str(row.get("model") or "").strip().lower()
            if model_name not in selected_lookup:
                continue
            summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
            sid = str(summary.get("sml_snapshot_id") or "").strip()
            if sid:
                return sid

    summary = sync_result.get("summary") if isinstance(sync_result.get("summary"), dict) else {}
    sid = str(summary.get("sml_snapshot_id") or "").strip()
    if sid:
        return sid

    for row in results:
        if not isinstance(row, dict):
            continue
        item_summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        sid = str(item_summary.get("sml_snapshot_id") or "").strip()
        if sid:
            return sid

    return ""


def _compat_build_project_entity_mappings(
    project_id: str,
    save_store: bool = True,
    target_connector: Optional[str] = None,
    selected_model_names: Optional[List[str]] = None,
    preferred_snapshot_id: str = "",
) -> Dict[str, Any]:
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        latest_model = _compat_latest_sml_state(project_id, preferred_snapshot_id)
    except TypeError:
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

    existing_mappings = _compat_existing_entity_mappings(project_id)
    latest_model = _compat_hydrate_missing_metrics_from_manual_mappings(latest_model, existing_mappings)
    latest_model = _compat_scope_model_for_dry_run(latest_model, selected_model_names)

    built = build_entity_mappings(
        project_id=project_id,
        model=latest_model,
        existing_mappings=existing_mappings,
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
            manual_target = _compat_sanitize_target_name_for_project(
                project_id,
                str(existing.get("target_name") or "").strip(),
            )
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


def _compat_extract_invalid_identifier(message: str) -> str:
    text = str(message or "")
    if not text:
        return ""
    marker = "invalid identifier '"
    idx = text.lower().find(marker)
    if idx < 0:
        return ""
    start = idx + len(marker)
    end = text.find("'", start)
    if end <= start:
        return ""
    return text[start:end].strip()


def _compat_latest_identifier_diagnostics(project_id: str) -> List[Dict[str, str]]:
    latest_run: Optional[Dict[str, Any]] = None
    for run in _compat_project_runs.get(project_id, []):
        if isinstance(run, dict):
            latest_run = run
            break

    if not latest_run:
        return []

    candidates: List[str] = []
    candidates.extend([str(item) for item in (latest_run.get("logs") or []) if str(item or "").strip()])
    if isinstance(latest_run.get("summary"), dict):
        for err in (latest_run.get("summary", {}).get("errors") or []):
            if isinstance(err, dict):
                candidates.append(str(err.get("message") or ""))
    candidates.append(str(latest_run.get("error") or ""))
    candidates.append(str(latest_run.get("message") or ""))

    diagnostics: List[Dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        actual_identifier = _compat_extract_invalid_identifier(candidate)
        if not actual_identifier:
            continue
        key = actual_identifier.upper()
        if key in seen:
            continue
        seen.add(key)
        parts = actual_identifier.split(".")
        source_hint = (parts[-1] if parts else actual_identifier).replace('"', "").strip().upper()
        diagnostics.append({
            "code": "INVALID_IDENTIFIER_REFERENCE",
            "actual_identifier": actual_identifier,
            "source_hint": source_hint,
            "message": f"Deploy SQL references invalid identifier {actual_identifier}.",
        })

    if diagnostics:
        return diagnostics
    return []


def _compat_apply_identifier_diagnostics_to_mappings(
    mappings: List[Dict[str, Any]],
    diagnostics: List[Dict[str, str]],
) -> None:
    if not diagnostics:
        return

    def _metric_expression_references_identifier(expression: str, identifier: str) -> bool:
        expr = str(expression or "")
        ident = str(identifier or "").strip()
        if not expr or not ident or "." not in ident:
            return False

        table_name, column_name = ident.rsplit(".", 1)
        table_name = table_name.strip().strip('"').strip("'").strip()
        column_name = column_name.strip().strip('"').strip("'").strip()
        if not table_name or not column_name:
            return False

        patterns = [
            rf"\b{re.escape(table_name)}\s*\.\s*{re.escape(column_name)}\b",
            rf"'{re.escape(table_name)}'\s*\[\s*{re.escape(column_name)}\s*\]",
            rf"\b{re.escape(table_name)}\s*\[\s*{re.escape(column_name)}\s*\]",
            rf"\"{re.escape(table_name)}\"\s*\.\s*\"{re.escape(column_name)}\"",
        ]
        return any(re.search(pattern, expr, flags=re.IGNORECASE) for pattern in patterns)

    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        if str(mapping.get("entity_kind") or "").lower() != "metric":
            continue

        source_name = str(mapping.get("source_name") or "").upper()
        source_path = str(mapping.get("source_path") or "").upper()
        source_expression = str(mapping.get("source_expression") or "")

        for diag in diagnostics:
            actual_identifier = str(diag.get("actual_identifier") or "").strip()
            hint = str(diag.get("source_hint") or "").upper()
            if not hint:
                continue
            expression_match = _metric_expression_references_identifier(source_expression, actual_identifier)
            path_or_name_match = bool(hint in source_name or hint in source_path)
            if expression_match or (not source_expression.strip() and path_or_name_match):
                mapping["collision_detected"] = True
                mapping["validation_status"] = "invalid"
                mapping["validation_code"] = str(diag.get("code") or "INVALID_IDENTIFIER_REFERENCE")
                mapping["validation_message"] = str(diag.get("message") or "Invalid identifier reference during deploy.")
                mapping["validation_debug"] = {
                    "actual_identifier": str(diag.get("actual_identifier") or ""),
                    "source_hint": hint,
                    "stage": "deploy_sql_compile",
                }
                break


def _compat_is_blocking_mapping(mapping: Dict[str, Any]) -> bool:
    if not isinstance(mapping, dict):
        return False

    validation_code = str(mapping.get("validation_code") or "").strip().upper()
    target_name = str(mapping.get("target_name") or "").strip()
    validation_message = str(mapping.get("validation_message") or "").strip().lower()
    status = str(mapping.get("status") or "").strip().lower()

    # Deterministic NAME_COLLISION rows are auto-resolved by suffixing the
    # identifier; they should not block deploy when a target exists.
    if (
        validation_code == "NAME_COLLISION"
        and target_name
        and ("resolved" in validation_message or status in {"auto", "manual"})
    ):
        return False

    if status in {"collision", "unmapped"}:
        return True

    validation_status = str(mapping.get("validation_status") or "").strip().lower()
    if validation_status in {"invalid", "collision"}:
        return True

    if validation_code and validation_code != "OK":
        return True

    if not target_name:
        return True

    if bool(mapping.get("collision_detected")):
        return True

    return False


def _compat_collect_dry_run_blockers(preview_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    blockers: List[Dict[str, Any]] = []
    mappings = preview_result.get("entity_mappings") if isinstance(preview_result, dict) else []
    if not isinstance(mappings, list):
        return blockers

    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        if not _compat_is_blocking_mapping(mapping):
            continue
        blockers.append({
            "id": str(mapping.get("id") or "").strip(),
            "source_path": str(mapping.get("source_path") or "").strip(),
            "source_name": str(mapping.get("source_name") or "").strip(),
            "target_name": str(mapping.get("target_name") or "").strip(),
            "status": str(mapping.get("status") or "").strip().lower(),
            "validation_status": str(mapping.get("validation_status") or "").strip().lower(),
            "validation_code": str(mapping.get("validation_code") or "").strip().upper(),
            "validation_message": str(mapping.get("validation_message") or "").strip(),
        })
    return blockers


def _compat_is_field_entity(mapping: Dict[str, Any]) -> bool:
    kind = str(mapping.get("entity_kind") or "").strip().lower()
    return kind in {"column", "metric", "measure"} or kind not in {"", "table", "dataset", "model"}


def _compat_hash_suffix(value: str) -> str:
    from semabridge.api.services.project_mapping_engine import deterministic_hash_suffix
    return deterministic_hash_suffix(str(value or ""), size=4)


def _compat_collision_fallback_name(source_name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(source_name or "").upper()).strip("_") or "UNNAMED"


def _compat_serialize_auto_map_entity_mappings(
    mappings: List[Dict[str, Any]],
    *,
    target_connector: str = "",
) -> List[Dict[str, Any]]:
    connector = str(target_connector or "").strip().lower()
    entity_mappings: List[Dict[str, Any]] = []

    for index, row in enumerate(mappings):
        if not isinstance(row, dict) or not _compat_is_field_entity(row):
            continue

        source_name = str(row.get("source_name") or row.get("name") or f"field_{index + 1}").strip()
        source_path = str(row.get("source_path") or "").strip()
        data_type = str(row.get("source_data_type") or row.get("data_type") or row.get("target_data_type") or "unknown").strip() or "unknown"
        target_name = str(row.get("target_name") or "").strip() or sanitize_identifier(source_name)
        validation_status = str(row.get("validation_status") or "valid").strip().lower() or "valid"
        validation_code = str(row.get("validation_code") or "OK").strip().upper() or "OK"
        validation_message = str(row.get("validation_message") or "").strip()
        suggested_target_name = str(row.get("suggested_target_name") or target_name).strip()
        collision_detected = bool(row.get("collision_detected"))
        if validation_code in {"RESERVED_KEYWORD", "COLLISION", "NAME_COLLISION"} or validation_status == "collision":
            collision_detected = True
        if validation_code == "OK" and validation_status == "valid":
            validation_message = ""

        if connector == "snowflake" and target_name.upper() in AUTO_MAP_SNOWFLAKE_RESERVED:
            suggested_target_name = f"COL_{target_name.upper()}"
            target_name = suggested_target_name
            collision_detected = True
            validation_status = "invalid"
            validation_code = "RESERVED_KEYWORD"
            validation_message = f"'{source_name}' is a Snowflake reserved keyword."

        entity_mappings.append({
            "id": str(row.get("id") or f"mapping-{index + 1}"),
            "source_name": source_name,
            "target_name": target_name,
            "source_data_type": data_type,
            "target_data_type": str(row.get("target_data_type") or data_type).strip() or "unknown",
            "entity_kind": str(row.get("entity_kind") or "column").strip().lower() or "column",
            "source_path": source_path,
            "parent_source_path": str(row.get("parent_source_path") or ""),
            "validation_status": validation_status,
            "validation_code": validation_code,
            "validation_message": validation_message,
            "collision_detected": collision_detected,
            "suggested_target_name": suggested_target_name,
            "measure_source_tables": list(row.get("measure_source_tables") or []),
            "source_expression": str(row.get("source_expression") or ""),
            "status": str(row.get("status") or "auto").strip().lower() or "auto",
        })

    seen: Dict[str, Dict[str, Any]] = {}
    for mapping in entity_mappings:
        target_key = str(mapping.get("target_name") or "").strip().upper()
        if not target_key:
            continue
        if target_key in seen:
            first = seen[target_key]
            first["collision_detected"] = True
            first["validation_status"] = "invalid"
            first["validation_code"] = "COLLISION"
            first["validation_message"] = "Duplicate target name detected."
            first["suggested_target_name"] = str(first.get("suggested_target_name") or first.get("target_name") or "").strip() or _compat_collision_fallback_name(str(first.get("source_name") or ""))
            entity_seed = str(mapping.get("parent_source_path") or mapping.get("source_table") or "").strip()
            field_seed = str(mapping.get("source_name") or "").strip()
            suffix = _compat_hash_suffix(f"{entity_seed}::{field_seed}")
            base_target = sanitize_identifier(str(mapping.get("target_name") or "").strip())
            mapping["target_name"] = f"{base_target}_{suffix}".upper()
            mapping["collision_detected"] = True
            mapping["validation_status"] = "invalid"
            mapping["validation_code"] = "COLLISION"
            mapping["validation_message"] = "Duplicate target name resolved with hash suffix."
            mapping["suggested_target_name"] = mapping["target_name"]
        else:
            seen[target_key] = mapping

    for mapping in entity_mappings:
        if mapping.get("collision_detected") and not str(mapping.get("suggested_target_name") or "").strip():
            mapping["suggested_target_name"] = _compat_collision_fallback_name(str(mapping.get("source_name") or ""))
        if not str(mapping.get("validation_status") or "").strip():
            mapping["validation_status"] = "invalid" if mapping.get("collision_detected") else "valid"
        if not str(mapping.get("validation_code") or "").strip():
            mapping["validation_code"] = "COLLISION" if mapping.get("collision_detected") else "OK"

    return entity_mappings


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
    user_id = (payload or {}).get("user_id")
    selected_model_names = (payload or {}).get("selected_model_names") if isinstance((payload or {}).get("selected_model_names"), list) else []
    target_connectors = (payload or {}).get("target_connectors") if isinstance((payload or {}).get("target_connectors"), list) else []
    explicit_target = str((payload or {}).get("target_connector") or "").strip()
    target_connector = explicit_target or (str(target_connectors[0]).strip() if target_connectors else "")
    dry_run = bool((payload or {}).get("dry_run", False))
    config_yaml = str((payload or {}).get("config_yaml") or "").strip()
    if project_id and not config_yaml:
        config_yaml = str(_compat_project_configs.get(project_id) or "").strip()

    if not project_id and not selected_model_names and _compat_projects:
        project_id = next(iter(_compat_projects.keys()))
    if not project_id and not selected_model_names:
        raise HTTPException(status_code=400, detail="project_id or selected_model_names is required")

    reset_manual = bool((payload or {}).get("reset_manual", False))
    # Only use synthetic preview mode when there is no concrete project context.
    # For project-scoped dry runs, use project-backed mappings so measure rows
    # from the latest model state are preserved.
    preview_mode = (not project_id) and dry_run and (bool(config_yaml) or bool(selected_model_names))
    if project_id and not preview_mode:
        from semabridge.api.services.core_domain_service import sync_models
        sync_result: Dict[str, Any] = {}
        preferred_snapshot_id = ""
        try:
            sync_result = await sync_models({
                "project_id": project_id,
                "content": config_yaml or None,
                "dry_run": True,
                "user_id": user_id,
            })
            preferred_snapshot_id = _compat_preferred_snapshot_id_from_sync_result(sync_result, selected_model_names)
        except Exception as exc:
            has_existing_project_mappings = any(
                str(mapping.get("project_id") or "") == project_id
                for mapping in _compat_mappings.values()
                if isinstance(mapping, dict)
            )
            if not has_existing_project_mappings:
                logger.warning("Auto-map pre-sync failed for project %s: %s", project_id, exc)
                raise HTTPException(
                    status_code=502,
                    detail=f"Dry-run sync failed before auto-map for project '{project_id}': {exc}",
                )
            logger.warning(
                "Auto-map pre-sync failed for project %s; falling back to existing mapping state: %s",
                project_id,
                exc,
            )

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
            selected_model_names=selected_model_names if dry_run else None,
            preferred_snapshot_id=preferred_snapshot_id,
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

    diagnostics = _compat_latest_identifier_diagnostics(project_id) if project_id else []
    _compat_apply_identifier_diagnostics_to_mappings(data.get("mappings", []), diagnostics)

    grouped_mappings = _compat_format_mapping_groups(data)
    entity_mappings = _compat_serialize_auto_map_entity_mappings(
        data.get("mappings", []),
        target_connector=target_connector,
    )
    collision_names = [
        str(mapping.get("source_name") or mapping.get("target_name") or "").strip()
        for mapping in entity_mappings
        if mapping.get("collision_detected")
    ]
    collision_names = [name for name in collision_names if name]
    return {
        "project_id": data.get("project_id") or project_id,
        "source_fields": data.get("source_fields", []),
        "target_fields": data.get("target_fields", []),
        "mappings": grouped_mappings,
        "entity_mappings": entity_mappings,
        "collisions": collision_names,
        "diagnostics": diagnostics,
        "status": "ok",
    }


async def update_mapping_compat(mapping_id: str, payload: dict):
    _compat_ensure_loaded()
    incoming = payload or {}
    existing = _compat_mappings.get(mapping_id, {"id": mapping_id, "project_id": incoming.get("project_id")})

    # Backfill canonical identity fields for clients that only send target_name.
    if not str(existing.get("source_path") or "").strip():
        project_id = str(incoming.get("project_id") or existing.get("project_id") or "").strip()
        if project_id:
            try:
                built = _compat_build_project_entity_mappings(project_id, save_store=False)
                for candidate in built.get("mappings", []):
                    if str(candidate.get("id") or "") == mapping_id:
                        existing = {**candidate, **existing}
                        break
            except Exception as exc:
                logger.debug("Mapping identity backfill skipped for %s: %s", mapping_id, exc)

    existing.update(incoming)
    existing["id"] = mapping_id
    if "target_name" in incoming:
        project_id = str(existing.get("project_id") or incoming.get("project_id") or "").strip()
        existing["target_name"] = _compat_sanitize_target_name_for_project(
            project_id,
            str(incoming.get("target_name") or "").strip(),
        )
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


# --- Advanced Version Control Extensions ---

def _compat_log_audit(project_id: str, action: str, user: str, details: str) -> None:
    """Helper to record system events."""
    _compat_ensure_loaded()
    # In-memory audit log for compatibility store
    if "_audit_logs" not in _compat_snapshot_groups: # Reusing a persistent slot or adding new one
         _compat_snapshot_groups["_audit_logs"] = []
    
    _compat_snapshot_groups["_audit_logs"].insert(0, {
        "timestamp": _compat_now_iso(),
        "project_id": project_id,
        "action": action,
        "user": user,
        "details": details
    })
    # Keep last 500 logs
    _compat_snapshot_groups["_audit_logs"] = _compat_snapshot_groups["_audit_logs"][:500]
    _compat_save_store()


async def get_audit_logs_compat(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _compat_ensure_loaded()
    logs = _compat_snapshot_groups.get("_audit_logs", [])
    if project_id:
        return [log for log in logs if log.get("project_id") == project_id]
    return logs


async def tag_snapshot_compat(project_id: str, snapshot_id: str, tag: str, comment: str = "") -> Dict[str, Any]:
    _compat_ensure_loaded()
    found = False
    
    for snap in _compat_project_snapshots.get(project_id, []):
        if snap.get("snapshot_id") == snapshot_id:
            snap["tag"] = tag
            snap["comment"] = comment
            found = True
            break
            
    # Propagate to runs for UI convenience
    for run in _compat_project_runs.get(project_id, []):
        if run.get("after_src_snapshot_id") == snapshot_id or run.get("before_src_snapshot_id") == snapshot_id:
            run.setdefault("tags", [])
            if tag and tag not in run["tags"]:
                run["tags"].append(tag)
            if comment:
                run["last_comment"] = comment
                
    if found:
        _compat_log_audit(project_id, "TAG_SNAPSHOT", "system", f"Tagged {snapshot_id} as '{tag}'")
        _compat_save_store()
        return {"status": "success", "snapshot_id": snapshot_id, "tag": tag}
    return {"status": "error", "message": "Snapshot not found"}


async def preview_restore_compat(project_id: str, snapshot_id: str) -> Dict[str, Any]:
    """Dry-run diff: Compare CURRENT state with TARGET snapshot state."""
    _compat_ensure_loaded()
    
    # 1. Get current state
    current_state = _compat_latest_sml_state(project_id)
    
    # 2. Get target state
    target_snap = None
    for snap in _compat_project_snapshots.get(project_id, []):
        if snap.get("snapshot_id") == snapshot_id:
            target_snap = snap
            break
            
    if not target_snap:
        raise HTTPException(status_code=404, detail="Target snapshot not found")
        
    target_state = target_snap.get("state") or {}
    if isinstance(target_state, str):
        try: target_state = json.loads(target_state)
        except Exception: target_state = {}
        
    # 3. Diff them
    diff_results = _diff_models(current_state, target_state)
    
    _compat_log_audit(project_id, "PREVIEW_RESTORE", "system", f"Previewed restore to {snapshot_id}")
    
    return {
        "project_id": project_id,
        "target_snapshot_id": snapshot_id,
        "models": diff_results,
        "impact_summary": {
            "added": len([m for m in diff_results if m["status"] == "ADDED"]),
            "removed": len([m for m in diff_results if m["status"] == "REMOVED"]),
            "modified": len([m for m in diff_results if m["status"] == "MODIFIED"]),
            "unchanged": len([m for m in diff_results if m["status"] == "UNCHANGED"]),
        }
    }


async def get_project_lineage_compat(project_id: str) -> Dict[str, Any]:
    """Return nodes and edges for the version history graph."""
    _compat_ensure_loaded()
    nodes = []
    edges = []
    
    # 1. Snapshots as nodes
    snapshots = _compat_project_snapshots.get(project_id, [])
    for snap in snapshots:
        nodes.append({
            "id": snap["snapshot_id"],
            "type": "snapshot",
            "label": snap.get("tag") or f"Snap {snap['snapshot_id'][:8]}",
            "metadata": {
                "role": snap.get("role"),
                "created_at": snap.get("created_at"),
                "connector": snap.get("connector"),
                "is_pinned": snap.get("is_pinned", False)
            }
        })
        
    # 2. Runs as nodes and link to snapshots
    runs = _compat_project_runs.get(project_id, [])
    for run in runs:
        run_node_id = f"run-{run['run_id']}"
        nodes.append({
            "id": run_node_id,
            "type": "run",
            "label": f"Run {run['run_id'][:8]}",
            "metadata": {
                "status": run.get("status"),
                "type": run.get("run_type"),
                "started_at": run.get("started_at")
            }
        })
        
        # Edges
        if run.get("before_src_snapshot_id"):
            edges.append({"source": run["before_src_snapshot_id"], "target": run_node_id, "label": "input"})
        if run.get("after_src_snapshot_id"):
            edges.append({"source": run_node_id, "target": run["after_src_snapshot_id"], "label": "output"})
        
        # Link to target snapshots
        for tsid in (run.get("after_tgt_snapshots") or []):
            edges.append({"source": run_node_id, "target": tsid, "label": "deploy"})

    return {"nodes": nodes, "edges": edges}


async def toggle_snapshot_pin_compat(project_id: str, snapshot_id: str, is_pinned: bool) -> Dict[str, Any]:
    _compat_ensure_loaded()
    found = False
    for snap in _compat_project_snapshots.get(project_id, []):
        if snap.get("snapshot_id") == snapshot_id:
            snap["is_pinned"] = is_pinned
            found = True
            break
            
    if found:
        _compat_save_store()
        status = "pinned" if is_pinned else "unpinned"
        _compat_log_audit(project_id, "TOGGLE_PIN", "system", f"Snapshot {snapshot_id} {status}")
        return {"status": "success", "is_pinned": is_pinned}
    return {"status": "error", "message": "Snapshot not found"}


async def get_model_history_compat(project_id: str, model_name: str) -> List[Dict[str, Any]]:
    """Return a timeline of changes for a specific model across all snapshots."""
    _compat_ensure_loaded()
    history = []
    
    # Sort runs chronologically
    runs = sorted(_compat_project_runs.get(project_id, []), key=lambda x: x.get("started_at", ""))
    
    for run in runs:
        # Check if this model was involved in this run's snapshots
        snap_id = run.get("after_tgt_snapshots", [None])[0] or run.get("before_tgt_snapshots", [None])[0]
        if not snap_id: continue
        
        # Find snapshot
        snap = next((s for s in _compat_project_snapshots.get(project_id, []) if s["snapshot_id"] == snap_id), None)
        if not snap: continue
        
        # Check model state in this snapshot
        state = snap.get("state") or {}
        if isinstance(state, str):
            try: state = json.loads(state)
            except Exception: state = {}
            
        models = state.get("models", [])
        model = next((m for m in models if m.get("name") == model_name), None)
        
        if model:
            history.append({
                "run_id": run["run_id"],
                "snapshot_id": snap_id,
                "timestamp": run["started_at"],
                "status": "active",
                "model_data": model
            })
            
    return history


async def get_project_stats_compat(project_id: str) -> Dict[str, Any]:
    _compat_ensure_loaded()
    runs = _compat_project_runs.get(project_id, [])
    snaps = _compat_project_snapshots.get(project_id, [])
    
    total_size = sum(len(str(s.get("state", ""))) for s in snaps)
    avg_models = sum(len((s.get("state") or {}).get("models", [])) if isinstance(s.get("state"), dict) else 0 for s in snaps) / (len(snaps) or 1)
    
    return {
        "project_id": project_id,
        "total_runs": len(runs),
        "total_snapshots": len(snaps),
        "pinned_count": len([s for s in snaps if s.get("is_pinned")]),
        "storage_estimate": f"{total_size / 1024 / 1024:.2f} MB",
        "avg_models_per_snapshot": int(avg_models),
        "health_score": int(95 if len([r for r in runs if r.get("status") == "success"]) / (len(runs) or 1) > 0.8 else 70)
    }


async def get_snapshot_content_compat(project_id: str, snapshot_id: str) -> Dict[str, Any]:
    """Retrieve raw content/state for a specific snapshot."""
    _compat_ensure_loaded()
    snaps = _compat_project_snapshots.get(project_id, [])
    snap = next((s for s in snaps if s.get("snapshot_id") == snapshot_id), None)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    state = snap.get("sml_blob") or snap.get("state") or {}
    if isinstance(state, str):
        try: state = json.loads(state)
        except Exception: state = {}
        
    return {
        "snapshot_id": snapshot_id,
        "project_id": project_id,
        "captured_at": snap.get("timestamp") or snap.get("captured_at") or snap.get("created_at"),
        "role": snap.get("role"),
        "content": state
    }


async def manual_deploy_compat(project_id: str, snapshot_id: str, background_tasks: BackgroundTasks, comment: str = "") -> Dict[str, Any]:
    """Manually deploy a specific snapshot to target connectors."""
    _compat_ensure_loaded()
    # Logic is similar to restore but with explicit manual_deploy type
    payload = {
        "restore_snapshot_id": snapshot_id,
        "comment": comment or f"Manual deploy of {snapshot_id[:8]}",
        "run_type": "manual_deploy"
    }
    return await run_project_now_compat(project_id, background_tasks, payload)


async def get_snapshot_report_compat(project_id: str, snapshot_id: str) -> Dict[str, Any]:
    """Generate a structured conversion/mapping report for a snapshot."""
    _compat_ensure_loaded()
    snaps = _compat_project_snapshots.get(project_id, [])
    snap = next((s for s in snaps if s.get("snapshot_id") == snapshot_id), None)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    state = snap.get("sml_blob") or snap.get("state") or {}
    if isinstance(state, str):
        try: state = json.loads(state)
        except Exception: state = {}
    
    # Extract conversion summary from state
    datasets = state.get("datasets") or state.get("models") or []
    total_models = len(datasets)
    total_columns = sum(len(d.get("columns", [])) for d in datasets if isinstance(d, dict))
    
    # Extract warnings/collisions if present in the snapshot metadata or run logs
    warnings = []
    
    return {
        "snapshot_id": snapshot_id,
        "role": snap.get("role"),
        "timestamp": snap.get("timestamp") or snap.get("created_at"),
        "summary": {
            "total_models": total_models,
            "total_columns": total_columns,
            "format": snap.get("intermediate_format") or snap.get("format") or "SML",
            "origin": snap.get("snapshot_origin") or snap.get("trigger") or "AUTO",
        },
        "models": [
            {
                "name": d.get("unique_name") or d.get("name"),
                "columns": len(d.get("columns", [])),
                "status": "MAPPED"
            } for d in datasets if isinstance(d, dict)
        ],
        "warnings": warnings 
    }
