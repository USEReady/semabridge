"""Understand measures: platform-neutral facts about every measure (step 3 / 6A).

For each Power BI measure this works out WHAT it is -- its DAX, its shape
(empty, constant, text result, context-transition problem), the columns it
uses that the model lacks, the measures and tables it uses, its complexity --
and, for the whole model, how the measures depend on each other (build order,
cycles, dependents).

It only reads DAX and the model's structure. It never produces SQL, never
quotes or renames anything, never reads a platform's rules and never decides
keep/decline: those are the target platform's job (target_rules, renderer,
planner). Snowflake (osi_to_sml.py) and Databricks (the planner) read the
same facts.

Every fact comes from the functions the converter already used
(analyze_complexity, extract_measure_references_from_ast, the DAX shape
checks in dax_ast_parser), so the facts cannot drift from what the Snowflake
path did before this module existed.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from semabridge.converter.dax_ast_parser import (
    ColumnRefNode,
    DaxAstParser,
    DaxNode,
    FunctionCallNode,
    BinaryOpNode,
    IdentifierNode,
    MeasureRefNode,
    UnaryOpNode,
    VarNode,
    _is_text_producing_node,
    dax_context_transition_failure_reason,
    dax_has_zero_data_dependencies,
    dax_root_is_string_producing,
    extract_measure_references_from_ast,
    unwrap_var_body,
)

# 'Table'[Column] or Table[Column] -- a column reference qualified by a table.
_QUALIFIED_REF_RE = re.compile(r"(?:'((?:[^']|'')+)'|\b([A-Za-z_][A-Za-z0-9_]*))\s*\[([^\]]+)\]")

# Implicit (column summarisation) measures: aggregation -> the DAX function
# that expresses it.
_IMPLICIT_DAX_FUNCTION = {
    "SUM": "SUM",
    "AVG": "AVERAGE",
    "MIN": "MIN",
    "MAX": "MAX",
    "COUNT": "COUNT",
    "COUNT_DISTINCT": "DISTINCTCOUNT",
}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasureAnalysis:
    """Facts about one measure. Read-only; holds no SQL and no decision."""

    name: str
    dataset: str
    # The measure's DAX: as written, or built from its column summarisation
    # (see `implicit`). Empty when the measure has none.
    dax: str
    # DAX was built from source_column + aggregation (no DAX in the source).
    implicit: bool
    # The source already carried SQL and no separate DAX (Snowflake source).
    # When True the DAX shape facts below are not evaluated.
    sql_sourced: bool
    # 'Table'[Column] references whose table is in the model but has no such
    # column: the measure is broken in Power BI too. Checked on the DAX as
    # written in the source.
    missing_column_refs: Tuple[str, ...]
    is_empty: bool
    is_constant: bool
    # The measure's result is text: a string function or literal at the root
    # (root_is_string_function) OR a root `&` concatenation, e.g.
    # "Sales: " & FORMAT([Total Sales], "0"). The complete, neutral fact.
    is_text_result: bool
    # Exactly dax_root_is_string_producing: a string FUNCTION or literal at
    # the root, not `&`. Kept separate because the Snowflake path has always
    # used this narrower check (a root `&` is declined later, by the
    # renderer); new code should read is_text_result.
    root_is_string_function: bool
    context_transition_reason: Optional[str]
    # Every [Name] / column-named-like-a-measure the DAX refers to, as written
    # (extract_measure_references_from_ast with the model's measure names).
    # May include names that are not measures, e.g. an unqualified [Column].
    referenced_names: Tuple[str, ...]
    # The subset of referenced_names that are measures of this model, by
    # their unique name (matched case-insensitively, original names too),
    # sorted by name.
    references: Tuple[str, ...]
    # Model tables the DAX reads directly (column refs, table arguments,
    # unqualified [Column] of the home table), in source order of the model.
    tables_used: Tuple[str, ...]
    # Exactly what DAXTranslator.analyze_complexity returns for this DAX
    # (tier, requires_time_intel, group_by_dimensions, depends_on_measures,
    # sync_enabled, failure_reason). Its sync_enabled/failure_reason are
    # hints, not decisions. None when not evaluated (empty, SQL-sourced or
    # broken measure).
    complexity: Optional[Mapping[str, Any]] = field(default=None, compare=False)

    def complexity_copy(self) -> Optional[Dict[str, Any]]:
        """A private deep copy of `complexity`, safe to hand to a model."""
        return copy.deepcopy(dict(self.complexity)) if self.complexity is not None else None


@dataclass(frozen=True)
class MeasureGraph:
    """How the model's measures depend on each other."""

    # Every measure once, dependencies before the measures that use them;
    # ties keep the model's source order. Members of a cycle come after
    # everything they do not depend on, in source order.
    order: Tuple[str, ...]
    # Each dependency loop once, as the names around the loop with the first
    # repeated at the end, e.g. ("A", "B", "A"); a self-reference is ("A", "A").
    cycles: Tuple[Tuple[str, ...], ...]
    # measure -> measures that reference it directly, in source order.
    dependents: Mapping[str, Tuple[str, ...]]
    # measure -> model tables it reads directly OR through the measures it
    # references (transitively), in source order of the model.
    all_tables_used: Mapping[str, Tuple[str, ...]]

    def in_cycle(self, name: str) -> bool:
        return any(name in cycle for cycle in self.cycles)


