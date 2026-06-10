"""Version service — versioning, restore preview, audit logs, model history, and project stats.

Provides restore preview, model history timeline, audit log management,
project statistics, and project storage stats.
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks

from semabridge.api.services.project_shared import (
    _compat_ensure_loaded,
    _compat_now_iso,
    _compat_project_runs,
    _compat_project_snapshots,
    _compat_projects,
    _compat_save_store,
    _compat_snapshot_groups,
)
from semabridge.domain.exceptions import NotFoundError


def _compat_log_audit(project_id: str, action: str, user: str, details: str) -> None:
    """Helper to record system events."""
    _compat_ensure_loaded()
    # In-memory audit log for compatibility store
    if "_audit_logs" not in _compat_snapshot_groups:
        _compat_snapshot_groups["_audit_logs"] = []

    _compat_snapshot_groups["_audit_logs"].insert(0, {
        "timestamp": _compat_now_iso(),
        "project_id": project_id,
        "action": action,
        "user": user,
        "details": details,
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


async def preview_restore_compat(project_id: str, snapshot_id: str) -> Dict[str, Any]:
    """Dry-run diff: Compare CURRENT state with TARGET snapshot state."""
    _compat_ensure_loaded()

    # Import shared helpers to avoid circular deps
    from semabridge.api.services.snapshot_service import (
        _compat_load_snapshot_state_from_orm,
        _compat_latest_sml_state,
        _diff_models,
    )

    # 1. Get current state
    current_state = _compat_latest_sml_state(project_id)

    # 2. Get target state
    target_snap = None
    for snap in _compat_project_snapshots.get(project_id, []):
        if snap.get("snapshot_id") == snapshot_id:
            target_snap = snap
            break

    if not target_snap:
        raise NotFoundError("Target snapshot not found")

    target_state = target_snap.get("state") or {}
    if not target_state:
        target_state = _compat_load_snapshot_state_from_orm(snapshot_id)
    if isinstance(target_state, str):
        try:
            target_state = json.loads(target_state)
        except Exception:
            target_state = {}

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
        },
    }


async def restore_project_version_compat(project_id: str, payload: Dict[str, Any], background_tasks: BackgroundTasks):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise NotFoundError("Project not found")
    snapshot_id = str((payload or {}).get("snapshot_id") or "").strip()
    if not snapshot_id:
        from semabridge.domain.exceptions import ValidationError
        raise ValidationError("snapshot_id is required")
    matches = [
        row for row in _compat_project_snapshots.get(project_id, [])
        if isinstance(row, dict) and str(row.get("snapshot_id") or "") == snapshot_id
    ]
    if not matches:
        raise NotFoundError("Snapshot not found")

    from semabridge.api.services.run_service import (
        _create_project_run,
        _run_project_background,
        _compat_sync_mode_for_restore_snapshot,
        _compat_apply_restore_overrides,
    )
    from semabridge.api.services.project_shared import (
        _compat_default_project_yaml,
        _compat_project_configs,
    )

    snapshot_row = matches[0]
    config_yaml = str(
        snapshot_row.get("project_config_yaml")
        or _compat_project_configs.get(project_id)
        or _compat_default_project_yaml(_compat_projects[project_id])
    )
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
    background_tasks.add_task(_run_project_background, restore_run, restore_cfg, restore_started)
    _compat_save_store()
    return {
        "status": "restored",
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "run_id": restore_run.get("id"),
        "run_type": "RESTORE",
        "intermediate_format": snapshot_row.get("intermediate_format") or "sml",
        "message": "Project configuration restored and restore run started.",
        "config_yaml": config_yaml,
    }


async def get_model_history_compat(project_id: str, model_name: str) -> List[Dict[str, Any]]:
    """Return a timeline of changes for a specific model across all snapshots."""
    _compat_ensure_loaded()
    history = []

    # Sort runs chronologically
    runs = sorted(_compat_project_runs.get(project_id, []), key=lambda x: x.get("started_at", ""))

    for run in runs:
        # Check if this model was involved in this run's snapshots
        snap_id = run.get("after_tgt_snapshots", [None])[0] or run.get("before_tgt_snapshots", [None])[0]
        if not snap_id:
            continue

        # Find snapshot
        snap = next(
            (s for s in _compat_project_snapshots.get(project_id, []) if s["snapshot_id"] == snap_id),
            None,
        )
        if not snap:
            continue

        # Check model state in this snapshot
        state = snap.get("state") or {}
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except Exception:
                state = {}

        models = state.get("models", [])
        model = next((m for m in models if m.get("name") == model_name), None)

        if model:
            history.append({
                "run_id": run["run_id"],
                "snapshot_id": snap_id,
                "timestamp": run["started_at"],
                "status": "active",
                "model_data": model,
            })

    return history


async def get_project_stats_compat(project_id: str) -> Dict[str, Any]:
    _compat_ensure_loaded()
    runs = _compat_project_runs.get(project_id, [])
    snaps = _compat_project_snapshots.get(project_id, [])

    total_size = sum(len(str(s.get("state", ""))) for s in snaps)
    avg_models = sum(
        len((s.get("state") or {}).get("models", [])) if isinstance(s.get("state"), dict) else 0
        for s in snaps
    ) / (len(snaps) or 1)

    return {
        "project_id": project_id,
        "total_runs": len(runs),
        "total_snapshots": len(snaps),
        "pinned_count": len([s for s in snaps if s.get("is_pinned")]),
        "storage_estimate": f"{total_size / 1024 / 1024:.2f} MB",
        "avg_models_per_snapshot": int(avg_models),
        "health_score": int(
            95 if len([r for r in runs if r.get("status") == "success"]) / (len(runs) or 1) > 0.8 else 70
        ),
    }
