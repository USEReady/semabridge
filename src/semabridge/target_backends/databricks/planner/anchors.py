"""Stage 1: what happens to each measure, and where it goes.

Ordered guards first (first match wins), then placement:

  A  every aggregate counts the same table -> a view built on that table;
  B  aggregates count different tables, and one table R is (or is directly
     pointed to by) all of them -> R's view, the others joined one-to-many
     (only when the one_to_many_joins capability is proven, 3.5).

Everything else is left out with a reason. Measures are decided in
dependency order; a measure built on a left-out measure is left out too,
naming the root cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from semabridge.target_backends.databricks.planner.bindings import SKIP, Bindings
from semabridge.target_backends.databricks.planner.graph import Edge, ModelGraph
from semabridge.target_backends.databricks.planner.model import (
    Capability,
    LeftOut,
    MeasureSemantics,
    PlannerSettings,
)
from semabridge.target_backends.databricks.planner import sqltext
from semabridge.utils.naming import sanitize_column
from semabridge.target_backends.databricks.planner.semantics import CYCLE, SemanticsReader, count_grains, cross_check

CALC_FLAG = "calculate_filter_semantics_difference"
TIME_FLAG = "time_intelligence_anchored_to_current_date"

# Left-out reasons that make every measure built on it unusable too. Placement
# reasons are not inherited: a dependant inlines the SQL and is placed on its own.
INHERITED = {
    "conversion_failed", "unrecognised_pattern", "analysis_mismatch",
    "filter_removal_unsupported", "filter_semantics_differ", CYCLE, "depends_on",
}


@dataclass
class Decision:
    measure: str
    placement: Optional[str] = None          # A | B | None (left out)
    view_source: Optional[str] = None        # anchor (A) or root (B)
    semantics: Optional[MeasureSemantics] = None
    left_out: Optional[LeftOut] = None
    approximate: bool = False
    warnings: List[str] = field(default_factory=list)
    children: List[Tuple[str, Edge]] = field(default_factory=list)  # B: (child grain, its edge to the root)
    constant: bool = False


def converted(metric) -> bool:
    sql = getattr(metric, "sql_expression", None)
    return bool(sql) and getattr(metric, "sync_enabled", True) is not False and not getattr(metric, "is_placeholder", False)


def _flags(metric) -> Set[str]:
    return set(getattr(metric, "advisory_categories", None) or [])


def _order(graph: ModelGraph, reader: SemanticsReader) -> List[str]:
    """Measures in dependency order (references first); stable by model order."""
    out: List[str] = []
    seen: Set[str] = set()

    def visit(name: str, stack: Tuple[str, ...]) -> None:
        if name in seen or name in stack:
            return
        sem = reader.read(name)
        for r in sem.measure_refs:
            if r in graph.measures:
                visit(r, stack + (name,))
        seen.add(name)
        out.append(name)

    for name in graph.measures:
        visit(name, ())
    return out


def _cycles(graph: ModelGraph, reader: SemanticsReader) -> Dict[str, str]:
    """{measure: loop description} for every measure on a reference loop."""
    refs = {n: [r for r in reader.read(n).measure_refs if r in graph.measures] for n in graph.measures}
    on_loop: Dict[str, str] = {}
    for start in graph.measures:
        stack = [(start, (start,))]
        while stack:
            cur, path = stack.pop()
            for r in refs.get(cur, []):
                if r == start:
                    loop = " -> ".join(path + (start,))
                    for n in path:
                        on_loop.setdefault(n, loop)
                elif r not in path and len(path) < 50:
                    stack.append((r, path + (r,)))
    return on_loop


def decide_all(graph: ModelGraph, reader: SemanticsReader, bindings: Bindings,
               settings: PlannerSettings) -> Dict[str, Decision]:
    decisions: Dict[str, Decision] = {}
    loops = _cycles(graph, reader)
    for name in _order(graph, reader):
        if name in loops and converted(graph.measures[name]):
            decisions[name] = _out(name, CYCLE, loops[name], sem=reader.read(name))
            continue
        decisions[name] = _decide(name, graph, reader, bindings, settings, decisions)
    return decisions


def _out(name: str, reason: str, detail: str = "", root: Optional[str] = None,
         components: Optional[List[str]] = None, sem: Optional[MeasureSemantics] = None) -> Decision:
    return Decision(name, semantics=sem, left_out=LeftOut(name, reason, detail, root or None, components or []))


def _components(sem: MeasureSemantics) -> List[str]:
    return [f"{a.function}({a.grain})" + (f" via [{a.via_measure}]" if a.via_measure else "") for a in sem.aggregates]


def _decide(name: str, graph: ModelGraph, reader: SemanticsReader, bindings: Bindings,
            settings: PlannerSettings, done: Dict[str, Decision]) -> Decision:
    metric = graph.measures[name]
    excluded = {m.lower() for m in settings.excluded_measures}

    # 1. conversion failed
    if not converted(metric):
        reason = getattr(metric, "sync_failure_reason", None) or "the converter did not produce SQL"
        return _out(name, "conversion_failed", str(reason))
    if name.lower() in excluded:
        return _out(name, "user_excluded", "unticked by the user")

    sem = reader.read(name)
    # dependency left out for a reason that carries over
    for ref in sem.measure_refs:
        dep = done.get(ref)
        if dep is not None and dep.left_out is not None and dep.left_out.reason in INHERITED:
            root = dep.left_out.root_cause or f"{ref} ({dep.left_out.reason})"
            return _out(name, "depends_on", f"depends on [{ref}]", root=root, sem=sem)

    # 2. unrecognised pattern / mismatch / cycle
    if sem.issue is not None:
        return _out(name, sem.issue[0], sem.issue[1], sem=sem)
    mismatch = cross_check(sem, metric.sql_expression, graph)
    if mismatch is not None:
        return _out(name, mismatch[0], mismatch[1], sem=sem)

    # every SQL reference must resolve to exactly one table and one physical column
    bad = _reference_problem(metric.sql_expression, graph, bindings)
    if bad is not None:
        return _out(name, bad[0], bad[1], sem=sem)

    # 3. filter removal (ALL / REMOVEFILTERS / ALLEXCEPT): no Databricks equivalent
    if sem.filter_removal:
        return _out(name, "filter_removal_unsupported",
                    "removes report filters (ALL/REMOVEFILTERS); metric views have no equivalent", sem=sem)

    # 4. CALCULATE / time-intelligence approximation
    flags = _flags(metric)
    dep_approx = any(done[r].approximate for r in sem.measure_refs if r in done and done[r].left_out is None)
    approximate = bool(flags & {CALC_FLAG, TIME_FLAG}) or dep_approx
    if CALC_FLAG in flags and settings.calculate_mode == "strict":
        return _out(name, "filter_semantics_differ",
                    "CALCULATE filters may give a different result under slicers (strict mode)", sem=sem)
    warnings: List[str] = []
    if CALC_FLAG in flags:
        warnings.append("approximate: CALCULATE filters may give a different result under some slicers")
    if TIME_FLAG in flags:
        warnings.append("approximate: time intelligence is anchored to the current date")
    if dep_approx:
        warnings.append("approximate: built on an approximate measure")

    # skipped tables
    skipped = sorted(t for t in sem.tables if bindings.action(t) == SKIP)
    if skipped:
        return _out(name, "table_skipped", f"needs skipped table(s) {skipped}", sem=sem)

    grains = sem.grains
    # 8. table-independent constant -> home table's view
    if not grains:
        refs_constant = bool(sem.measure_refs) and all(
            r in done and done[r].constant and done[r].left_out is None for r in sem.measure_refs)
        if sem.constant is None and not refs_constant:
            return _out(name, "unrecognised_pattern", "the measure has no aggregate", sem=sem)
        home = graph.table(getattr(metric, "dataset", ""))
        if home is None:
            return _out(name, "unrecognised_pattern", "constant with no home table", sem=sem)
        if not settings.has(Capability.CONSTANT_COALESCE):
            if settings.calculate_mode == "strict":
                return _out(name, "constant_zero_row",
                            "MAX(c) returns NULL on empty data and COALESCE is not yet proven (strict mode)", sem=sem)
            warnings.append("approximate: returns NULL instead of the constant when no rows match")
            approximate = True
        return Decision(name, "A", home, sem, approximate=approximate, warnings=warnings, constant=True)

    # 6. one grain -> Placement A
    if len(grains) == 1:
        anchor = next(iter(grains))
        need = set(sem.condition_tables)
        for a in sem.aggregates:
            need |= set(a.value_tables) | set(a.condition_tables)
        need.discard(anchor)
        for t in sorted(need):
            if graph.unique_path(anchor, t) is None:
                paths = graph.paths_from(anchor).get(t, [])
                if len(paths) > 1:
                    return _out(name, "ambiguous_path", f"{t} is reachable from {anchor} in more than one way", sem=sem)
                return _out(name, "needs_composition",
                            f"{t} is not reachable from {anchor} through many-to-one relationships",
                            components=_components(sem), sem=sem)
        return Decision(name, "A", anchor, sem, approximate=approximate, warnings=warnings)

    # 7. several grains -> Placement B (if proven) or left out
    components = _components(sem)
    if not settings.has(Capability.ONE_TO_MANY_JOINS):
        return _out(name, "mixed_grain_needs_composition",
                    f"counts {sorted(grains)} at different grains; composed views are not enabled yet",
                    components=components, sem=sem)
    return _place_b(name, sem, graph, approximate, warnings, components, str(metric.sql_expression))


def _reference_problem(sql: str, graph: ModelGraph, bindings: Bindings) -> Optional[Tuple[str, str]]:
    shared = set(graph.ambiguous_aliases) | set(bindings.ambiguous_aliases)
    for alias, col in sqltext.refs(sql):
        if alias in shared:
            return "ambiguous_table_alias", f"tables {sorted(graph.by_alias.get(alias, []))} share the SQL alias {alias}"
        table = graph.table_for_alias(alias)
        if table is None:
            continue  # cross_check already covers unknown aliases
        status = bindings.reference_status(table, col)
        if status != "ok":
            b = bindings.binding(table)
            cands = ""
            if status == "ambiguous_binding" and b is not None:
                hit = next((v for k, v in (b.ambiguous_columns or {}).items()
                            if sanitize_column(k).upper() == col.upper()), None)
                cands = f"; candidates {hit}" if hit else ""
            return status, f"{table}[{col}]{cands}"
    return None


def _place_b(name: str, sem: MeasureSemantics, graph: ModelGraph, approximate: bool,
             warnings: List[str], components: List[str], sql: str) -> Decision:
    grains = sem.grains
    for a in sem.aggregates:
        if set(a.value_tables) != {a.grain} or (set(a.condition_tables) - {a.grain}):
            return _out(name, "composition_unsupported",
                        f"{a.function}({a.grain}) reads other tables inside one aggregate", components=components, sem=sem)
    if sem.condition_tables:
        return _out(name, "composition_unsupported",
                    f"measure-level filters on {sorted(sem.condition_tables)} in a composed measure", components=components, sem=sem)
    counted = count_grains(sem)
    if len(counted) > 1 and sqltext.count_star(sql):
        return _out(name, "count_attribution_ambiguous",
                    f"row counts of {sorted(counted)} can't be told apart in the SQL", components=components, sem=sem)

    candidates: List[str] = []
    for r in sorted(graph.tables):
        if all(g == r or len(graph.direct_edges(g, r)) == 1 for g in grains):
            candidates.append(r)
    in_grains = [r for r in candidates if r in grains]
    if len(in_grains) == 1:
        root = in_grains[0]
    elif len(candidates) == 1:
        root = candidates[0]
    elif not candidates:
        return _out(name, "needs_composition",
                    f"no table that {sorted(grains)} all point to directly (shared spine sources come later)",
                    components=components, sem=sem)
    else:
        return _out(name, "ambiguous_composition_root", f"candidate roots {candidates}",
                    components=components, sem=sem)
    children = [(g, graph.direct_edges(g, root)[0]) for g in sorted(grains) if g != root]
    warnings = list(warnings) + [
        f"composed: {', '.join(g for g, _ in children)} joined one-to-many to {root}; "
        f"group only by {root} attributes"
    ]
    return Decision(name, "B", root, sem, approximate=approximate, warnings=warnings, children=children)