@dataclass(frozen=True)
class ModelMeasureAnalysis:
    """All facts for one model: one MeasureAnalysis per measure + the graph."""

    measures: Mapping[str, MeasureAnalysis]
    graph: MeasureGraph
    # One analysis per entry of model.metrics, same order and length (a
    # repeated name gets its own entry), for callers that walk the metrics
    # list itself.
    per_metric: Tuple[MeasureAnalysis, ...] = ()

    def get(self, name: str) -> Optional[MeasureAnalysis]:
        return self.measures.get(name)


# ---------------------------------------------------------------------------
# Building blocks (also used by osi_to_sml.py for its column check)
# ---------------------------------------------------------------------------


def model_column_index(model: Any) -> Dict[str, Set[str]]:
    """Table name (casefolded) -> its column names (casefolded), as modelled in the source."""
    index: Dict[str, Set[str]] = {}
    for ds in getattr(model, "datasets", None) or []:
        cols = {str(c.unique_name).casefold() for c in (getattr(ds, "columns", None) or [])}
        for name in (ds.unique_name, getattr(ds, "name", None), getattr(ds, "label", None)):
            if name:
                index.setdefault(str(name).casefold(), set()).update(cols)
    return index


def missing_model_column_refs(
    expression: Optional[str],
    model_columns: Dict[str, Set[str]],
    measure_names: Iterable[str],
) -> List[str]:
    """Qualified column references whose table is in the model but has no such column.

    Only references to a table the model actually has are checked, so DAX
    variables and virtual tables (ADDCOLUMNS/SUMMARIZE results) are never
    flagged. Such a measure is broken in Power BI itself.
    """
    if not expression or not model_columns:
        return []
    text = re.sub(r'"(?:[^"]|"")*"', '""', str(expression))  # ignore string literals
    text = re.sub(r"//[^\n]*|--[^\n]*|/\*.*?\*/", " ", text, flags=re.S)  # and comments
    measures = {str(m).casefold() for m in measure_names or ()}
    missing: List[str] = []
    for quoted, bare, col in _QUALIFIED_REF_RE.findall(text):
        table = (quoted.replace("''", "'") if quoted else bare).strip()
        cols = model_columns.get(table.casefold())
        if cols is None:
            continue
        col_key = col.strip().casefold()
        if col_key in cols or col_key in measures:
            continue
        ref = f"{table}[{col.strip()}]"
        if ref not in missing:
            missing.append(ref)
    return missing


