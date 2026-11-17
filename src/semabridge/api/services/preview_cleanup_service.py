"""Cleanup of transient dry-run ``preview-*`` projects.

Every dry run creates (or reuses) a ``preview-<hash>`` project with its own
runs, snapshots and table bindings; nothing ever removed them. This module
finds preview projects that are

* older than ``older_than_days`` (``projects.last_updated``), and
* NOT referenced by the latest dry-run job of any real project (that job's
  per-file preview projects are what the project page / sync gate still
  shows),

and either reports them (default) or deletes them with their dependent rows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger("semabridge.api.preview_cleanup")

# Tables whose rows belong to a project (deleted with it, children first).
_DEPENDENT_TABLES = (
    "changes",               # via snapshots
    "run_model_results",
    "source_artifacts",      # via runs
    "table_bindings",
    "synonym_overrides",
    "precompute_aggregation_overrides",
    "snapshots",
    "runs",
    "mapping_overrides",
    "project_configs",
)


def _is_preview(pid: str) -> bool:
    return pid == "preview" or pid.startswith("preview-")


def cleanup_preview_projects(older_than_days: int = 7, *, apply: bool = False) -> Dict[str, Any]:
    """Report (``apply=False``) or delete stale preview projects."""
    from sqlalchemy import text

    from semabridge.repository.orm.session_factory import db_manager

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(older_than_days)))
    with db_manager.get_session() as session:
        conn = session.connection()
        dialect = conn.dialect.name
        tables = {
            r[0] for r in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'" if dialect == "sqlite" else
                "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
            ))
        }
        previews = [
            (str(r[0]), r[1]) for r in conn.execute(text(
                "SELECT project_id, last_updated FROM projects "
                "WHERE project_id = 'preview' OR project_id LIKE 'preview-%'"
            ))
        ]
        protected: Dict[str, str] = {}
        if {"dry_run_jobs", "dry_run_job_files"} <= tables:
            # Latest dry-run job of each real project -> its preview projects.
            rows = conn.execute(text(
                "SELECT j.project_id, f.preview_project_id FROM dry_run_jobs j "
                "JOIN dry_run_job_files f ON f.job_id = j.job_id "
                "WHERE j.created_at = (SELECT max(j2.created_at) FROM dry_run_jobs j2 WHERE j2.project_id = j.project_id) "
                "AND f.preview_project_id IS NOT NULL"
            ))
            for project_id, preview_id in rows:
                if project_id and not _is_preview(str(project_id)):
                    protected[str(preview_id)] = str(project_id)

        candidates: List[Dict[str, Any]] = []
        kept: List[Dict[str, Any]] = []
        for pid, last_updated in previews:
            ts = last_updated
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    ts = None
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            entry = {"project_id": pid, "last_updated": ts.isoformat() if ts else None}
            if pid in protected:
                kept.append({**entry, "reason": f"latest dry run of {protected[pid]}"})
                continue
            if ts is not None and ts > cutoff:
                kept.append({**entry, "reason": f"newer than {older_than_days} day(s)"})
                continue
            counts: Dict[str, int] = {}
            for table in ("runs", "snapshots", "table_bindings", "run_model_results"):
                if table in tables:
                    counts[table] = int(conn.execute(
                        text(f"SELECT count(*) FROM {table} WHERE project_id = :p"), {"p": pid}
                    ).scalar() or 0)
            candidates.append({**entry, "rows": counts})

        report: Dict[str, Any] = {
            "mode": "apply" if apply else "report",
            "older_than_days": older_than_days,
            "cutoff": cutoff.isoformat(),
            "preview_projects_total": len(previews),
            "would_delete" if not apply else "deleted": candidates,
            "kept": kept,
            "row_totals": {
                t: sum(c["rows"].get(t, 0) for c in candidates)
                for t in ("runs", "snapshots", "table_bindings", "run_model_results")
            },
        }
        if not apply or not candidates:
            session.rollback()
            return report

        ids = [c["project_id"] for c in candidates]
        for pid in ids:
            params = {"p": pid}
            if "changes" in tables and "snapshots" in tables:
                conn.execute(text(
                    "DELETE FROM changes WHERE snapshot_id IN (SELECT snapshot_id FROM snapshots WHERE project_id = :p)"
                ), params)
            if "source_artifacts" in tables and "runs" in tables:
                conn.execute(text(
                    "DELETE FROM source_artifacts WHERE run_id IN (SELECT run_id FROM runs WHERE project_id = :p)"
                ), params)
            for table in _DEPENDENT_TABLES:
                if table in ("changes", "source_artifacts") or table not in tables:
                    continue
                conn.execute(text(f"DELETE FROM {table} WHERE project_id = :p"), params)
            conn.execute(text("DELETE FROM projects WHERE project_id = :p"), params)
    # Drop them from the in-memory cache too.
    try:
        import semabridge.api.services.project_shared as ps

        with ps.compat_store_lock():
            for pid in ids:
                for cache in (ps._compat_projects, ps._compat_project_configs, ps._compat_project_runs,
                              ps._compat_project_snapshots, ps._compat_snapshot_groups):
                    cache.pop(pid, None)
        ps._compat_save_store()
    except Exception as exc:
        logger.error("Preview cleanup: cache update failed: %s", exc)
    logger.warning("Preview cleanup deleted %d preview project(s)", len(ids))
    return report
