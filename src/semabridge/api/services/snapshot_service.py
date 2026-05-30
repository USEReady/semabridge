"""Snapshot service — snapshot listing, capture, comparison, deletion, tagging, and pinning.

Provides all public snapshot management functions plus shared snapshot
helper utilities used by run_service and version_service.
"""
import json
import uuid
from typing import Any, Dict, List, Optional

import yaml

from fastapi import BackgroundTasks, Query

from semabridge.api.services.project_shared import (
    _compat_default_project_yaml,
    _compat_ensure_loaded,
    _compat_load_repo_yaml_text,
    _compat_now_iso,
    _compat_project_configs,
    _compat_project_runs,
    _compat_project_snapshots,
    _compat_projects,
    _compat_save_store,
    _compat_snapshot_groups,
)
from semabridge.domain.exceptions import NotFoundError, ValidationError
from semabridge.core.run_helpers import normalize_sync_mode

# Backward-compatible alias used within this module
_compat_normalize_sync_mode = normalize_sync_mode


# ---------------------------------------------------------------------------
# Private helpers (shared with run_service)
# ---------------------------------------------------------------------------

def _compat_parse_project_cfg_dict(project_cfg: str) -> Dict[str, Any]:
    try:
        parsed = yaml.safe_load(project_cfg) or {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _compat_load_snapshot_state_from_orm(snapshot_id: str) -> Any:
    """Load sml_blob/state for a single snapshot from the ORM (on-demand).

    State blobs are stripped from the in-memory compat store to keep the
    store file small and cold-start parsing fast. This function fetches the
    blob from the database only when a detail/compare view actually needs it.
    Returns the raw value (str or dict) or an empty dict on failure.
    """
    try:
        from semabridge.repository.orm.models import SnapshotRow
        from sqlalchemy import select
        from semabridge.repository.orm.session_factory import db_manager
        import logging
        logger = logging.getLogger(__name__)

        with db_manager.get_session() as session:
            row = session.execute(
                select(SnapshotRow.sml_blob).where(
                    SnapshotRow.snapshot_id == snapshot_id
                )
            ).scalar_one_or_none()
        return row or {}
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("ORM snapshot state load skipped for %s: %s", snapshot_id, exc)
        return {}


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
    from semabridge.repository.orm.session_factory import db_manager
    import logging
    logger = logging.getLogger(__name__)

    sid = str(preferred_snapshot_id or "").strip()
    if sid:
        try:
            snap = db_manager.get_snapshot(sid)
            if snap and isinstance(getattr(snap, "sml_blob", None), dict):
                return snap.sml_blob
        except Exception as exc:
            logger.debug("ORM snapshot state load skipped for %s: %s", sid, exc)

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


def _compat_strip_runtime_metadata(value: Any) -> Any:
    """Remove deployment-runtime metadata from a snapshot payload copy."""
    runtime_keys = {"initiated_by", "connector_id", "trigger", "trigger_by"}

    def _normalize_key(key: Any) -> str:
        return str(key or "").strip().lower().replace(" ", "_").replace("-", "_")

    if isinstance(value, dict):
        return {
            key: _compat_strip_runtime_metadata(child)
            for key, child in value.items()
            if _normalize_key(key) not in runtime_keys
        }
    if isinstance(value, list):
        return [_compat_strip_runtime_metadata(item) for item in value]
    return value


def _compat_state_in_selected_format(state_sml: Dict[str, Any], selected_format: str) -> Dict[str, Any]:
    sanitized_state = _compat_strip_runtime_metadata(state_sml)
    if not isinstance(sanitized_state, dict) or not sanitized_state or selected_format != "osi":
        return sanitized_state if isinstance(sanitized_state, dict) else {}
    try:
        from semabridge.formats.sml.models import SMLModel
        from semabridge.converter.sml_to_osi import SMLToOSIConverter

        return SMLToOSIConverter().to_osi(SMLModel.model_validate(sanitized_state)).model_dump(mode="json")
    except Exception:
        return sanitized_state


def _compat_version_metadata() -> Dict[str, Any]:
    return {"semabridge_version": "unknown", "connector_versions": {}, "rule_pack_version": "unknown"}


def _compat_create_snapshot_group(
    project_id: str, created_by: str, label: str, origin: str, run_id: Optional[str] = None
) -> str:
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
    state_blob = _compat_state_in_selected_format(
        _compat_latest_sml_state(project_id, preferred_snapshot_id),
        selected_format,
    )
    run_id = str(run.get("run_id") or run.get("id") or "")
    origin = "RUN_BEFORE" if stage == "before" else "RUN_AFTER"
    timing = "before" if stage == "before" else "after"
    group_id = _compat_create_snapshot_group(
        project_id, "system", f"{origin.lower()}-{run_id[:8]}", origin, run_id
    )

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
                for i, m in enumerate(raw)
                if isinstance(m, dict)
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


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

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
    import logging
    logger = logging.getLogger(__name__)

    _compat_ensure_loaded()
    cached_snaps = _compat_project_snapshots.get(project_id, [])
    if not cached_snaps:
        try:
            from semabridge.repository.orm.models import SnapshotRow
            from sqlalchemy import select
            from semabridge.repository.orm.session_factory import db_manager

            with db_manager.get_session() as session:
                stmt = (
                    select(SnapshotRow)
                    .where(SnapshotRow.project_id == project_id)
                    .where(SnapshotRow.deleted_at == None)
                    .order_by(SnapshotRow.timestamp.desc())
                    .limit(limit)
                )
                db_rows = session.execute(stmt).scalars().all()

                cached_snaps = []
                for row in db_rows:
                    state_val = {}
                    if row.sml_blob:
                        try:
                            state_val = json.loads(row.sml_blob) if isinstance(row.sml_blob, str) else row.sml_blob
                        except Exception as exc:
                            logger.debug("Could not parse sml_blob for snapshot row: %s", exc)

                    # Default values
                    snap_role = "source"
                    system_role = "SOURCE"
                    snap_stage = "manual"
                    connector = "fabric"
                    snapshot_origin = "MANUAL"
                    snapshot_group_id = None
                    project_cfg = ""

                    if isinstance(state_val, dict):
                        snap_role = state_val.get("role", snap_role)
                        system_role = state_val.get("system_role", system_role)
                        snap_stage = state_val.get("stage", snap_stage)
                        connector = state_val.get("connector") or state_val.get("source_type") or connector
                        snapshot_origin = state_val.get("snapshot_origin", snapshot_origin)
                        snapshot_group_id = state_val.get("snapshot_group_id", snapshot_group_id)
                        project_cfg = state_val.get("project_config_yaml", project_cfg)

                    snap_data = {
                        "snapshot_id": row.snapshot_id,
                        "project_id": row.project_id,
                        "run_id": row.run_id,
                        "stage": snap_stage,
                        "role": snap_role,
                        "timing": None,
                        "target_index": None,
                        "target_id": None,
                        "connector": connector,
                        "intermediate_format": "sml",
                        "state": state_val,
                        "snapshot_origin": snapshot_origin,
                        "snapshot_group_id": snapshot_group_id,
                        "system_role": system_role,
                        "project_config_yaml": project_cfg,
                        "created_at": row.timestamp.isoformat() if row.timestamp else _compat_now_iso(),
                        "is_pinned": False,
                        "tag": row.version_tag or "",
                        "comment": "",
                    }
                    cached_snaps.append(snap_data)

                if cached_snaps:
                    _compat_project_snapshots[project_id] = cached_snaps
        except Exception as exc:
            logger.error("Failed to retrieve snapshots from ORM: %s", exc)

    rows: List[Dict[str, Any]] = []
    for row in cached_snaps:
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
        raise NotFoundError("Project not found")

    # Older compat-store files may have a project row without initialized
    # snapshot/group collections. Seed them defensively before inserts.
    _compat_project_snapshots.setdefault(project_id, [])
    _compat_snapshot_groups.setdefault(project_id, [])

    project_cfg = (
        _compat_project_configs.get(project_id)
        or _compat_load_repo_yaml_text()
        or _compat_default_project_yaml(_compat_projects[project_id])
    )
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    include_source = bool(scope.get("source", True))
    include_targets = scope.get("targets", "all")
    if not include_source and not include_targets:
        raise ValidationError("At least one scope must be selected")

    selected_format = (
        str(payload.get("format") or "").strip().lower()
        or _compat_selected_intermediate_format(project_cfg)
    )
    selected_format = "osi" if selected_format == "osi" else "sml"
    connector_descriptors = _compat_connector_descriptors(project_cfg)
    state_blob = _compat_state_in_selected_format(_compat_latest_sml_state(project_id), selected_format)
    group_id = _compat_create_snapshot_group(
        project_id,
        "user",
        str(payload.get("label") or "").strip() or f"manual-{_compat_now_iso()[:19]}",
        "MANUAL",
        None,
    )

    created_snapshot_ids: List[str] = []
    if include_source:
        sid = f"psnap-{uuid.uuid4().hex}"
        created_snapshot_ids.append(sid)
        _compat_project_snapshots[project_id].insert(0, {
            "snapshot_id": sid,
            "project_id": project_id,
            "run_id": None,
            "stage": "manual",
            "role": "source",
            "timing": None,
            "target_index": None,
            "target_id": None,
            "connector": connector_descriptors["source"].get("connector_type"),
            "intermediate_format": selected_format,
            "state": state_blob,
            "snapshot_origin": "MANUAL",
            "snapshot_group_id": group_id,
            "system_role": "SOURCE",
            "project_config_yaml": project_cfg,
            "created_at": _compat_now_iso(),
        })
    if include_targets:
        for idx, target_descriptor in enumerate(connector_descriptors.get("targets") or []):
            sid = f"psnap-{uuid.uuid4().hex}"
            created_snapshot_ids.append(sid)
            _compat_project_snapshots[project_id].insert(0, {
                "snapshot_id": sid,
                "project_id": project_id,
                "run_id": None,
                "stage": "manual",
                "role": "target",
                "timing": None,
                "target_index": idx,
                "target_id": target_descriptor.get("target_id") or f"target-{idx + 1}",
                "connector": target_descriptor.get("connector_type") or "target",
                "intermediate_format": selected_format,
                "state": state_blob,
                "snapshot_origin": "MANUAL",
                "snapshot_group_id": group_id,
                "system_role": "TARGET",
                "project_config_yaml": project_cfg,
                "created_at": _compat_now_iso(),
            })
    _compat_save_store()
    return {
        "project_id": project_id,
        "snapshot_group_id": group_id,
        "snapshot_ids": created_snapshot_ids,
        "count": len(created_snapshot_ids),
        "intermediate_format": selected_format,
    }


async def compare_project_snapshots_compat(
    project_id: str,
    from_snapshot_id: str,
    to_snapshot_id: str,
    max_changes: int = Query(default=200, ge=1, le=1000),
    include_states: bool = Query(default=False),
):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise NotFoundError("Project not found")
    rows = [row for row in _compat_project_snapshots.get(project_id, []) if isinstance(row, dict)]
    from_row = next((r for r in rows if str(r.get("snapshot_id") or "") == str(from_snapshot_id)), None)
    to_row = next((r for r in rows if str(r.get("snapshot_id") or "") == str(to_snapshot_id)), None)
    if not from_row:
        raise NotFoundError("from_snapshot_id not found")
    if not to_row:
        raise NotFoundError("to_snapshot_id not found")
    left_state = from_row.get("state")
    if not left_state:
        left_state = _compat_load_snapshot_state_from_orm(from_snapshot_id)
    if isinstance(left_state, str):
        try:
            left_state = json.loads(left_state)
        except Exception:
            left_state = {}

    right_state = to_row.get("state")
    if not right_state:
        right_state = _compat_load_snapshot_state_from_orm(to_snapshot_id)
    if isinstance(right_state, str):
        try:
            right_state = json.loads(right_state)
        except Exception:
            right_state = {}

    if not isinstance(left_state, dict):
        left_state = {}
    if not isinstance(right_state, dict):
        right_state = {}
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


async def delete_project_snapshots_compat(project_id: str, snapshot_ids: List[str]):
    """Soft-delete snapshots after ensuring they are not referenced by any project runs."""
    _compat_ensure_loaded()
    from semabridge.repository.orm.models import SnapshotRow, Run
    from sqlalchemy import select, update, or_
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
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
        from semabridge.api.services.version_service import _compat_log_audit
        _compat_log_audit(project_id, "TAG_SNAPSHOT", "system", f"Tagged {snapshot_id} as '{tag}'")
        _compat_save_store()
        return {"status": "success", "snapshot_id": snapshot_id, "tag": tag}
    return {"status": "error", "message": "Snapshot not found"}


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
        from semabridge.api.services.version_service import _compat_log_audit
        _compat_log_audit(project_id, "TOGGLE_PIN", "system", f"Snapshot {snapshot_id} {status}")
        return {"status": "success", "is_pinned": is_pinned}
    return {"status": "error", "message": "Snapshot not found"}


async def get_snapshot_content_compat(project_id: str, snapshot_id: str) -> Dict[str, Any]:
    """Retrieve raw content/state for a specific snapshot."""
    _compat_ensure_loaded()
    snaps = _compat_project_snapshots.get(project_id, [])
    snap = next((s for s in snaps if s.get("snapshot_id") == snapshot_id), None)
    if not snap:
        raise NotFoundError("Snapshot not found")

    # State blobs are stripped from the in-memory list to keep the compat store
    # small. Load from ORM on demand when the blob is absent.
    state = snap.get("sml_blob") or snap.get("state") or {}
    if not state:
        state = _compat_load_snapshot_state_from_orm(snapshot_id)
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except Exception:
            state = {}

    return {
        "snapshot_id": snapshot_id,
        "project_id": project_id,
        "captured_at": snap.get("timestamp") or snap.get("captured_at") or snap.get("created_at"),
        "role": snap.get("role"),
        "content": state,
    }


async def get_snapshot_report_compat(project_id: str, snapshot_id: str) -> Dict[str, Any]:
    """Generate a structured conversion/mapping report for a snapshot."""
    _compat_ensure_loaded()
    snaps = _compat_project_snapshots.get(project_id, [])
    snap = next((s for s in snaps if s.get("snapshot_id") == snapshot_id), None)
    if not snap:
        raise NotFoundError("Snapshot not found")

    state = snap.get("sml_blob") or snap.get("state") or {}
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except Exception:
            state = {}

    # Extract conversion summary from state
    datasets = state.get("datasets") or state.get("models") or []
    total_models = len(datasets)
    total_columns = sum(len(d.get("columns", [])) for d in datasets if isinstance(d, dict))

    # Extract warnings/collisions if present in the snapshot metadata or run logs
    warnings: List[Dict[str, Any]] = []

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
                "status": "MAPPED",
            }
            for d in datasets
            if isinstance(d, dict)
        ],
        "warnings": warnings,
    }
