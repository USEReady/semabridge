"""Explain a query that failed because the deployed view is not there.

Databricks answers ``[TABLE_OR_VIEW_NOT_FOUND] The table or view `c`.`s`.`v`
cannot be found ...``. For a SemaBridge project that means the view its sync
deployed was dropped since, or the sync never created it: the fix is a
re-sync, which the raw message does not say.
"""
from __future__ import annotations

import re
from typing import Optional

_NOT_FOUND = re.compile(r"TABLE_OR_VIEW_NOT_FOUND\]?\s*The table or view (`[^ ]+`) cannot be found", re.I)


def _last_successful_sync(project_id: str) -> Optional[str]:
    try:
        from semabridge.api.services.run_records import list_project_runs

        for run in list_project_runs(project_id, limit=50) or []:
            if str(run.get("status") or "").lower() in ("success", "warning"):
                return str(run.get("completed_at") or run.get("started_at") or "")[:10] or None
    except Exception:  # noqa: BLE001 -- the hint is best-effort
        return None
    return None


def explain(error: Optional[str], project_id: Optional[str] = None) -> Optional[str]:
    """The error with a plain explanation in front when a view is missing; else unchanged."""
    text = str(error or "")
    m = _NOT_FOUND.search(text)
    if not m:
        return error
    view = m.group(1).replace("`", "")
    last = _last_successful_sync(project_id) if project_id else None
    when = f" The project's last successful sync was on {last}." if last else ""
    return (
        f"The view {view} is not in Databricks.{when} It was removed after that sync, or the sync "
        f"did not create it. Re-sync the project to deploy it again. (Databricks: {text.split('. ')[0]}.)"
    )
