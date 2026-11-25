"""Stage 5: the measures of one view.

Each measure keeps the converter's SQL (dependencies are already inlined, so
it stands alone), with table aliases rewritten to the view's paths: the
source becomes ``source``, a joined table its join path. Text inside string
literals is never touched. In a composed (B) view, a row count of a
one-to-many table becomes NULLIF(COUNT(<join key>), 0): every joined row has
a non-null key, and DAX COUNTROWS of nothing is BLANK.

Status, verification and readiness are tracked separately and carried
through dependencies (worst wins); every warning is kept.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from semabridge.target_backends.databricks.planner import sqltext
from semabridge.target_backends.databricks.planner.anchors import Decision
from semabridge.target_backends.databricks.planner.bindings import Bindings
from semabridge.target_backends.databricks.planner.graph import ModelGraph
from semabridge.target_backends.databricks.planner.model import Capability, MeasurePlan, PlannerSettings, SourceId
from semabridge.target_backends.databricks.planner.names import label_metadata
from semabridge.utils.naming import sanitize_column

READINESS_ORDER = {"real": 0, "unbound": 1, "structure_only": 2}


class RewriteError(RuntimeError):
    pass


def measure_identity(metric) -> "Tuple[SourceId, str]":
    """Structured identity and naming-v2 base name of a measure. Implicit
    (generated) measures such as SUM_OF_SELLINGAREASIZE are identified by
    table + column + aggregation and named from the column: sum_of_selling_area_size."""
    from semabridge.target_rules.databricks.naming import normalize

    name = str(metric.unique_name)
    column = getattr(metric, "source_column", None)
    expr = str(getattr(metric, "expression", "") or "")
    if column and "_OF_" in name.upper():   # generated from a column's default aggregation (tmsl_to_osi)
        agg_raw = getattr(metric, "aggregation", None)
        agg = str(getattr(agg_raw, "value", agg_raw) or "").upper()
        word = name.split("_OF_", 1)[0].lower() if "_OF_" in name else (agg.lower() or "agg")
        sid = SourceId("implicit_measure", str(getattr(metric, "dataset", "")), str(column), agg or word.upper())
        return sid, normalize(f"{word} of {column}", "measure", sid.key())[0]
    sid = SourceId("measure", "", name)
    return sid, normalize(name, "measure", sid.key())[0]


def _rewrite(sql: str, alias_to_path: Dict[str, str]) -> str:
    def repl(m: "re.Match[str]") -> str:
        alias = m.group(1).upper()
        if alias not in alias_to_path:
            raise RewriteError(f"SQL alias {m.group(1)} has no path in this view")
        return f"{alias_to_path[alias]}.`{m.group(2)}`"

    return sqltext.sub_in_code(sqltext.REF, repl, sql)


def _calculated_columns_used(sql: str, graph: ModelGraph, bindings: Bindings) -> List[str]:
    alias_to_table = {graph.alias(t): t for t in graph.tables}
    out = []
    for is_lit, text in sqltext.segments(sql or ""):
        if is_lit:
            continue
        for m in sqltext.REF.finditer(text):
            table = alias_to_table.get(m.group(1).upper())
            if table is None:
                continue
            wanted = m.group(2).replace("``", "`").upper()
            for col in graph.tables[table].columns.values():
                name = str(col.unique_name)
                if getattr(col, "is_calculated", False) and (
                    sanitize_column(name).upper() == wanted or bindings.real_column(table, name).upper() == wanted
                ):
                    out.append(f"{table}[{name}]")
    return sorted(set(out))


def build_measures(source: str, decisions: List[Decision], where: Dict[str, str], graph: ModelGraph,
                   bindings: Bindings, settings: PlannerSettings, placed: Dict[str, MeasurePlan]
                   ) -> List[MeasurePlan]:
    """`placed` collects every measure built so far (any view), for status inheritance."""
    alias_to_path = {graph.alias(t): p for t, p in where.items()}
    plans: List[MeasurePlan] = []
    for d in decisions:
        metric = graph.measures[d.measure]
        sql = str(metric.sql_expression).strip()
        if d.constant and settings.has(Capability.CONSTANT_COALESCE):
            m = re.fullmatch(r"MAX\((-?[0-9.]+)\)", sql)
            if m:
                sql = f"COALESCE(MAX({m.group(1)}), {m.group(1)})"
        if d.placement == "B":
            sql = _rewrite_child_counts(sql, d, where, bindings)
        expr = _rewrite(sql, alias_to_path)

        sem = d.semantics
        tables = sorted(sem.tables) if sem is not None else []
        readiness = max((bindings.readiness(t) for t in tables), key=lambda r: READINESS_ORDER.get(r, 0), default="real")
        if d.constant:
            readiness = bindings.readiness(source)
        calc_cols = _calculated_columns_used(metric.sql_expression, graph, bindings)
        verification = "unverified" if calc_cols else "verified"
        warnings = list(d.warnings)
        if calc_cols:
            warnings.append(f"unverified: reads calculated column(s) matched by name only: {', '.join(calc_cols)}")
        depends = [r for r in (sem.measure_refs if sem else ()) if r in graph.measures]
        status = "approximate" if d.approximate else "converted"
        for r in depends:  # carry the worst state of every dependency
            dep = placed.get(r)
            if dep is None:
                continue
            if dep.semantic_status == "approximate":
                status = "approximate"
            if dep.source_verification == "unverified" and verification != "unverified":
                verification = "unverified"
                warnings.append(f"unverified: built on [{r}], which reads unverified columns")
            if READINESS_ORDER.get(dep.readiness, 0) > READINESS_ORDER.get(readiness, 0):
                readiness = dep.readiness
        if readiness == "structure_only":
            warnings.append("structure only: reads stub (empty) tables until real data arrives")
        comment = "; ".join(w for w in warnings if w.startswith(("approximate", "unverified"))) or None
        sid, base = measure_identity(metric)
        display, synonyms, label_notes = label_metadata(str(getattr(metric, "label", "") or d.measure),
                                                        list(getattr(metric, "synonyms", None) or []))
        plan = MeasurePlan(
            name=base,
            source_name=d.measure,
            expr=expr,
            display_name=display,
            placement=d.placement,
            grain=next(iter(sem.grains)) if sem is not None and len(sem.grains) == 1 else source,
            semantic_status=status,
            source_verification=verification,
            readiness=readiness,
            warnings=list(dict.fromkeys(warnings)),
            depends_on=depends,
            synonyms=synonyms,
            comment=comment,
            source_id=sid,
            source_format=getattr(metric, "format_string", None),
            notes=label_notes,
        )
        placed[d.measure] = plan
        plans.append(plan)
    return plans


def _rewrite_child_counts(sql: str, d: Decision, where: Dict[str, str], bindings: Bindings) -> str:
    """COUNT(*) of a one-to-many table -> NULLIF(COUNT(<its join key>), 0)."""
    from semabridge.target_backends.databricks.planner.semantics import count_grains

    counted = sorted(count_grains(d.semantics))
    if not counted or counted[0] == d.view_source or not sqltext.count_star(sql):
        return sql  # counts of the root: COUNT(*) counts root rows
    child = counted[0]
    edge = next(e for g, e in d.children if g == child)
    marker = bindings.real_column(child, edge.many_cols[0])
    replacement = f"NULLIF(COUNT({where[child]}.`{marker}`), 0)"
    return sqltext.sub_in_code(sqltext.COUNT_STAR, lambda m: replacement, sql)


def propagate(placed: Dict[str, MeasurePlan], order: List[str]) -> None:
    """Carry the worst status of every dependency, across views, in dependency order."""
    for name in order:
        plan = placed.get(name)
        if plan is None:
            continue
        for r in plan.depends_on:
            dep = placed.get(r)
            if dep is None:
                continue
            if dep.semantic_status == "approximate" and plan.semantic_status != "approximate":
                plan.semantic_status = "approximate"
                plan.warnings.append(f"approximate: built on [{r}], which is approximate")
            if dep.source_verification == "unverified" and plan.source_verification != "unverified":
                plan.source_verification = "unverified"
                plan.warnings.append(f"unverified: built on [{r}], which reads unverified columns")
            if READINESS_ORDER.get(dep.readiness, 0) > READINESS_ORDER.get(plan.readiness, 0):
                plan.readiness = dep.readiness
        plan.comment = "; ".join(w for w in plan.warnings if w.startswith(("approximate", "unverified"))) or None