def is_sql_sourced(measure: Any) -> bool:
    """The source already carried SQL and no separate DAX (Snowflake source).

    The OSI measure validator copies sql_expression into expression when no
    DAX was set, so "no separate DAX" means the two are equal (or DAX is absent).
    """
    expression = getattr(measure, "expression", None)
    sql = getattr(measure, "sql_expression", None)
    has_separate_dax = bool(expression and sql and expression != sql)
    return bool(sql) and not has_separate_dax


def implicit_dax(measure: Any) -> Optional[str]:
    """DAX for an implicit (column summarisation) measure: source column +
    aggregation and no expression, e.g. SUM('Sales'[Amount]). None otherwise."""
    expression = getattr(measure, "expression", None) or ""
    source_column = getattr(measure, "source_column", None)
    if str(expression).strip() or not source_column:
        return None
    aggregation = getattr(measure, "aggregation", None)
    agg_name = str(getattr(aggregation, "name", aggregation) or "").upper()
    dax_fn = _IMPLICIT_DAX_FUNCTION.get(agg_name)
    if not dax_fn:
        return None
    table = str(getattr(measure, "dataset", "") or "").replace("'", "''")
    column = str(source_column).replace("]", "]]")
    return f"{dax_fn}('{table}'[{column}])"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze_measures(model: Any, translator: Any = None) -> ModelMeasureAnalysis:
    """Facts for every measure of an OSI (or SML-shaped) model, plus the graph.

    `model` needs `.metrics` (unique_name, dataset, expression, and optionally
    sql_expression, source_column, aggregation, original_name, name) and
    `.datasets` (unique_name and columns). `translator` is a DAXTranslator,
    used only for analyze_complexity; one is created when not given.
    """
    if translator is None:
        from semabridge.converter.dax_translator import DAXTranslator

        translator = DAXTranslator()

    metrics = list(getattr(model, "metrics", None) or [])
    known_measure_names = {m.unique_name for m in metrics}
    model_columns = model_column_index(model)
    tables = _model_tables(model)
    measure_lookup = _measure_lookup(metrics)

    measures: Dict[str, MeasureAnalysis] = {}
    per_metric: List[MeasureAnalysis] = []
    for measure in metrics:
        analysis = _analyze_one(
            measure,
            translator=translator,
            known_measure_names=known_measure_names,
            model_columns=model_columns,
            tables=tables,
            measure_lookup=measure_lookup,
        )
        per_metric.append(analysis)
        measures.setdefault(analysis.name, analysis)

    graph = _build_graph([m.unique_name for m in metrics], measures, tables)
    return ModelMeasureAnalysis(measures=measures, graph=graph, per_metric=tuple(per_metric))


def analyze_measure(
    measure: Any,
    known_measure_names: Iterable[str],
    model: Any = None,
    translator: Any = None,
) -> MeasureAnalysis:
    """Facts for one measure (no graph). `model`, when given, supplies the
    tables and columns for the missing-column check and tables_used."""
    if translator is None:
        from semabridge.converter.dax_translator import DAXTranslator

        translator = DAXTranslator()
    known = set(known_measure_names or ())
    return _analyze_one(
        measure,
        translator=translator,
        known_measure_names=known,
        model_columns=model_column_index(model) if model is not None else {},
        tables=_model_tables(model) if model is not None else {},
        measure_lookup={str(n).casefold(): n for n in known},
    )


