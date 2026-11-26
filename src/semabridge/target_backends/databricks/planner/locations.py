"""Where each table a planned view reads lives (writer input, 3.7).

* bound  -- a real Unity Catalog table (from 3.4's table matching)
* proposed_not_created -- no table exists; a PROPOSED location in the project's
  output catalog/schema, named by the Databricks naming policy and allocated in
  that schema's relation namespace (the planned view names and every object
  already there are taken). It is never a verified binding and nothing implies
  it exists: a table-scoped blocker says publishing needs it bound or created.
* unchecked -- no Databricks connection: the table check never ran; proposed
  as above, and a model-wide blocker says the tables were not checked.

Blockers carry their scope: ``model`` (whole plan), ``view`` (one view) or
``table`` (with the views that depend on it).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from semabridge.target_backends.databricks.planner.bindings import BIND, SKIP, STUB, Bindings
from semabridge.target_backends.databricks.planner.model import Blocker, PlannerResult, PlannerSettings, SourceId

BOUND = "bound"
PROPOSED = "proposed_not_created"
UNCHECKED = "unchecked"


def _dependencies(result: PlannerResult) -> Dict[str, List[str]]:
    deps: Dict[str, List[str]] = {}
    for v in result.views:
        for ds in [v.source_dataset] + [j.dataset for j in v.joins]:
            deps.setdefault(ds, [])
            if v.name not in deps[ds]:
                deps[ds].append(v.name)
    return {ds: sorted(vs) for ds, vs in deps.items()}


def _split(full_name: str) -> Tuple[str, str, str]:
    parts = str(full_name).split(".")
    if len(parts) != 3:
        raise ValueError(f"not a three-part table name: {full_name!r}")
    return parts[0], parts[1], parts[2]


def _bound_columns(bindings: Bindings, ds: str) -> List[Dict[str, str]]:
    """The real columns the model uses from a bound table, with the types seen at
    plan time (3.8 re-checks them before deploying)."""
    b = bindings.binding(ds)
    table = getattr(b, "table", None)
    if table is None:
        return []
    out: Dict[str, Dict[str, str]] = {}
    for real in (getattr(b, "column_map", {}) or {}).values():
        hit = table.columns.get(str(real).upper())
        if hit:
            out[hit[0].lower()] = {"name": hit[0], "type": hit[1]}
    return [out[k] for k in sorted(out)]


def _stub_columns(bindings: Bindings, ds: str) -> List[Dict[str, str]]:
    """The approved columns of a proposed (empty) table: name and Spark type (3.4)."""
    b = bindings.binding(ds)
    return [{"name": n, "type": t} for n, t in (getattr(b, "stub_columns", None) or [])]


def plan_locations(result: PlannerResult, bindings: Bindings, settings: PlannerSettings
                   ) -> Tuple[List[Dict], List[Blocker]]:
    """(one entry per table the views read, table/model-scoped blockers)."""
    from semabridge.target_backends.databricks.planner.names import NAMING, Allocator, _Item

    deps = _dependencies(result)
    catalog = (settings.output_catalog or "").strip() or None
    schema = (settings.output_schema or "").strip() or None
    existing = settings.existing_objects                         # None: never listed (no connection)
    own = {str(n).lower() for n in (settings.reserved_table_names or {}).values()}
    taken = [v.name for v in result.views] + sorted(n for n in (existing or ()) if n.lower() not in own)
    alloc = Allocator(taken)
    reserved = {SourceId("table", ds, ds): name for ds, name in (settings.reserved_table_names or {}).items()}

    entries: Dict[str, Dict] = {}
    items: List[_Item] = []
    for ds in sorted(deps):
        action = bindings.action(ds)
        if action == SKIP:
            continue
        if action == BIND and bindings.uc_table(ds):
            c, s, n = _split(bindings.uc_table(ds))
            entries[ds] = {"dataset": ds, "status": BOUND, "catalog": c, "schema": s, "name": n,
                           "name_checked": True, "views": deps[ds], "columns": _bound_columns(bindings, ds),
                           "uc_object_id": getattr(getattr(bindings.binding(ds), "table", None), "table_id", None)}
            continue
        status = PROPOSED if action == STUB else UNCHECKED
        base = NAMING.normalize(ds, "table", ("table", ds))[0]
        sid = SourceId("table", ds, ds)
        items.append(_Item(None, sid, base, ds, "table", (lambda n, b=base: f"{b}_{n + 1}"), (0, ds)))
        entries[ds] = {"dataset": ds, "status": status, "catalog": catalog, "schema": schema, "name": None,
                       "name_checked": existing is not None, "views": deps[ds], "columns": _stub_columns(bindings, ds)}
    alloc.allocate(items, reserved)
    for it in items:
        entries[it.sid.table]["name"] = it.final

    tables = [entries[ds] for ds in sorted(entries)]
    blockers: List[Blocker] = []
    if not catalog or not schema:
        if any(t["status"] != BOUND for t in tables):
            blockers.append(Blocker(
                "output_location_missing",
                "the project has no output catalog/schema configured, so missing tables have no proposed location "
                "and the view definitions cannot be written",
                scope="model"))
    unchecked = [t["dataset"] for t in tables if t["status"] == UNCHECKED]
    if unchecked:
        blockers.append(Blocker(
            "tables_not_checked",
            f"no Databricks connection: {len(unchecked)} table(s) were not checked against Unity Catalog "
            f"({', '.join(unchecked)}); run the dry run with a Databricks connection before publishing",
            scope="model", details={"tables": unchecked}))
    for t in tables:
        if t["status"] != PROPOSED:
            continue
        where = f"{t['catalog']}.{t['schema']}.{t['name']}" if t["catalog"] and t["schema"] else "(no output location)"
        blockers.append(Blocker(
            "source_table_missing",
            f"table {t['dataset']} does not exist in Unity Catalog (proposed location {where}, not created); "
            "publishing needs it bound to a real table or created",
            scope="table", table=t["dataset"], views=list(t["views"]),
            details={"proposed_location": where if t["catalog"] and t["schema"] else None,
                     "name_checked_against_existing_objects": t["name_checked"]}))
    return tables, blockers
