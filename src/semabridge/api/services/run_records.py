"""Authoritative project-run records in the ``runs`` table (run_kind='project').

One run id (a uuid, or a legacy ``run-<ms>`` id imported by sb0004) names the
run everywhere: this row, the compat-store copy, the per-model log directory
(output/runs/<project>/<run_id>/), report files and the UI. Per-model engine
rows link to it through ``parent_run_id``.

The DB row is authoritative for listing and status (it is what the stale
sweep and other processes see); the in-memory compat-store copy only adds
the large per-run blobs (results, logs, summary) while the process that ran
it is alive.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BLOB_KEYS = frozenset({"summary", "results", "logs", "stage_states"})
# Columns win over meta_json for these keys: they are what other processes
# and the stale sweep update.
_TERMINAL_STATUSES = frozenset({"success", "warning", "partial", "failed", "interrupted", "skipped", "conflict"})


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso_z(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _meta(run: Dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in run.items() if k not in _BLOB_KEYS}, default=str)


def _error_message(run: Dict[str, Any]) -> Optional[str]:
    status = str(run.get("status") or "").lower()
    if status in ("success", "running", "queued"):
        return None
    return str(run.get("error") or run.get("message") or "") or None


def _session():
    from semabridge.repository.orm.session_factory import db_manager

    return db_manager.get_session()


def save_project_run(run: Dict[str, Any]) -> bool:
    """Insert or update the project-run row for ``run`` (never raises)."""
    from semabridge.core.run_coordination import WORKER_ID
    from semabridge.repository.orm.models import Run

    run_id = str(run.get("run_id") or run.get("id") or "")
    project_id = str(run.get("project_id") or "")
    if not (run_id and project_id):
        return False
    status = str(run.get("status") or "running").lower()[:20]
    try:
        with _session() as session:
            row = session.get(Run, run_id)
            if row is None:
                row = Run(
                    run_id=run_id,
                    project_id=project_id,
                    started_at=_parse_ts(run.get("started_at")) or datetime.now(timezone.utc),
                    run_kind="project",
                    worker_id=WORKER_ID,
                    heartbeat_at=datetime.now(timezone.utc),
                )
                session.add(row)
            row.run_kind = "project"
            row.status = status
            row.completed_at = _parse_ts(run.get("completed_at"))
            row.duration_ms = int(run.get("duration_ms") or 0) or None
            row.run_type = str(run.get("run_type") or "SYNC")[:50]
            row.sync_mode = str(run.get("sync_mode") or "copy")[:20]
            row.source_type = (str(run.get("source_type"))[:50] if run.get("source_type") else None)
            row.target_type = (str(run.get("target_type"))[:50] if run.get("target_type") else row.target_type)
            row.schedule_label = (str(run.get("schedule"))[:50] if run.get("schedule") else None)
            row.message = run.get("message")
            row.error_message = _error_message(run)
            row.retry_of_run_id = run.get("retry_of_run_id") or None
            row.demo_mode = bool(run.get("demo_mode"))
            row.demo_masked = bool(run.get("demo_mode")) and status in ("failed", "partial", "warning", "interrupted")
            row.meta_json = _meta(run)
            session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Project run %s not persisted to the runs table: %s", run_id, exc)
        return False


def close_model_rows(run_id: str, *, status: str = "failed", message: str = "") -> int:
    """Finish model rows of ``run_id`` still marked running (their engine
    raised before recording an outcome). Returns rows changed."""
    from sqlalchemy import update

    from semabridge.core import run_coordination
    from semabridge.repository.orm.models import Run

    try:
        with _session() as session:
            ids = [
                r[0] for r in session.query(Run.run_id)
                .filter(Run.parent_run_id == run_id, Run.status.in_(("running", "queued"))).all()
            ]
            if not ids:
                return 0
            session.execute(
                update(Run).where(Run.run_id.in_(ids)).values(
                    status=status,
                    error_message=message or "Model execution ended without recording an outcome.",
                    completed_at=datetime.now(timezone.utc),
                ).execution_options(synchronize_session=False)
            )
            session.commit()
        for rid in ids:
            run_coordination.unregister_live_run(rid)
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not close model rows of %s: %s", run_id, exc)
        return 0


def _row_to_run(row: Any) -> Dict[str, Any]:
    def col(name: str) -> Any:
        return getattr(row, name, None)

    try:
        meta = json.loads(col("meta_json")) if col("meta_json") else {}
    except (TypeError, ValueError):
        meta = {}
    run = dict(meta) if isinstance(meta, dict) else {}
    status = col("status") or "unknown"
    run.update({
        "run_id": col("run_id"),
        "id": col("run_id"),
        "project_id": col("project_id"),
        "status": status,
        "started_at": iso_z(col("started_at")) or run.get("started_at"),
        "completed_at": iso_z(col("completed_at")),
        "heartbeat_at": iso_z(col("heartbeat_at")),
        "run_type": col("run_type") or run.get("run_type") or "SYNC",
        "sync_mode": col("sync_mode") or run.get("sync_mode") or "copy",
        "demo_mode": bool(col("demo_mode")),
        "run_kind": "project",
    })
    if col("duration_ms") is not None:
        run["duration_ms"] = col("duration_ms")
    if col("message"):
        run["message"] = col("message")
    error_message = col("error_message")
    if error_message and str(status).lower() not in ("success", "running", "queued"):
        run.setdefault("error", error_message)
        if not col("message"):
            run["message"] = error_message
    if col("schedule_label"):
        run["schedule"] = col("schedule_label")
    if col("retry_of_run_id"):
        run["retry_of_run_id"] = col("retry_of_run_id")
    for key in ("before_target_snapshot_ids", "after_target_snapshot_ids"):
        raw = col(key)
        if raw and key not in run:
            try:
                run[key] = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                pass
    run.setdefault("taken_at", run.get("started_at"))
    return run


def list_project_runs(project_id: str, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
    """Project runs of ``project_id``, newest first; None if the DB failed."""
    from sqlalchemy import select

    from semabridge.repository.orm.models import Run

    try:
        with _session() as session:
            rows = session.execute(
                select(Run)
                .where(Run.project_id == str(project_id), Run.run_kind == "project")
                .order_by(Run.started_at.desc())
                .limit(limit)
            ).scalars().all()
            return [_row_to_run(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to list project runs from the runs table: %s", exc)
        return None


def get_project_run(run_id: str) -> Optional[Dict[str, Any]]:
    from semabridge.repository.orm.models import Run

    try:
        with _session() as session:
            row = session.get(Run, str(run_id))
            if row is None or row.run_kind != "project":
                return None
            return _row_to_run(row)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read project run %s: %s", run_id, exc)
        return None


def merge_with_memory(db_run: Dict[str, Any], mem_run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The in-memory copy contributes its blobs and any key the DB row lacks;
    the DB row's status/timestamps/message are authoritative."""
    if not isinstance(mem_run, dict):
        return db_run
    merged = dict(mem_run)
    authoritative = ("status", "completed_at", "heartbeat_at", "message", "error", "duration_ms")
    for key, value in db_run.items():
        if key in authoritative or key not in merged or merged.get(key) in (None, "", []):
            merged[key] = value
    if str(db_run.get("status") or "").lower() == str(mem_run.get("status") or "").lower():
        # Same status: in-memory message/logs are at least as fresh.
        for key in ("message",):
            if mem_run.get(key):
                merged[key] = mem_run[key]
    return merged