def _analyze_one(
    measure: Any,
    *,
    translator: Any,
    known_measure_names: Set[str],
    model_columns: Dict[str, Set[str]],
    tables: Dict[str, str],
    measure_lookup: Dict[str, str],
) -> MeasureAnalysis:
    name = measure.unique_name
    dataset = getattr(measure, "dataset", "") or ""
    written = getattr(measure, "expression", None) or ""

    missing = tuple(missing_model_column_refs(written, model_columns, known_measure_names))
    sql_sourced = is_sql_sourced(measure)

    built = None if sql_sourced else implicit_dax(measure)
    dax = built if built is not None else written
    is_empty = not dax or not str(dax).strip()

    is_constant = False
    is_text_result = False
    root_is_string_function = False
    context_reason: Optional[str] = None
    complexity: Optional[Dict[str, Any]] = None
    referenced: Tuple[str, ...] = ()
    references: Tuple[str, ...] = ()
    tables_used: Tuple[str, ...] = ()

    if not sql_sourced and not is_empty:
        referenced = tuple(sorted(extract_measure_references_from_ast(dax, known_measure_names)))
        references = _resolve_references(referenced, measure_lookup, name)
        tables_used = _tables_used(dax, dataset, tables, model_columns, set(measure_lookup))
        if not missing:
            is_constant = bool(dax_has_zero_data_dependencies(dax))
            root_is_string_function = bool(dax_root_is_string_producing(dax))
            root = unwrap_var_body(DaxAstParser().parse(dax))
            is_text_result = root_is_string_function or (
                root is not None and bool(_is_text_producing_node(root))
            )
            context_reason = dax_context_transition_failure_reason(dax)
            metric_ident = getattr(measure, "name", None) or name
            complexity = translator.analyze_complexity(dax, metric_name=metric_ident)

    return MeasureAnalysis(
        name=name,
        dataset=dataset,
        dax=dax,
        implicit=built is not None,
        sql_sourced=sql_sourced,
        missing_column_refs=missing,
        is_empty=is_empty,
        is_constant=is_constant,
        is_text_result=is_text_result,
        root_is_string_function=root_is_string_function,
        context_transition_reason=context_reason,
        referenced_names=referenced,
        references=references,
        tables_used=tables_used,
        complexity=complexity,
    )


def _model_tables(model: Any) -> Dict[str, str]:
    """casefolded table name/label -> the table's unique name."""
    out: Dict[str, str] = {}
    for ds in getattr(model, "datasets", None) or []:
        for alias in (ds.unique_name, getattr(ds, "name", None), getattr(ds, "label", None)):
            if alias:
                out.setdefault(str(alias).casefold(), ds.unique_name)
    return out


def _measure_lookup(metrics: Sequence[Any]) -> Dict[str, str]:
    """casefolded measure name (unique or original) -> unique name."""
    out: Dict[str, str] = {}
    for m in metrics:
        out.setdefault(str(m.unique_name).casefold(), m.unique_name)
    for m in metrics:
        original = getattr(m, "original_name", None)
        if original:
            out.setdefault(str(original).casefold(), m.unique_name)
    return out


def _resolve_references(
    referenced: Iterable[str], measure_lookup: Dict[str, str], own_name: str
) -> Tuple[str, ...]:
    resolved: List[str] = []
    for ref in referenced:
        target = measure_lookup.get(str(ref).strip().casefold())
        if target and target not in resolved:
            resolved.append(target)
    return tuple(sorted(resolved))


def _clean_table(name: str) -> str:
    name = str(name or "").strip()
    if len(name) >= 2 and name[0] == name[-1] == "'":
        name = name[1:-1].replace("''", "'")
    return name


