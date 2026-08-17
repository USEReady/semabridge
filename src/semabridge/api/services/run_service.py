"""Run service — project run execution, background tasks, and run/conflict retrieval.

Provides run creation, background execution, run-now, get-runs, run-conflicts,
manual deploy, project lineage, and clear-job-runs.
"""
import json
import time as _time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

import semabridge.api.services.project_shared as project_shared
from fastapi import BackgroundTasks

from semabridge.api.services.project_shared import (
    _compat_default_project_yaml,
    _compat_ensure_loaded,
    _compat_load_repo_yaml_text,
    _compat_now_iso,
    _compat_project_configs,
    _compat_project_runs,
    _compat_projects,
    _compat_save_store,
    _compat_snapshot_groups,
    _compat_project_snapshots,
)
from semabridge.core.run_helpers import elapsed_ms, normalize_sync_mode, resolve_run_status
from semabridge.domain.exceptions import NotFoundError, ValidationError

# Backward-compatible alias
_compat_normalize_sync_mode = normalize_sync_mode


# ---------------------------------------------------------------------------
# Private helpers (also exported for use in version_service / schedule_service)
# ---------------------------------------------------------------------------

def _compat_parse_project_cfg_dict(project_cfg: str) -> Dict[str, Any]:
    try:
        parsed = yaml.safe_load(project_cfg) or {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_source_type_from_project_cfg(project_cfg: str, fallback: str = "fabric") -> str:
    parsed = _compat_parse_project_cfg_dict(project_cfg)
    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    source_type = str(source_cfg.get("type") or fallback).strip().lower()
    return source_type or fallback


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


def _compat_sync_mode_for_restore_snapshot(
    project_id: str, snapshot_id: str, payload: Optional[Dict[str, Any]] = None
) -> str:
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
        import logging
        logger = logging.getLogger(__name__)

        with db_manager.get_session() as session:
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
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug(
            "Could not infer restore sync_mode for %s/%s: %s", project_id, snapshot_id, exc
        )

    return "copy"


def _format_step_timestamp(iso_value: Optional[str]) -> str:
    """Format an ISO timestamp as 'HH:MM:SS', or '' if missing/unparseable."""
    if not iso_value:
        return ""
    try:
        import datetime as _datetime
        return _datetime.datetime.fromisoformat(str(iso_value)).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ""


def _collect_step_logs(summary: Dict[str, Any], prefix: str = "") -> List[str]:
    lines: List[str] = []
    for step in (summary.get("steps_completed") or []):
        if not isinstance(step, dict):
            continue
        detail = f" - {step.get('message')}" if step.get("message") else ""
        parts = [str(step.get("status") or "info").upper()]
        timestamp = _format_step_timestamp(step.get("completed_at") or step.get("started_at"))
        if timestamp:
            parts.append(timestamp)
        if prefix:
            parts.append(prefix)
        parts.append(f"Stage {step.get('step_number', '?')}: {step.get('step_name') or 'Unknown'}{detail}")
        lines.append(" ".join(parts))
    for err in (summary.get("errors") or []):
        if not isinstance(err, dict):
            continue
        parts = ["ERROR"]
        timestamp = _format_step_timestamp(summary.get("completed_at") or summary.get("started_at"))
        if timestamp:
            parts.append(timestamp)
        if prefix:
            parts.append(prefix)
        parts.append(
            f"Stage {err.get('step_number', '?')} ({err.get('step_name') or 'Execution'}) - {err.get('message') or 'Unknown error'}"
        )
        lines.append(" ".join(parts))
    return lines


def _build_run_logs(sync_result: Dict[str, Any]) -> List[str]:
    """Build one log line per stage.

    ``sync_result["summary"]`` is not an independent aggregate — for both
    single- and multi-model syncs it is simply the last model's own summary,
    re-surfaced at the top level (see ``last_summary`` in
    sync_execution_service.py). Rendering both it and ``results[*].summary``
    duplicated every stage line. Per-model results are the complete,
    correctly-scoped picture whenever they exist; the top-level summary is
    only used as a fallback for shapes that never populate ``results``.
    """
    results = [r for r in (sync_result.get("results") or []) if isinstance(r, dict)]
    if results:
        logs: List[str] = []
        for result in results:
            logs.extend(
                _collect_step_logs(
                    result.get("summary") if isinstance(result.get("summary"), dict) else {},
                    f"[{str(result.get('model') or 'Model')}]",
                )
            )
        return logs

    return _collect_step_logs(
        sync_result.get("summary") if isinstance(sync_result.get("summary"), dict) else {}
    )


def _build_stage_states(sync_result: Dict[str, Any]) -> List[Dict[str, str]]:
    defaults = [
        {"id": "extraction", "label": "Extraction", "status": "pending"},
        {"id": "osi_conversion", "label": "OSI Conversion", "status": "pending"},
        {"id": "sml_generation", "label": "SML Generation", "status": "pending"},
        {"id": "snowflake_deployment", "label": "Snowflake Deployment", "status": "pending"},
    ]
    steps = (
        sync_result.get("summary", {}).get("steps_completed", [])
        if isinstance(sync_result.get("summary"), dict)
        else []
    )
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
    return [
        {"id": item["id"], "label": item["label"], "status": status_by_stage.get(item["id"], item["status"])}
        for item in defaults
    ]


def _run_retention_background(project_id: str):
    """Internal helper to run retention policy in a separate thread."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.api.services.retention_service import apply_retention_policy
        with db_manager.get_session() as session:
            apply_retention_policy(session, project_id)
            logger.info("Background retention policy completed for project %s", project_id)
    except Exception as exc:
        logger.error("Background retention policy failed for %s: %s", project_id, exc)


def _create_project_run(
    project_id: str,
    schedule_label: str = "Manual",
    run_type: str = "SYNC",
    project_cfg_override: Optional[str] = None,
    restore_snapshot_id: Optional[str] = None,
    sync_mode: str = "copy",
) -> tuple:
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise NotFoundError("Project not found")

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
    project_meta = _compat_projects[project_id]
    run = {
        "run_id": run_id,
        "id": run_id,
        "project_id": project_id,
        "project_name": project_meta.get("name", project_id),
        "run_type": str(run_type or "SYNC").upper(),
        "schedule": schedule_label,
        "status": "running",
        "sync_mode": sync_mode,
        "account_id": project_meta.get("account_id") or project_meta.get("source_account_id"),
        "source_type": _extract_source_type_from_project_cfg(
            project_cfg,
            fallback=str(project_meta.get("source") or "fabric").lower(),
        ),
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
        from semabridge.api.services.snapshot_service import _compat_capture_snapshots_for_run
        _compat_capture_snapshots_for_run(project_id=project_id, run=run, project_cfg=project_cfg, stage="before")
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("Pre-sync snapshot capture skipped for %s: %s", project_id, exc)
    _compat_save_store()
    return run, project_cfg, started


async def _perform_project_run(run: dict, project_cfg: str, started: float) -> dict:
    import logging
    logger = logging.getLogger(__name__)

    project_id = str(run.get("project_id") or "")
    try:
        from semabridge.api.services.core_domain_service import sync_models
        from semabridge.api.services.snapshot_service import _compat_capture_snapshots_for_run

        sync_payload: Dict[str, Any] = {
            "content": project_cfg,
            "project_id": project_id,
            # Explicitly enable deployment — the config YAML controls the target
            # connector details, but the deploy flag must be set here so
            # execute_sync_request does not skip Stage 8/9.
            "deploy": True,
        }
        account_id = run.get("account_id")
        if account_id:
            sync_payload["account_id"] = account_id
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
        run["duration_ms"] = elapsed_ms(started)
        run["completed_at"] = _compat_now_iso()
        run["status"] = resolve_run_status(sync_result or {})
        # Propagate run result back to in-memory project so the Projects page
        # shows the real status instead of the initial "draft" placeholder.
        _run_final_status = run["status"]
        _project_status_map = {
            "success": "active",
            "warning": "warning",
            "partial": "warning",
            "failed": "failed",
        }
        if project_id in _compat_projects:
            _compat_projects[project_id]["status"] = _project_status_map.get(
                _run_final_status, _run_final_status
            )
            # Persist the updated status so the store file has the correct value
            # on the next server start (avoids reverting to "draft" on restart).
            try:
                from semabridge.api.services.project_shared import _compat_save_store
                _compat_save_store()
            except Exception:
                pass
        run["summary"] = (sync_result or {}).get("summary") or {}
        run["results"] = (sync_result or {}).get("results") or []
        run["models_synced"] = int((sync_result or {}).get("models_synced") or 0)
        run["total_models"] = int((sync_result or {}).get("total_models") or 0)
        run["logs"] = _build_run_logs(sync_result or {})
        run["stage_states"] = _build_stage_states(sync_result or {})

        # Post-deploy reconciliation: verify every Stage 6 snapshot metric is
        # either live in the deployed semantic view or already recorded in
        # dropped_entities above. Best-effort and non-fatal on purpose — a
        # reconciliation failure (no Snowflake target, the live GET_DDL query
        # itself failing, etc.) must never flip an otherwise-successful
        # deploy to "failed". Unaccounted metrics are surfaced through the
        # same dropped_entities list DroppedFieldsPanel already renders,
        # tagged with stage="reconciliation" so they group under their own
        # heading rather than needing a new UI mechanism.
        try:
            if (
                run["status"] != "failed"
                and str(run["summary"].get("target_type") or "").lower() == "snowflake"
                and run["summary"].get("sml_snapshot_id")
            ):
                from semabridge.core.reconciliation import reconcile_run as _reconcile_run

                recon = _reconcile_run(run["id"])
                run["summary"]["reconciliation"] = {
                    "is_clean": recon.is_clean(),
                    "unaccounted": recon.unaccounted,
                    "deployed_live_count": len(recon.deployed_live_metrics),
                    "deployed_dead_count": len(recon.deployed_dead_metrics),
                    "ddl_error": recon.ddl_error,
                }
                if not recon.is_clean():
                    logger.warning(
                        "[%s] Post-deploy reconciliation found unaccounted metrics: %s",
                        project_id, recon.unaccounted,
                    )
                    unaccounted_entries = [
                        {
                            "entity_kind": "metric",
                            "entity_name": base,
                            "dataset": None,
                            "stage": "reconciliation",
                            "reason": (
                                "This metric normalizes to a name present in the Stage 6 "
                                "snapshot but absent from both the deployed semantic "
                                "view's DDL and the drop ledger — possible silent loss "
                                "during deployment."
                            ),
                            "detail": f"{count} snapshot occurrence(s) unaccounted for.",
                            "by_design": False,
                        }
                        for base, count in recon.unaccounted.items()
                    ]
                    run["summary"].setdefault("dropped_entities", [])
                    run["summary"]["dropped_entities"].extend(unaccounted_entries)
                    for result_entry in run.get("results") or []:
                        if not isinstance(result_entry, dict):
                            continue
                        result_summary = result_entry.get("summary") or {}
                        if result_summary.get("sml_snapshot_id") == recon.snapshot_id:
                            result_entry.setdefault("dropped_entities", [])
                            result_entry["dropped_entities"].extend(unaccounted_entries)
        except Exception as recon_exc:
            logger.warning(
                "[%s] Post-deploy reconciliation skipped (non-fatal): %s",
                project_id, recon_exc,
            )

        run["message"] = (
            "Execution completed successfully."
            if run["status"] == "success"
            else "Execution completed with warnings."
            if run["status"] == "warning"
            else str(
                (((run["summary"].get("errors") or [{}])[0]).get("message"))
                or "Execution failed."
            )
        )

        try:
            preferred_snapshot_id = str((run.get("summary") or {}).get("sml_snapshot_id") or "")
            _compat_capture_snapshots_for_run(
                project_id=project_id,
                run=run,
                project_cfg=project_cfg,
                stage="after",
                preferred_snapshot_id=preferred_snapshot_id,
            )
        except Exception as exc:
            logger.debug("Post-sync snapshot capture skipped for %s: %s", project_id, exc)

        # Apply Retention Policy in background thread
        import threading
        threading.Thread(target=_run_retention_background, args=(project_id,), daemon=True).start()

        # Email notification when project has notification_email configured.
        # Always notify on scheduled runs; for manual runs only notify when
        # the sync completed with warnings or failures.
        try:
            _project_meta = _compat_projects.get(project_id) or {}
            _notify_email = str(_project_meta.get("notification_email") or "").strip()
            if _notify_email:
                _run_status = run.get("status", "")
                _run_type = str(run.get("schedule") or run.get("run_type") or "Manual").lower()
                _is_scheduled = "scheduled" in _run_type or "cron" in _run_type
                _needs_notify = _is_scheduled or _run_status in ("failed", "warning", "partial")
                if _needs_notify:
                    import os as _os
                    from semabridge.auth.email_service import send_run_summary_notification
                    threading.Thread(
                        target=send_run_summary_notification,
                        kwargs=dict(
                            project_name=_project_meta.get("name") or project_id,
                            project_id=project_id,
                            run_id=run.get("id") or run.get("run_id") or "",
                            status=_run_status,
                            changes_summary=run.get("summary") or {},
                            results=run.get("results") or [],
                            recipient_email=_notify_email,
                            frontend_url=_os.environ.get("FRONTEND_URL", "http://localhost:5173"),
                        ),
                        daemon=True,
                    ).start()
        except Exception as _notify_exc:
            logger.debug("Run notification skipped: %s", _notify_exc)

    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
        run["message"] = str(exc)
        run["completed_at"] = _compat_now_iso()
        run["logs"] = [f"ERROR Execution failed: {exc}"]
        try:
            from semabridge.api.services.snapshot_service import _compat_capture_snapshots_for_run
            _compat_capture_snapshots_for_run(
                project_id=project_id, run=run, project_cfg=project_cfg, stage="after"
            )
        except Exception as snap_exc:
            logger.debug("Post-failure snapshot capture skipped for %s: %s", project_id, snap_exc)
    finally:
        # Unconditional -- runs on both the success and exception paths
        # above, so a failed run still gets a downloadable report. Never
        # allowed to affect the run's own recorded outcome (write_run_report
        # swallows its own errors and returns None on failure).
        try:
            from semabridge.api.services.run_report_service import write_run_report
            run["report_path"] = write_run_report(run, project_cfg)
        except Exception as report_exc:
            logger.debug("Run report generation skipped for %s: %s", project_id, report_exc)
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


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

async def get_project_runs_compat(project_id: str):
    """Retrieve run history for a project.

    Falls back to ORM queries if the in-memory compatibility store is empty.
    """
    import logging
    logger = logging.getLogger(__name__)

    _compat_ensure_loaded()
    pid = str(project_id).strip()

    runs = _compat_project_runs.get(pid, [])
    if runs:
        return runs

    try:
        from semabridge.repository.orm.models import Run
        from sqlalchemy import select
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
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
    except Exception as exc:
        logger.error("Failed to retrieve runs from ORM: %s", exc)
        return []


async def run_project_now_compat(
    project_id: str, background_tasks: BackgroundTasks, payload: Optional[Dict[str, Any]] = None
):
    _compat_ensure_loaded()
    if bool((payload or {}).get("dry_run", False)):
        from semabridge.api.services.mapping_service import auto_map_compat
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
    restore_snapshot_id = (
        str(
            (payload or {}).get("restore_snapshot_id")
            or (payload or {}).get("snapshot_id")
            or ""
        ).strip()
        or None
    )
    config_override = None
    if run_type == "SYNC":
        modular_bundle = project_shared._compat_load_modular_project(project_id)
        base_cfg = (
            (str(modular_bundle.get("config_yaml") or "") if modular_bundle else "")
            or _compat_project_configs.get(project_id)
            or _compat_load_repo_yaml_text()
            or _compat_default_project_yaml(_compat_projects.get(project_id, {}))
        )
        from semabridge.api.services.mapping_service import _compat_apply_manual_mapping_overrides_to_cfg
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
    background_tasks.add_task(_run_project_background, run, project_cfg, started)
    return {"run_id": run["id"], "status": "running", "run_type": run_type, "message": "Sync started in background"}


async def get_run_report_compat(project_id: str, run_id: str) -> Optional[Dict[str, str]]:
    """Locate and read the Markdown report written by write_run_report for
    one run. Returns {"content": ..., "filename": ...} or None if no report
    exists (e.g. the run predates this feature, or generation failed).
    """
    from semabridge.api.services.run_report_service import REPORTS_ROOT

    _compat_ensure_loaded()
    report_path = None
    project_name = project_id
    for run in _compat_project_runs.get(project_id, []):
        if str(run.get("run_id") or run.get("id") or "") == run_id:
            report_path = run.get("report_path")
            project_name = run.get("project_name") or project_id
            break

    if not report_path:
        # In-memory compat record may have been evicted by a restart since
        # the run completed -- the file itself still lives at the fixed,
        # run_id-scoped path write_run_report always uses.
        candidate = REPORTS_ROOT / project_id / f"{run_id}.md"
        if candidate.exists():
            report_path = str(candidate)

    if not report_path:
        return None
    path = Path(report_path)
    if not path.exists():
        return None

    safe_project_name = str(project_name).strip().replace(" ", "_") or project_id
    return {
        "content": path.read_text(encoding="utf-8"),
        "filename": f"{safe_project_name}_{run_id}_report.md",
    }


async def get_run_conflicts_compat(run_id: str):
    """Retrieve sync conflicts for a specific run."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        return repo.get_sync_conflicts(run_id)
    except Exception as exc:
        logger.error("Failed to fetch conflicts for run %s: %s", run_id, exc)
        return []


async def manual_deploy_compat(
    project_id: str, snapshot_id: str, background_tasks: BackgroundTasks, comment: str = ""
) -> Dict[str, Any]:
    """Manually deploy a specific snapshot to target connectors."""
    _compat_ensure_loaded()
    payload = {
        "restore_snapshot_id": snapshot_id,
        "comment": comment or f"Manual deploy of {snapshot_id[:8]}",
        "run_type": "manual_deploy",
    }
    return await run_project_now_compat(project_id, background_tasks, payload)


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
                "is_pinned": snap.get("is_pinned", False),
            },
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
                "started_at": run.get("started_at"),
            },
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


