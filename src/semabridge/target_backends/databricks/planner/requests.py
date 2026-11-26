"""Saved names as planner requests (integration of the dry-run screen's renames).

A saved name arrives as (source_path, name, origin) -- read by Step 6b from
the project's ``mappings_overrides`` without modifying the model:

  * ``datasets.<table>.columns.<column>`` -> the field (table, column)
  * ``metrics.<measure>``                  -> the measure; a generated
    (implicit) measure by table + column + aggregation

origin "user" = an explicit user edit (firm); anything else = an unmarked,
historical saved name (soft). One request per source object applies in every
view the object appears in; the allocator (names.py) decides per view.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from semabridge.target_backends.databricks.planner.graph import ModelGraph
from semabridge.target_backends.databricks.planner.model import (
    Blocker,
    NameRequestOutcome,
    PlannerSettings,
    SourceId,
)
from semabridge.target_backends.databricks.planner.names import NameRequest

_COLUMNS = ".columns."


def _unique_ci(name: str, candidates) -> Optional[str]:
    if name in candidates:
        return name
    hits = [c for c in candidates if str(c).lower() == name.lower()]
    return hits[0] if len(hits) == 1 else None


def _identity(path: str, graph: ModelGraph) -> Tuple[Optional[SourceId], str]:
    """(source identity, reason when there is none)."""
    from semabridge.target_backends.databricks.planner.measures import measure_identity

    if path.startswith("datasets.") and _COLUMNS in path:
        table_part, column = path[len("datasets."):].split(_COLUMNS, 1)
        table = _unique_ci(table_part, list(graph.tables))
        if table is None:
            return None, f"table {table_part} is not in this model"
        cols = {str(c.unique_name): c for c in graph.tables[table].columns.values()}
        col = _unique_ci(column, list(cols))
        if col is None:
            return None, f"column {table_part}[{column}] is not in this model"
        return SourceId("field", table, col), ""
    if path.startswith("metrics."):
        name = _unique_ci(path[len("metrics."):], list(graph.measures))
        if name is None:
            return None, f"measure {path[len('metrics.'):]} is not in this model"
        return measure_identity(graph.measures[name])[0], ""
    return None, "only field and measure names can be set on Databricks (view names are planned)"


def resolve(settings: PlannerSettings, graph: ModelGraph
            ) -> Tuple[Dict[SourceId, NameRequest], List[NameRequestOutcome]]:
    """({identity: request}, outcomes for requests that match no object)."""
    by_sid: Dict[SourceId, NameRequest] = {}
    unresolved: List[NameRequestOutcome] = []
    for path, name, origin in settings.requested_names or ():
        firm = str(origin or "").strip().lower() == "user"
        sid, why = _identity(str(path), graph)
        if sid is None:
            unresolved.append(NameRequestOutcome(str(path), str(name), "user" if firm else "unmarked",
                                                 "not_applicable", None, [], why))
            continue
        by_sid[sid] = NameRequest(str(path), str(name), firm)
    return by_sid, unresolved


def outcomes(requests: Dict[SourceId, NameRequest], unresolved: List[NameRequestOutcome],
             events: List[dict]) -> Tuple[List[NameRequestOutcome], List[Blocker]]:
    """One outcome per request (across all views) and a blocker per firm conflict."""
    by_sid: Dict[SourceId, List[dict]] = {}
    for ev in events:
        by_sid.setdefault(ev["item"].sid, []).append(ev)
    out: List[NameRequestOutcome] = []
    blockers: List[Blocker] = []
    for sid, req in requests.items():
        evs = by_sid.get(sid, [])
        origin = "user" if req.firm else "unmarked"
        if not evs:
            out.append(NameRequestOutcome(req.source_path, req.name, origin, "not_placed", sid, [],
                                          "not in any planned view (hidden, left out, or its view is not built)"))
            continue
        views = [{"view": ev["view"], "final": ev["final"], "status": ev["status"], "reason": ev.get("reason", "")}
                 for ev in sorted(evs, key=lambda e: e["view"])]
        statuses = {v["status"] for v in views}
        status = "conflict" if "conflict" in statuses else "changed" if "changed" in statuses else "kept"
        reason = "; ".join(f"{v['view']}: {v['reason']}" for v in views if v["reason"])
        out.append(NameRequestOutcome(req.source_path, req.name, origin, status, sid, views, reason))
        for ev in evs:
            if ev["status"] == "conflict":
                blockers.append(Blocker(
                    "name_override_conflict",
                    f"your name {req.name} for {req.source_path} cannot be used in {ev['view']}: {ev['reason']}",
                    view=ev["view"], source_path=req.source_path,
                    details={"requested": req.name, "used_instead": ev["final"], "holder": ev.get("holder", "")},
                    scope="view", views=[ev["view"]],
                ))
    out += unresolved
    out.sort(key=lambda o: o.source_path)
    return out, blockers
