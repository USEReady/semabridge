"""Run preview service — fast snapshot diff for pre-run confirmation modal."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def get_run_preview_compat(project_id: str) -> Dict[str, Any]:
    """Compare the two most recent successful snapshots for a project and return
    a change summary.

    This is intentionally read-only and fast (< 50 ms) — it never extracts
    from the source or writes anything.  The result is displayed in the
    "Confirm Sync Run" modal so the user can see what changed last time before
    re-running.

    Response shape:
    {
        "has_prior_run": bool,
        "last_run_at": str | None,       # ISO timestamp of the most recent snapshot
        "total_changes": int,
        "summary": {
            "metrics_added": int,
            "metrics_removed": int,
            "metrics_changed": int,
            "tables_added": int,
            "tables_removed": int,
            "columns_added": int,
            "columns_removed": int,
            "relationships_added": int,
            "relationships_removed": int,
        },
        "changes": [                     # first 50 changes for the detail list
            {"object_type": str, "object_name": str, "diff_type": str}
        ]
    }
    """
    try:
        from semabridge.repository.orm.session_factory import db_manager
        repo = db_manager  # ModelRepository instance

        snapshots = repo.list_snapshots(project_id, limit=2, include_failed=False)
        if len(snapshots) < 2:
            return {
                "has_prior_run": len(snapshots) == 1,
                "last_run_at": snapshots[0].timestamp.isoformat() if snapshots else None,
                "total_changes": 0,
                "summary": _empty_summary(),
                "changes": [],
            }

        # newest first (list_snapshots returns newest first)
        new_snap = snapshots[0]
        old_snap = snapshots[1]

        # _compute_diff lives in model_repository module
        from semabridge.repository.model_repository import _compute_diff
        changes = _compute_diff(
            old_snap.sml_blob if isinstance(old_snap.sml_blob, dict) else {},
            new_snap.sml_blob if isinstance(new_snap.sml_blob, dict) else {},
        )

        summary = _empty_summary()
        change_list = []
        for c in changes:
            otype = str(c.object_type or "")
            dtype = str(c.diff_type or "")

            if otype == "metric" and dtype == "ADDED":
                summary["metrics_added"] += 1
            elif otype == "metric" and dtype == "DELETED":
                summary["metrics_removed"] += 1
            elif otype == "metric" and dtype == "MODIFIED":
                summary["metrics_changed"] += 1
            elif otype == "dataset" and dtype == "ADDED":
                summary["tables_added"] += 1
            elif otype == "dataset" and dtype == "DELETED":
                summary["tables_removed"] += 1
            elif otype == "dimension" and dtype == "ADDED":
                summary["columns_added"] += 1
            elif otype == "dimension" and dtype == "DELETED":
                summary["columns_removed"] += 1
            elif otype == "relationship" and dtype == "ADDED":
                summary["relationships_added"] += 1
            elif otype == "relationship" and dtype == "DELETED":
                summary["relationships_removed"] += 1

            if len(change_list) < 50:
                change_list.append({
                    "object_type": otype,
                    "object_name": str(c.object_name or ""),
                    "diff_type": dtype,
                })

        return {
            "has_prior_run": True,
            "last_run_at": new_snap.timestamp.isoformat() if new_snap.timestamp else None,
            "total_changes": len(changes),
            "summary": summary,
            "changes": change_list,
        }

    except Exception as exc:
        logger.debug("run_preview failed for %s: %s", project_id, exc)
        return {
            "has_prior_run": False,
            "last_run_at": None,
            "total_changes": 0,
            "summary": _empty_summary(),
            "changes": [],
        }


def _empty_summary() -> Dict[str, int]:
    return {
        "metrics_added": 0,
        "metrics_removed": 0,
        "metrics_changed": 0,
        "tables_added": 0,
        "tables_removed": 0,
        "columns_added": 0,
        "columns_removed": 0,
        "relationships_added": 0,
        "relationships_removed": 0,
    }
