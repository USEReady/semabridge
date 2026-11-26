"""Runs a probe fixture through the PRODUCTION path, offline:

    TMSL -> TMSLToOSIConverter -> OSIToSMLConverter(Databricks rules)
         -> plan_tables (bound to the fixture's tables) -> measure column rewrite (as Step 6b)
         -> plan_metric_views (with the capability under test provisionally enabled)
         -> placement check -> writer with the probe-only inline-source substitution

The probe-only pieces are confined here: the capability activation is a
PlannerSettings built only by this module (customer plans get capabilities
from valid proof intersected with the admin allow-list, never from here), and
the inline sources come from ``ProbeSources`` with the writer's private key.
Plans built here are marked ``meta.probe = true``; their bundles are probe
bundles and never deployable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from semabridge.target_backends.databricks.probes.fixtures import Fixture, Table

PROBE_CATALOG_PLACEHOLDER = ("probe_catalog", "probe_schema")


class FixtureDefect(RuntimeError):
    """SemaBridge did not produce what the fixture expects (a SemaBridge defect,
    never a platform result)."""


@dataclass
class BuiltFixture:
    fixture: Fixture
    plan: Dict[str, Any]
    view_names: Dict[str, str]                     # planner view name -> probe object name
    measure_names: Dict[str, Tuple[str, str]]      # measure source name -> (probe view, planned name)
    field_names: Dict[Tuple[str, str, str], str]   # (probe view, table, column) -> planned field name
    rendered: List[Any] = field(default_factory=list)
    skipped_measures: Dict[str, str] = field(default_factory=dict)   # not emitted by the converter: why


def tmsl_of(fx: Fixture) -> Dict[str, Any]:
    tables = []
    for t in fx.tables:
        tables.append({
            "name": t.name,
            "columns": [{"name": c.name, "dataType": c.tmsl_type, "sourceColumn": c.name} for c in t.columns],
            "measures": [{"name": m.name, "expression": m.dax, **({"formatString": m.format_string} if m.format_string else {})}
                         for m in fx.measures if m.table == t.name],
        })
    rels = [{"name": f"r{i}", "fromTable": a, "fromColumn": b, "toTable": c, "toColumn": d}
            for i, (a, b, c, d) in enumerate(fx.relationships)]
    return {"model": {"name": f"probe_{fx.fid}", "tables": tables, "relationships": rels}}


def _uc_tables(fx: Fixture):
    from semabridge.target_backends.databricks.tables import UcTable

    cat, sch = PROBE_CATALOG_PLACEHOLDER
    return {(cat, sch): {t.name.upper(): UcTable(
        cat, sch, t.name, "VIEW",
        {t.real(c.name).upper(): (t.real(c.name), c.sql_type.lower()) for c in t.columns},
    ) for t in fx.tables}}


def _literal(value: Any, sql_type: str) -> str:
    if value is None:
        return f"CAST(NULL AS {sql_type})"
    if isinstance(value, str):
        return f"CAST('{value.replace(chr(39), chr(39) * 2)}' AS {sql_type})"
    return f"CAST({value} AS {sql_type})"


def inline_query(t: Table) -> str:
    """SELECT ... FROM VALUES ... with explicit types, the probe's stand-in for a table."""
    cols = ", ".join(f"`{t.real(c.name)}`" for c in t.columns)
    rows = ", ".join("(" + ", ".join(_literal(v, c.sql_type) for v, c in zip(row, t.columns)) + ")" for row in t.rows)
    return f"SELECT * FROM VALUES {rows} AS t({cols})"


