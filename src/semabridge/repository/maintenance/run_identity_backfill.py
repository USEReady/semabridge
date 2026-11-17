"""One-off backfill for the single run identity (migration sb0004).

Before sb0004 a project run lived in two places under two ids: the JSON
compat store held the run as ``run-<ms>`` and the ``runs`` table held one
engine row per model under its own uuid, with nothing linking them. sb0004
adds ``runs.run_kind`` ("project" for the run a user started, "model" for an
engine row) and ``runs.parent_run_id`` (model row -> its project run).

This backfill:

* imports every run of the compat store as a ``run_kind='project'`` row of
  ``runs`` (same id, so history, reports and per-model rows keep working),
  for projects that exist in ``projects`` (the FK); never deletes anything;
* links the engine rows of those runs to them: an engine row of the same
  project (not a dry run) that started inside the run's start/completion
  window belongs to it (PostgreSQL ``UPDATE ... FROM``).

Idempotent: an imported run is skipped on a second pass, a linked row is
never relinked.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_BLOB_KEYS = {"summary", "results", "logs", "stage_states"}


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # The compat store wrote datetime.utcnow().isoformat(): naive UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_store_runs(store_path: Optional[Path]) -> List[Dict[str, Any]]:
    if not store_path or not Path(store_path).exists():
        return []
    try:
        data = json.loads(Path(store_path).read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("run identity backfill: store unreadable (%s)", exc)
        return []
    runs: List[Dict[str, Any]] = []
    for pid, items in (data.get("project_runs") or {}).items():
        for run in items or []:
            if isinstance(run, dict) and (run.get("run_id") or run.get("id")):
                run = {k: v for k, v in run.items() if k not in _BLOB_KEYS}
                run.setdefault("project_id", pid)
                runs.append(run)
    return runs


def backfill_run_identity(conn: Any, store_runs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Import store runs as project rows and link their engine rows."""
    from sqlalchemy import text

    report = {"store_runs": 0, "imported": 0, "already_present": 0, "skipped_no_project": 0, "linked_model_rows": 0}
    projects = {r[0] for r in conn.execute(text("SELECT project_id FROM projects"))}
    existing = {r[0] for r in conn.execute(text("SELECT run_id FROM runs"))}
    imported: List[str] = []
    for run in store_runs:
        report["store_runs"] += 1
        run_id = str(run.get("run_id") or run.get("id"))
        project_id = str(run.get("project_id") or "")
        if run_id in existing:
            conn.execute(text("UPDATE runs SET run_kind = 'project' WHERE run_id = :r"), {"r": run_id})
            report["already_present"] += 1
            imported.append(run_id)
            continue
        if project_id not in projects:
            report["skipped_no_project"] += 1
            continue
        started = _parse_ts(run.get("started_at") or run.get("taken_at")) or datetime.now(timezone.utc)
        conn.execute(
            text(
                "INSERT INTO runs (run_id, project_id, started_at, completed_at, status, source_type, target_type, "
                "error_message, duration_ms, run_type, sync_mode, demo_mode, demo_masked, run_kind, "
                "schedule_label, message, meta_json) VALUES (:run_id, :project_id, :started, :completed, :status, "
                ":source_type, :target_type, :error, :duration, :run_type, :sync_mode, :demo, false, 'project', "
                ":schedule, :message, :meta)"
            ),
            {
                "run_id": run_id,
                "project_id": project_id,
                "started": started,
                "completed": _parse_ts(run.get("completed_at")),
                "status": str(run.get("status") or "unknown")[:20],
                "source_type": (str(run.get("source_type"))[:50] if run.get("source_type") else None),
                "target_type": (str(run.get("target_type"))[:50] if run.get("target_type") else None),
                "error": run.get("error"),
                "duration": int(run.get("duration_ms") or 0),
                "run_type": str(run.get("run_type") or "SYNC")[:50],
                "sync_mode": str(run.get("sync_mode") or "copy")[:20],
                "demo": bool(run.get("demo_mode")),
                "schedule": (str(run.get("schedule"))[:50] if run.get("schedule") else None),
                "message": run.get("message"),
                "meta": json.dumps(run, default=str),
            },
        )
        imported.append(run_id)
        report["imported"] += 1

    # Engine rows of an imported run: same project, not a dry run, started
    # inside the run's [started_at, completed_at] window. Per-model summaries
    # carry only the project run id, so the time window is the link.
    for run_id in imported:
        result = conn.execute(
            text(
                "UPDATE runs AS child SET parent_run_id = parent.run_id FROM runs AS parent "
                "WHERE parent.run_id = :p AND child.run_kind = 'model' AND child.parent_run_id IS NULL "
                "AND child.project_id = parent.project_id "
                "AND COALESCE(child.run_type, '') <> 'DRY_RUN' "
                "AND parent.completed_at IS NOT NULL "
                "AND child.started_at >= parent.started_at AND child.started_at <= parent.completed_at"
            ),
            {"p": run_id},
        )
        report["linked_model_rows"] += int(result.rowcount or 0)
    return report
