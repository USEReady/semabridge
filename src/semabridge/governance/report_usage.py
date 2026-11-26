"""Which Power BI report visuals use which fields, per project and model.

Captured from the report layout at sync time (PBIX file, or Fabric reports
bound to the model when the report layer is on) and kept in the shared state
store, so Blast Radius can name the report pages a change affects. Replaced
on every sync of that model.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COLLECTION = "report_usage"


def _key(project_id: str, model: str) -> str:
    return f"{project_id}::{re.sub(r'[^0-9a-z]+', '_', str(model or '').lower()).strip('_')}"


def save(project_id: Optional[str], model: str, visuals: List[Dict[str, Any]]) -> bool:
    pid = str(project_id or "").strip()
    if not pid or pid.startswith("preview"):
        return False
    try:
        from semabridge.repository import state_store

        state_store.ensure_table()
        state_store.apply_changes([(COLLECTION, _key(pid, model), {
            "project_id": pid,
            "model": model,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "visuals": list(visuals or []),
        })])
        return True
    except Exception as exc:  # noqa: BLE001 -- never fails a sync
        logger.info("Report usage of %s/%s not saved: %s", pid, model, exc)
        return False


def load(project_id: Optional[str]) -> List[Dict[str, Any]]:
    """Every saved visual of the project (all its models); [] when none."""
    pid = str(project_id or "").strip()
    if not pid:
        return []
    try:
        from semabridge.repository import state_store

        state_store.ensure_table()
        docs = state_store.load_collection(COLLECTION) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Report usage of %s unavailable: %s", pid, exc)
        return []
    out: List[Dict[str, Any]] = []
    for key, doc in docs.items():
        if isinstance(doc, dict) and str(key).startswith(f"{pid}::"):
            out.extend(v for v in doc.get("visuals") or [] if isinstance(v, dict))
    return out


def backfill_from_pbix(project_id: Optional[str]) -> List[Dict[str, Any]]:
    """For a project synced before usage was recorded: read the report layout of
    the PBIX files its last successful sync used (still on disk) and save it."""
    pid = str(project_id or "").strip()
    if not pid:
        return []
    try:
        from pathlib import Path

        from semabridge.api.services.run_records import list_project_runs
        from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
        from semabridge.sources import report_parsing

        run = next(
            (r for r in (list_project_runs(pid, limit=20) or [])
             if str(r.get("status") or "").lower() in ("success", "warning")),
            None,
        )
        if run is None:
            return []
        from semabridge.api.services.project_shared import load_run_details

        results = run.get("results") or (load_run_details([str(run.get("run_id") or "")]).get(str(run.get("run_id") or "")) or {}).get("results") or []
        visuals: List[Dict[str, Any]] = []
        for res in results:
            path = Path(str((res or {}).get("pbix_path") or ""))
            if not path.is_file():
                continue
            connector = LocalPBIXConnector({"pbix_path": str(path)})
            try:
                connector._open_archive()  # noqa: SLF001 -- layout only, no model parse
                layout = connector._extract_report_layout()  # noqa: SLF001
            finally:
                connector._close_archive()  # noqa: SLF001
            if layout:
                found = report_parsing.report_field_usage(layout, report=path.stem)
                save(pid, path.stem, found)
                visuals.extend(found)
        return visuals
    except Exception as exc:  # noqa: BLE001
        logger.info("Report usage backfill for %s skipped: %s", pid, exc)
        return []
