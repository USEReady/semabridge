"""Per-model run results that survive server restarts.

When a project run completes, every model's outcome is written to the
``run_model_results`` table (status, view, duration, drop summary,
reconciliation, artifact paths) and its JSON report summary to a file next
to its Markdown report. The Runs page reads both back after a restart, so a
model's section never degrades to "Unknown / No logs".
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _reports_root() -> Path:
    from semabridge.api.services.run_report_service import REPORTS_ROOT

    return REPORTS_ROOT


def _safe_label(model: str) -> str:
    from semabridge.api.services.run_report_service import _sanitize_report_filename_part

    return _sanitize_report_filename_part(model, "model")


def model_summary_path(project_id: str, run_id: str, model: str) -> Path:
    return _reports_root() / str(project_id) / f"{run_id}__{_safe_label(model)}.summary.json"


def model_report_path(project_id: str, run_id: str, model: str) -> Path:
    return _reports_root() / str(project_id) / f"{run_id}__{_safe_label(model)}.md"


def model_log_file(project_id: str, run_id: str, model: str) -> Path:
    from semabridge.core.run_model_logs import model_log_path

    return model_log_path(project_id, run_id, model)


def build_model_summary_data(run: Dict[str, Any], result: Dict[str, Any], project_cfg: str) -> Dict[str, Any]:
    """The JSON report summary for ONE model of a run."""
    from semabridge.api.services.run_report_service import _describe_source_for_result, build_run_report_data

    scoped_run = dict(run)
    scoped_run["results"] = [result]
    source_override = _describe_source_for_result(result, project_cfg, run)
    return build_run_report_data(scoped_run, project_cfg, source_override=source_override)


def _error_message(result: Dict[str, Any]) -> Optional[str]:
    errors = ((result.get("summary") or {}).get("errors") or [])
    if errors and isinstance(errors[0], dict):
        return str(errors[0].get("message") or "") or None
    return None


def _results(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in (run.get("results") or []) if isinstance(r, dict) and r.get("model")]


def persist_run_model_results(run: Dict[str, Any], project_cfg: str) -> int:
    """Write every model's outcome (DB row + JSON summary file). Replaces any
    rows already stored for this run. Never raises; returns rows written."""
    project_id = str(run.get("project_id") or "")
    run_id = str(run.get("id") or run.get("run_id") or "")
    results = _results(run)
    if not (project_id and run_id and results):
        return 0

    rows = []
    for position, result in enumerate(results):
        model = str(result["model"])
        summary_file = model_summary_path(project_id, run_id, model)
        try:
            summary_file.parent.mkdir(parents=True, exist_ok=True)
            summary_file.write_text(
                json.dumps(build_model_summary_data(run, result, project_cfg), default=str),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write summary for model %s of run %s: %s", model, run_id, exc)
            summary_file = None
        report_file = model_report_path(project_id, run_id, model)
        log_file = model_log_file(project_id, run_id, model)
        rows.append({
            "id": str(uuid.uuid4()),
            "run_id": run_id,
            "project_id": project_id,
            "model": model,
            "position": position,
            "status": str(result.get("status") or "unknown"),
            "view_name": result.get("view_name"),
            "physical_namespace": result.get("physical_namespace"),
            "duration_ms": int(result["duration_ms"]) if result.get("duration_ms") is not None else None,
            "error_message": _error_message(result),
            "dropped_json": json.dumps(result.get("dropped_entities") or [], default=str),
            "reconciliation_json": json.dumps(result.get("reconciliation"), default=str)
            if result.get("reconciliation") is not None else None,
            "report_path": str(report_file) if report_file.exists() else None,
            "summary_path": str(summary_file) if summary_file and summary_file.exists() else None,
            "log_path": str(log_file) if log_file.exists() else None,
        })

    try:
        from sqlalchemy import delete

        from semabridge.repository.orm.models import Base, RunModelResult
        from semabridge.repository.orm.session_factory import db_manager

        Base.metadata.create_all(bind=db_manager.get_engine(), tables=[RunModelResult.__table__])
        with db_manager.get_session() as session:
            session.execute(delete(RunModelResult).where(RunModelResult.run_id == run_id))
            for row in rows:
                session.add(RunModelResult(**row))
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist per-model results for run %s: %s", run_id, exc)
        return 0


def _summary_upsert_delta(summary_path: Optional[str]) -> Optional[Dict[str, Any]]:
    """UPSERT delta from a persisted per-model summary file (None for COPY)."""
    if not summary_path:
        return None
    try:
        data = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    entries = data.get("upsert_deltas") if isinstance(data, dict) else None
    if entries and isinstance(entries[0], dict):
        return entries[0].get("delta")
    return None


def _row_to_result(row: Any) -> Dict[str, Any]:
    def _loads(value, default):
        try:
            return json.loads(value) if value else default
        except (TypeError, ValueError):
            return default

    return {
        "model": row.model,
        "status": row.status,
        "view_name": row.view_name,
        "physical_namespace": row.physical_namespace,
        "duration_ms": row.duration_ms,
        "dropped_entities": _loads(row.dropped_json, []),
        "reconciliation": _loads(row.reconciliation_json, None),
        "error": row.error_message,
        "report_path": row.report_path,
        "summary_path": row.summary_path,
        "log_path": row.log_path,
        "upsert_delta": _summary_upsert_delta(row.summary_path),
        "persisted": True,
    }


def load_run_model_results(project_id: str, run_id: str) -> List[Dict[str, Any]]:
    """Per-model results stored for one run (ordered as the run listed them)."""
    try:
        from sqlalchemy import select

        from semabridge.repository.orm.models import Base, RunModelResult
        from semabridge.repository.orm.session_factory import db_manager

        Base.metadata.create_all(bind=db_manager.get_engine(), tables=[RunModelResult.__table__])
        with db_manager.get_session() as session:
            rows = session.execute(
                select(RunModelResult)
                .where(RunModelResult.run_id == str(run_id), RunModelResult.project_id == str(project_id))
                .order_by(RunModelResult.position)
            ).scalars().all()
            return [_row_to_result(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not load per-model results for run %s: %s", run_id, exc)
        return []


def run_belongs_to_project(project_id: str, run_id: str) -> bool:
    """True only when ``run_id`` is a run of ``project_id`` (in-memory run
    store, persisted per-model rows, or the run's own report/log files)."""
    pid, rid = str(project_id or "").strip(), str(run_id or "").strip()
    if not pid or not rid or "/" in rid or "\\" in rid or ".." in rid:
        return False
    try:
        import semabridge.api.services.project_shared as project_shared

        project_shared._compat_ensure_loaded()
        for candidate in project_shared._compat_project_runs.get(pid, []) or []:
            if str(candidate.get("run_id") or candidate.get("id") or "") == rid:
                return True
    except Exception:  # noqa: BLE001
        pass
    if load_run_model_results(pid, rid):
        return True
    root = _reports_root() / pid
    return (root / f"{rid}.md").exists() or any(root.glob(f"{rid}__*"))


def in_memory_run(project_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    try:
        import semabridge.api.services.project_shared as project_shared

        project_shared._compat_ensure_loaded()
        for candidate in project_shared._compat_project_runs.get(str(project_id), []) or []:
            if str(candidate.get("run_id") or candidate.get("id") or "") == str(run_id):
                return candidate
    except Exception:  # noqa: BLE001
        pass
    return None


def run_model_manifest(project_id: str, run_id: str) -> List[Dict[str, Any]]:
    """Every model of a run with its status and which artifacts exist.

    Live results (run in memory) win; persisted rows cover restarts; while a
    run is still in progress, models that have started writing their log are
    listed as running."""
    run = in_memory_run(project_id, run_id) or {}
    entries: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def _put(model: str, data: Dict[str, Any]) -> None:
        if model not in entries:
            order.append(model)
            entries[model] = {}
        entries[model].update({k: v for k, v in data.items() if v is not None})

    for row in load_run_model_results(project_id, run_id):
        _put(row["model"], row)
    for result in _results(run):
        _put(str(result["model"]), {
            "model": str(result["model"]),
            "status": result.get("status"),
            "view_name": result.get("view_name"),
            "physical_namespace": result.get("physical_namespace"),
            "duration_ms": result.get("duration_ms"),
            "dropped_entities": result.get("dropped_entities") or [],
            "reconciliation": result.get("reconciliation"),
            "upsert_delta": result.get("upsert_delta"),
            "error": _error_message(result),
        })
    if not order and str(run.get("status") or "").lower() == "running":
        from semabridge.core.run_model_logs import RUN_LOGS_ROOT, _safe_part

        run_dir = RUN_LOGS_ROOT / _safe_part(project_id, "project") / _safe_part(run_id, "run")
        if run_dir.exists():
            for log_file in sorted(run_dir.glob("*.log")):
                _put(log_file.stem, {"model": log_file.stem, "status": "running"})

    manifest = []
    for model in order:
        entry = {"model": model, "status": "unknown", **entries[model]}
        entry["has_report"] = model_report_path(project_id, run_id, model).exists()
        entry["has_summary"] = model_summary_path(project_id, run_id, model).exists()
        entry["has_log"] = model_log_file(project_id, run_id, model).exists()
        entry.pop("report_path", None)
        entry.pop("summary_path", None)
        entry.pop("log_path", None)
        manifest.append(entry)
    return manifest


def load_model_summary(project_id: str, run_id: str, model: str) -> Optional[Dict[str, Any]]:
    path = model_summary_path(project_id, run_id, model)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