def _tables_used(
    dax: str,
    home: str,
    tables: Dict[str, str],
    model_columns: Dict[str, Set[str]],
    measure_names_cf: Set[str],
) -> Tuple[str, ...]:
    tree = DaxAstParser().parse(dax)
    if tree is None:
        return ()
    found: List[str] = []
    home_cols = model_columns.get(str(home).casefold(), set())

    def add(table_name: str) -> None:
        canonical = tables.get(_clean_table(table_name).casefold())
        if canonical and canonical not in found:
            found.append(canonical)

    stack: List[Optional[DaxNode]] = [tree]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        if isinstance(node, ColumnRefNode):
            if node.table:
                add(node.table)
            elif home and str(node.column or "").strip().casefold() in home_cols:
                add(home)
        elif isinstance(node, IdentifierNode):
            add(node.name)
        elif isinstance(node, MeasureRefNode):
            # [Name] that is not a measure but a column of the home table.
            ref = str(node.name or "").strip().casefold()
            if ref not in measure_names_cf and ref in home_cols and home:
                add(home)
        elif isinstance(node, FunctionCallNode):
            stack.extend(reversed(node.args))
        elif isinstance(node, BinaryOpNode):
            stack.extend([node.right, node.left])
        elif isinstance(node, UnaryOpNode):
            stack.append(node.operand)
        elif isinstance(node, VarNode):
            stack.append(node.body)
            stack.extend(reversed([expr for _, expr in node.variables]))
    order = {canonical: i for i, canonical in enumerate(dict.fromkeys(tables.values()))}
    return tuple(sorted(found, key=lambda t: order.get(t, len(order))))


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _build_graph(
    names: Sequence[str],
    measures: Dict[str, MeasureAnalysis],
    tables: Dict[str, str],
) -> MeasureGraph:
    ordered = list(dict.fromkeys(names))
    position = {n: i for i, n in enumerate(ordered)}
    edges: Dict[str, List[str]] = {
        n: sorted((r for r in measures[n].references if r in position), key=position.__getitem__)
        for n in ordered
    }

    components = _strongly_connected(ordered, edges, position)
    component_of = {n: idx for idx, comp in enumerate(components) for n in comp}

    # Cycles: components with more than one member, or a self-reference.
    cycles: List[Tuple[str, ...]] = []
    for comp in components:
        if len(comp) > 1:
            cycles.append(tuple(comp) + (comp[0],))
        elif comp[0] in edges[comp[0]]:
            cycles.append((comp[0], comp[0]))
    cycles.sort(key=lambda c: position[c[0]])

    # Order: dependencies first over the component graph; ties by source order.
    comp_deps: Dict[int, Set[int]] = {i: set() for i in range(len(components))}
    for n in ordered:
        for d in edges[n]:
            if component_of[d] != component_of[n]:
                comp_deps[component_of[n]].add(component_of[d])
    first_pos = {i: min(position[n] for n in comp) for i, comp in enumerate(components)}
    remaining = {i: set(deps) for i, deps in comp_deps.items()}
    done: List[int] = []
    while remaining:
        ready = sorted((i for i, deps in remaining.items() if not deps), key=first_pos.__getitem__)
        nxt = ready[0]
        done.append(nxt)
        del remaining[nxt]
        for deps in remaining.values():
            deps.discard(nxt)
    order: List[str] = [n for i in done for n in components[i]]

    dependents: Dict[str, List[str]] = {n: [] for n in ordered}
    for n in ordered:
        for d in edges[n]:
            if n not in dependents[d]:
                dependents[d].append(n)

    table_order = {t: i for i, t in enumerate(dict.fromkeys(tables.values()))}
    all_tables: Dict[str, Tuple[str, ...]] = {}
    for i in done:
        comp = components[i]
        acc: Set[str] = set()
        for n in comp:
            acc.update(measures[n].tables_used)
            for d in edges[n]:
                if component_of[d] != i:
                    acc.update(all_tables[d])
        value = tuple(sorted(acc, key=lambda t: table_order.get(t, len(table_order))))
        for n in comp:
            all_tables[n] = value

    return MeasureGraph(
        order=tuple(order),
        cycles=tuple(cycles),
        dependents={n: tuple(v) for n, v in dependents.items()},
        all_tables_used=all_tables,
    )


def _strongly_connected(
    ordered: Sequence[str], edges: Dict[str, List[str]], position: Dict[str, int]
) -> List[List[str]]:
    """Tarjan's algorithm, iterative. Each component's members in source order."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    components: List[List[str]] = []
    counter = 0

    for root in ordered:
        if root in index:
            continue
        work: List[Tuple[str, int]] = [(root, 0)]
        while work:
            node, child_i = work[-1]
            if child_i == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = edges[node]
            if child_i < len(children):
                work[-1] = (node, child_i + 1)
                child = children[child_i]
                if child not in index:
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp: List[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    comp.append(member)
                    if member == node:
                        break
                components.append(sorted(comp, key=position.__getitem__))
    return components