def build(fx: Fixture, *, catalog: str, schema: str, token: str) -> BuiltFixture:
    """Fixture -> plan -> rendered probe views (offline). Raises FixtureDefect."""
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views
    from semabridge.target_backends.databricks.tables import plan_tables, rewrite_measure_columns
    from semabridge.target_backends.databricks.writer import _PROBE_KEY, ProbeSources, render_plan
    from semabridge.target_rules import DATABRICKS_RULES

    osi = TMSLToOSIConverter().to_osi({
        "tmsl": tmsl_of(fx), "workspace_id": "probe", "dataset_id": f"probe_{fx.fid}",
        "display_name": f"probe_{fx.fid}", "project_id": "probe", "field_aliases": [], "source_file": None,
        "infer_relationships": None,
    })
    sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi)
    cat, sch = PROBE_CATALOG_PLACEHOLDER
    # Explicit mapping: every fixture table binds to its own inline table, deterministically.
    tp = plan_tables(sml, _uc_tables(fx), source_schemas=[PROBE_CATALOG_PLACEHOLDER], on_missing_source="fail",
                     table_mapping={t.name: f"{cat}.{sch}.{t.name}" for t in fx.tables})
    rewrite = tp.rewrite_map()
    for metric in sml.metrics:
        metric.sql_expression = rewrite_measure_columns(metric.sql_expression, rewrite)

    # Provisional, probe-only capability activation (never reachable from customer plans).
    settings = PlannerSettings(security_roles=0, capabilities=frozenset(fx.capabilities),
                               output_catalog=catalog, output_schema=schema, existing_objects=frozenset())
    plan = plan_metric_views(sml, tp, settings).as_dict()
    plan["meta"] = {"revision": 0, "status": "valid", "probe": True, "fixture": fx.fid}

    skipped: Dict[str, str] = {}
    placed = {m["source_name"]: (v["name"], m) for v in plan["views"] for m in v["measures"]}
    left = {lo["measure"]: lo for lo in plan["left_out"]}
    problems = []
    for name, want in fx.placement.items():
        if name in left and left[name]["reason"] == "conversion_failed":
            skipped[name] = f"not emitted by the converter: {left[name]['detail']}"
            continue
        if name in left and name in fx.optional:
            skipped[name] = f"not placed by SemaBridge ({left[name]['reason']}): {left[name]['detail']}"
            continue
        got = placed.get(name, (None, {}))[1].get("placement") if name in placed else None
        if got != want:
            why = f" ({left[name]['reason']}: {left[name]['detail']})" if name in left else ""
            problems.append(f"{name}: expected placement {want}, planner gave {got}{why}")
    if problems:
        raise FixtureDefect(f"fixture {fx.fid}: " + "; ".join(problems))

    # Probe object names (the planner's view names prefixed for this run).
    view_names = {v["name"]: f"semabridge_probe_{token}_{fx.fid}_{v['name']}" for v in plan["views"]}
    for v in plan["views"]:
        v["name"] = view_names[v["name"]]
    for t in plan.get("tables") or []:
        t["views"] = [view_names.get(x, x) for x in t.get("views") or []]
    measure_names = {m["source_name"]: (v["name"], m["name"]) for v in plan["views"] for m in v["measures"]}
    field_names = {(v["name"], f["dataset"], f["column"]): f["name"] for v in plan["views"] for f in v["fields"]}

    sources = ProbeSources({t.name: inline_query(t) for t in fx.tables}, key=_PROBE_KEY)
    rendered = render_plan(plan, catalog=catalog, schema=schema, model_label=f"probe {token}", probe=sources)
    return BuiltFixture(fx, plan, view_names, measure_names, field_names, rendered, skipped)


def query_sql(built: BuiltFixture, query, catalog: str, schema: str) -> Tuple[str, List[str], List[str]]:
    """(SQL, group columns, measure columns) for one fixture query, by planned names."""
    from semabridge.target_backends.databricks.writer import quote_identifier, quote_three_part

    view, _ = built.measure_names[query.view_of]
    dims = []
    for table, column in query.group_by:
        name = built.field_names.get((view, table, column))
        if name is None:
            raise FixtureDefect(f"{query.qid}: {table}[{column}] is not a field of {view}")
        dims.append(name)
    measures = [m for m in query.measures if m not in built.skipped_measures]
    for m in measures:
        if built.measure_names.get(m, (None,))[0] != view:
            raise FixtureDefect(f"{query.qid}: measure {m} is not placed in {view}")
    select = [quote_identifier(d) for d in dims] + [
        f"MEASURE({quote_identifier(built.measure_names[m][1])}) AS {quote_identifier('m_' + str(i))}"
        for i, m in enumerate(measures)]
    sql = f"SELECT {', '.join(select)} FROM {quote_three_part(catalog, schema, view)}"
    if query.where is not None:
        (table, column), value = query.where
        name = built.field_names.get((view, table, column))
        if name is None:
            raise FixtureDefect(f"{query.qid}: filter field {table}[{column}] is not in {view}")
        literal = "'" + str(value).replace("'", "''") + "'" if isinstance(value, str) else str(value)
        sql += f" WHERE {quote_identifier(name)} = {literal}"
    if dims:
        sql += " GROUP BY " + ", ".join(quote_identifier(d) for d in dims)
    return sql, dims, measures