async def clear_job_runs_compat(before: Optional[str] = None, user_id: Optional[str] = None):
    """Clear job runs, optionally filtered by date and/or user ownership.

    Args:
        before: ISO-8601 datetime string (e.g. "2026-05-31T06:23:53"). When
                provided, only runs whose started_at/created_at is on or before
                this timestamp are deleted.
        user_id: When provided (auth mode), only delete runs belonging to
                 projects owned by this user.
    """
    import logging
    from datetime import datetime, timezone

    logger = logging.getLogger(__name__)

    _compat_ensure_loaded()

    # Parse the cutoff timestamp
    cutoff_dt: Optional[datetime] = None
    if before:
        try:
            # Accept both "2026-05-31T06:23:53" and "2026-05-31 06:23:53" forms
            cutoff_dt = datetime.fromisoformat(before.replace(" ", "T"))
            if cutoff_dt.tzinfo is None:
                cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("clear_job_runs_compat: invalid 'before' value '%s' — ignoring filter", before)

    deleted_db = 0
    try:
        from semabridge.repository.orm.models import Run
        from sqlalchemy import select
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
            runs = session.execute(select(Run)).scalars().all()
            for r in runs:
                # Date filter
                if cutoff_dt is not None:
                    run_ts = getattr(r, "started_at", None) or getattr(r, "created_at", None)
                    if run_ts is not None:
                        if isinstance(run_ts, str):
                            try:
                                run_ts = datetime.fromisoformat(run_ts.replace("Z", "+00:00"))
                            except ValueError:
                                run_ts = None
                        if run_ts is not None:
                            if run_ts.tzinfo is None:
                                run_ts = run_ts.replace(tzinfo=timezone.utc)
                            if run_ts > cutoff_dt:
                                continue  # newer than cutoff — keep
                # Owner filter
                if user_id:
                    from semabridge.api.services.project_ownership_service import is_project_owned_by_user
                    if not is_project_owned_by_user(str(r.project_id or ""), user_id,
                                                    log_denied=False, log_prefix="ClearRuns"):
                        continue
                session.delete(r)
                deleted_db += 1
            session.commit()
    except Exception as exc:
        logger.error("Failed to connect to ORM to clear runs: %s", exc)

    # Mirror filter on the in-memory compat store
    deleted_compat = 0
    for project_id in list(_compat_project_runs.keys()):
        if user_id:
            from semabridge.api.services.project_ownership_service import is_project_owned_by_user
            if not is_project_owned_by_user(project_id, user_id, log_denied=False, log_prefix="ClearRuns"):
                continue
        if cutoff_dt is not None:
            kept = []
            for run in _compat_project_runs.get(project_id, []):
                run_ts_str = run.get("started_at") or run.get("created_at") or run.get("timestamp") or ""
                if run_ts_str:
                    try:
                        run_ts = datetime.fromisoformat(str(run_ts_str).replace("Z", "+00:00"))
                        if run_ts.tzinfo is None:
                            run_ts = run_ts.replace(tzinfo=timezone.utc)
                        if run_ts > cutoff_dt:
                            kept.append(run)
                            continue
                    except ValueError:
                        pass
                deleted_compat += 1
            _compat_project_runs[project_id] = kept
        else:
            deleted_compat += len(_compat_project_runs.get(project_id, []))
            _compat_project_runs[project_id] = []

    _compat_save_store()

    label = f"on or before {before}" if before else "all"
    return {
        "status": "success",
        "message": f"Cleared runs {label}.",
        "deleted_db": deleted_db,
        "deleted_compat": deleted_compat,
    }
