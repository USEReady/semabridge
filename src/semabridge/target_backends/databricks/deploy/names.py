"""Deployed-name history fed back into planning (Phase 3.8, audit A5).

After a successful deploy, every deployed view, field, measure and stub table
name is recorded with its source identity and its metric-view occurrence. The
next plan reserves those names (PlannerSettings.reserved_names /
reserved_view_names / reserved_table_names), so re-planning keeps what
consumers already use. View hashes are not name preservation; these rows are.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def source_key(source_id: Any) -> Dict[str, Any]:
    """A plan SourceId (dataclass or dict) -> a stored key."""
    if source_id is None:
        return {}
    get = source_id.get if isinstance(source_id, dict) else (lambda k, d=None: getattr(source_id, k, d))
    return {"kind": get("kind"), "table": get("table") or "", "name": get("name") or "",
            "aggregation": get("aggregation"), "lineage": get("lineage")}


def names_from_bundle_views(plan_views: Iterable[Dict[str, Any]], deployed_views: Iterable[str],
                            tables: Iterable[Dict[str, Any]], created_stubs: Iterable[str]) -> List[Dict[str, Any]]:
    """Rows to record for the views (and stub tables) this run deployed."""
    done = {str(v) for v in deployed_views}
    rows: List[Dict[str, Any]] = []
    for v in plan_views:
        name = str(v.get("name"))
        if name not in done:
            continue
        src = str(v.get("source_dataset") or "")
        rows.append({"kind": "view", "view_name": name, "view_source": src,
                     "source_key": {"kind": "view", "table": src}, "target_name": name})
        for kind, items in (("field", v.get("fields") or []), ("measure", v.get("measures") or [])):
            for it in items:
                key = source_key(it.get("source_id"))
                if key:
                    rows.append({"kind": kind, "view_name": name, "view_source": src, "source_key": key,
                                 "target_name": str(it.get("name"))})
    stubs = {str(s) for s in created_stubs}
    for t in tables:
        if t.get("dataset") in stubs and t.get("name"):
            rows.append({"kind": "stub_table", "view_name": None, "view_source": str(t["dataset"]),
                         "source_key": {"kind": "table", "table": str(t["dataset"])}, "target_name": str(t["name"])})
    return rows


def reserved_from_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """{"reserved_names": {view source: {SourceId: name}}, "reserved_view_names":
    {view source: view}, "reserved_table_names": {dataset: table}}."""
    from semabridge.target_backends.databricks.planner.model import SourceId

    names: Dict[str, Dict[Any, str]] = {}
    views: Dict[str, str] = {}
    tables: Dict[str, str] = {}
    for r in rows:
        kind, src, key = r.get("kind"), str(r.get("view_source") or ""), r.get("source_key") or {}
        if kind == "view" and src:
            views[src] = str(r["target_name"])
        elif kind == "stub_table" and src:
            tables[src] = str(r["target_name"])
        elif kind in ("field", "measure") and src and key.get("kind"):
            sid = SourceId(kind=str(key["kind"]), table=str(key.get("table") or ""), name=str(key.get("name") or ""),
                           aggregation=key.get("aggregation"), lineage=key.get("lineage"))
            names.setdefault(src, {})[sid] = str(r["target_name"])
    return {"reserved_names": names, "reserved_view_names": views, "reserved_table_names": tables}


def reserved_for(project_id: Optional[str], model_key: Optional[str]) -> Dict[str, Any]:
    """Reserved names from the newest successful deploy; empty when there is none
    or the store cannot be read (planning never fails because of history)."""
    empty = {"reserved_names": {}, "reserved_view_names": {}, "reserved_table_names": {}}
    if not project_id:
        return empty
    try:
        from semabridge.target_backends.databricks.deploy import store

        return reserved_from_rows(store.deployed_names(str(project_id), str(model_key or "")))
    except Exception as exc:  # pragma: no cover - store unavailable
        logger.warning("Deployed Databricks names could not be read (planning without them): %s", exc)
        return empty
