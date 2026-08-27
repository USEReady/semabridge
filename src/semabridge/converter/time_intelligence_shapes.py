"""Discovery of canonical time-intelligence "shapes" used by a model's metrics.

Background (see the proj-test-1 MAX_DATE investigation): Snowflake's
semantic-view METRICS clause rejects a direct reference to the enriched
view's MAX_DATE anchor column, even though the column physically exists.
Ordinary per-row enriched-view columns work fine (confirmed live). The fix
is to precompute the YTD/MTD/QTD/SAMEPERIODLASTYEAR-family boundary check
as a boolean flag column on the enriched view itself (an ordinary column),
so metric expressions reference `<alias>.IS_YTD` etc. instead of doing
`CASE WHEN date >= DATE_TRUNC('YEAR', <alias>.MAX_DATE) ...` inline.

This module answers "which flag columns does this model actually need?" —
structurally, from each metric's own DAX, not from a fixed list of metric
names. It mirrors (and will be consumed by) the exact function dispatch
already implemented in dax_ast_parser.py's DaxSqlRenderer
(_render_period_to_date / _render_lag_period / _render_calculate's
lag-modifier branch) — see that file for the SQL each shape renders to
today via inline MAX_DATE arithmetic.

A "shape" is a tuple of canonical component names, ordered innermost to
outermost, e.g.:
  ("YTD",)               -- TOTALYTD
  ("SPLY_YEAR",)         -- SAMEPERIODLASTYEAR / PREVIOUSYEAR of a plain measure
  ("YTD", "SPLY_YEAR")   -- SAMEPERIODLASTYEAR wrapping a YTD-shaped measure
                            (DAX: CALCULATE([SomeYTDMeasure], SAMEPERIODLASTYEAR(...)))

Nesting is resolved structurally by following measure references (`[Name]`)
through the model's other metrics — including transparently through
CALCULATE(...) wrappers whose only filter modifiers are FILTER/ALL/
ALLEXCEPT (which change *what rows* are included, not *what date window*
the value represents) — so a metric like
`CALCULATE([TOTAL_UNITS_YTD], FILTER(ALL(Product[isVanArsdel]), ...))`
is correctly recognized as still being YTD-shaped for this purpose, even
though it has no time-intelligence function call of its own.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from semabridge.converter.dax_ast_parser import (
    DaxAstParser,
    DaxNode,
    FunctionCallNode,
    MeasureRefNode,
)

Shape = Tuple[str, ...]

# DAX function name -> canonical period-to-date component. These are the
# functions dax_ast_parser.DaxSqlRenderer._render_period_to_date handles.
PERIOD_TO_DATE_FUNCS: Dict[str, str] = {
    "TOTALYTD": "YTD",
    "TOTALMTD": "MTD",
    "TOTALQTD": "QTD",
}

# DAX function name -> canonical lag component. These are the functions
# dax_ast_parser.DaxSqlRenderer._render_lag_period / _render_calculate's
# lag-modifier branch handle. SAMEPERIODLASTYEAR and PREVIOUSYEAR both map
# to the same "shift back one year" component, matching the renderer's own
# `interval="year"` dispatch for both.
LAG_FUNCS: Dict[str, str] = {
    "SAMEPERIODLASTYEAR": "SPLY_YEAR",
    "PREVIOUSYEAR": "SPLY_YEAR",
    "PREVIOUSMONTH": "SPLY_MONTH",
    "PREVIOUSQUARTER": "SPLY_QUARTER",
}

# Calendar unit each component operates on — shared by the boolean-SQL
# builder below.
PERIOD_UNITS: Dict[str, str] = {"YTD": "YEAR", "MTD": "MONTH", "QTD": "QUARTER"}
LAG_UNITS: Dict[str, str] = {
    "SPLY_YEAR": "YEAR",
    "SPLY_MONTH": "MONTH",
    "SPLY_QUARTER": "QUARTER",
}


def flag_column_name(shape: Shape) -> str:
    """Canonical, deterministic flag-column name for a shape, e.g.
    ("YTD",) -> "IS_YTD", ("YTD", "SPLY_YEAR") -> "IS_YTD_SPLY_YEAR". Both
    _create_enriched_view (Step 2) and the renderer (Step 3) must derive
    the same name from the same shape tuple — this is the single source of
    truth for that naming convention."""
    return "IS_" + "_".join(shape)


# Real incident: dry-run's DDL-emission schema-validation step rejected an
# already-correctly-translated metric (SUM(CASE WHEN SALESFACT.IS_YTD ...))
# because IS_YTD only exists on the enriched view _create_enriched_view
# builds at real-deploy time -- dry-run's trial DDL-build pass has no live
# connection, so its dataset_col_lookup is built from the model's own
# declared (raw, pre-enrichment) columns only, and IS_YTD is correctly
# absent from those. The fix is NOT to pretend the column is confirmed --
# it's to recognize, generically, that THIS specific "unknown column" is
# exactly the flag column this codebase's own enrichment mechanism is
# known to produce for this metric's own resolved shape, and report that
# honestly instead of as a hard failure. See metrics_clause_builder.py's
# call site.
ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE = "enrichment_column_unverifiable"


def enrichment_flag_column_if_referenced(shape: Optional[Shape], sql_expr: Optional[str]) -> Optional[str]:
    """If `sql_expr` references (as a distinct identifier, case-
    insensitively) the canonical flag column for `shape`, return that
    column's canonical (uppercase) name; otherwise None.

    Deliberately takes an already-resolved `shape` rather than re-deriving
    it here — callers with many metrics to check (e.g.
    metrics_clause_builder.py's per-metric DDL-emission loop) should
    resolve every metric's shape ONCE via metrics_with_time_intelligence_
    shapes() and pass each one in, rather than re-running discovery per
    metric. A metric with no resolved shape at all (the vast majority)
    always returns None here, never a guess.
    """
    if not shape or not sql_expr:
        return None
    flag_name = flag_column_name(shape)
    if re.search(rf"\b{re.escape(flag_name)}\b", sql_expr, re.IGNORECASE):
        return flag_name
    return None


def _resolve_arg0_shape(
    arg0: Optional[DaxNode],
    dax_by_name: Dict[str, Optional[str]],
    memo: Dict[str, Optional[Shape]],
    visiting: Set[str],
) -> Optional[Shape]:
    """Resolve the shape (if any) of a CALCULATE/lag-function's first
    argument — either a direct nested time-intelligence call, a reference
    to another metric (recurse into it), or a plain aggregate/column (no
    shape)."""
    if arg0 is None:
        return None
    if isinstance(arg0, MeasureRefNode):
        return _resolve_own_shape(arg0.name, dax_by_name, memo, visiting)
    if isinstance(arg0, FunctionCallNode):
        # Rare double-wrapping, e.g. a bare TOTALYTD(...) passed straight as
        # CALCULATE's first argument. Reuses the same dispatch as top-level
        # resolution.
        return _resolve_node_shape(arg0, dax_by_name, memo, visiting)
    return None


def _resolve_node_shape(
    node: Optional[DaxNode],
    dax_by_name: Dict[str, Optional[str]],
    memo: Dict[str, Optional[Shape]],
    visiting: Set[str],
) -> Optional[Shape]:
    if node is None:
        return None

    if isinstance(node, MeasureRefNode):
        return _resolve_own_shape(node.name, dax_by_name, memo, visiting)

    from semabridge.converter.dax_ast_parser import BinaryOpNode
    if isinstance(node, BinaryOpNode):
        left_s = _resolve_node_shape(node.left, dax_by_name, memo, visiting)
        right_s = _resolve_node_shape(node.right, dax_by_name, memo, visiting)
        return left_s or right_s

    if not isinstance(node, FunctionCallNode):
        return None

    func = node.func.upper()

    if func in PERIOD_TO_DATE_FUNCS:
        return (PERIOD_TO_DATE_FUNCS[func],)

    if func in LAG_FUNCS:
        arg0 = node.args[0] if node.args else None
        inner = _resolve_arg0_shape(arg0, dax_by_name, memo, visiting)
        outer = LAG_FUNCS[func]
        return (inner + (outer,)) if inner else (outer,)

    if func == "CALCULATE":
        # Structurally precise rolling months detection on AST repr string
        full_node_str = str(node)
        if "MONTHINDEX" in full_node_str.upper():
            m_r = re.search(r"MONTHINDEX.*?op='-'.*?value=(\d+)", full_node_str, re.IGNORECASE | re.DOTALL)
            if m_r:
                n_months = int(m_r.group(1))
                return (f"R{n_months}M",)

        arg0 = node.args[0] if node.args else None
        lag_modifier: Optional[FunctionCallNode] = None
        for modifier in node.args[1:]:
            if isinstance(modifier, FunctionCallNode) and modifier.func.upper() in LAG_FUNCS:
                lag_modifier = modifier
                break

        if lag_modifier is not None:
            inner = _resolve_arg0_shape(arg0, dax_by_name, memo, visiting)
            outer = LAG_FUNCS[lag_modifier.func.upper()]
            return (inner + (outer,)) if inner else (outer,)

        return _resolve_arg0_shape(arg0, dax_by_name, memo, visiting)

    # General composite function calls (DIVIDE, CONCATENATE, INT, LEFT, IF, SWITCH, COALESCE, etc.)
    for arg in node.args:
        arg_s = _resolve_node_shape(arg, dax_by_name, memo, visiting)
        if arg_s:
            return arg_s

    return None


def _resolve_own_shape(
    metric_name: str,
    dax_by_name: Dict[str, Optional[str]],
    memo: Dict[str, Optional[Shape]],
    visiting: Set[str],
) -> Optional[Shape]:
    if metric_name in memo:
        return memo[metric_name]
    if metric_name in visiting:
        # Circular measure reference — fail closed rather than recurse
        # forever. Never expected in real models; a metric can't legally
        # depend on itself, but a malformed one shouldn't hang discovery.
        return None

    dax = dax_by_name.get(metric_name)
    if not dax or not str(dax).strip():
        memo[metric_name] = None
        return None

    visiting.add(metric_name)
    try:
        ast = DaxAstParser().parse(dax)
        shape = _resolve_node_shape(ast, dax_by_name, memo, visiting)
    except Exception:
        # Discovery must never be fatal to a deploy — treat any unexpected
        # parse/traversal error as "no shape for this metric".
        shape = None
    finally:
        visiting.discard(metric_name)

    memo[metric_name] = shape
    return shape


def metrics_with_time_intelligence_shapes(metrics: Iterable[Any]) -> Dict[str, Shape]:
    """Map of metric unique_name -> resolved Shape, for every metric that
    discovery finds to have (directly, or transitively through measure
    references) a time-intelligence shape of its own — the same
    traversal `discover_time_intelligence_shapes` uses, exposed per-metric
    instead of only as the aggregate `Set[Shape]`.

    `metrics` is any iterable of objects with `.unique_name` and
    `.expression` (DAX text) attributes — the same shape as SML/OSI metric
    objects already used throughout this package. A metric with no shape
    (the vast majority, for most models) is simply absent from the
    returned dict — never guessed, never defaulted.

    Callers that need to know WHICH metrics to re-render once an
    anchor_flag_map becomes available (predicted or real — see
    predict_anchor_flag_map / connectors/anchor_flag_rerender.py) use
    this; callers that only need to know which shapes exist AT ALL (e.g.
    _create_enriched_view, deciding which flag columns to project) use
    `discover_time_intelligence_shapes` below.
    """
    metrics = list(metrics)
    dax_by_name: Dict[str, Optional[str]] = {
        str(getattr(m, "unique_name", "") or ""): getattr(m, "expression", None)
        for m in metrics
    }

    memo: Dict[str, Optional[Shape]] = {}
    result: Dict[str, Shape] = {}
    for m in metrics:
        name = str(getattr(m, "unique_name", "") or "")
        if not name:
            continue
        shape = _resolve_own_shape(name, dax_by_name, memo, set())
        if shape:
            result[name] = shape
    return result


def discover_time_intelligence_shapes(metrics: Iterable[Any]) -> Set[Shape]:
    """Scan every metric's own DAX expression and return the deduplicated
    set of canonical time-intelligence shapes actually needed by this
    model — never a fixed/hardcoded list, and never guessed when none are
    present (a model with no time-intelligence metrics returns an empty
    set, not a default set).

    `metrics` is any iterable of objects with `.unique_name` and
    `.expression` (DAX text) attributes — the same shape as SML/OSI metric
    objects already used throughout this package.
    """
    return set(metrics_with_time_intelligence_shapes(metrics).values())


def predict_anchor_flag_map(
    metrics: Iterable[Any],
    eligible_fact_tables: Iterable[str],
) -> Dict[str, Dict[Shape, str]]:
    """Predict, WITHOUT a live connection, which fact tables will end up
    with which time-intelligence flag columns once _create_enriched_view
    (snowflake_emitter.py) actually runs for real.

    Reuses `discover_time_intelligence_shapes`/`flag_column_name` — the
    exact same functions _create_enriched_view itself calls
    (snowflake_emitter.py's enrichment loop) — so the predicted shape ->
    name mapping can never drift from what real enrichment will produce.
    Only WHETHER a given fact table gets an entry at all can differ from
    the real run, and that's entirely bounded by `eligible_fact_tables`,
    which the caller resolves using the same eligibility check
    _create_enriched_view uses (see
    SnowflakeEmitter._resolve_fact_enrichment_date_column /
    predict_anchor_flag_map on SnowflakeEmitter) — this function itself
    has no opinion on eligibility, only on naming.

    Mirrors _create_enriched_view's own population exactly: every eligible
    fact table gets the FULL set of shapes the model's metrics need (not
    scoped to metrics on that one dataset alone), keyed by casefolded
    fact-table name — never a flat map merged across every fact table
    (see _create_enriched_view's own comment on why: a fact table whose
    own anchor can't be established must get no entries at all, and a
    metric must never resolve a flag name that only exists on some OTHER
    table's enriched view).

    Never needs the actual anchor date value — metric SQL only ever
    references the flag column's NAME (`<alias>."IS_YTD"`), never a
    literal date; the value is only baked into the enriched view's own
    column definition, which this function never builds.
    """
    shapes = discover_time_intelligence_shapes(metrics)
    if not shapes:
        return {}
    return {
        str(fact_table).casefold(): {shape: flag_column_name(shape) for shape in shapes}
        for fact_table in eligible_fact_tables
        if fact_table
    }


def build_shape_boolean_sql(shape: Shape, date_col_sql: str, anchor_sql: str) -> str:
    """Boolean SQL expression for a canonical shape, built from the same
    anchor literal `_create_enriched_view` already fetches once per deploy
    (today spliced inline as MAX_DATE arithmetic; here precomputed as a
    named flag column instead — see dax_ast_parser.py's
    _render_period_to_date / _render_lag_period for the formulas this
    mirrors).

    Composition: every lag component in the shape shifts the anchor by one
    calendar unit (in tuple order); the (at most one) period-to-date
    component then bounds the date column to [start-of-unit, anchor] using
    that shifted anchor. A shape with lag components only (no period-to-date
    component) uses the last lag component's unit to bound the date column
    to that whole prior unit — this exactly reproduces
    _render_lag_period's existing year/month/quarter formulas (confirmed
    algebraically: a "prior full year, same day cutoff" bound and a
    "YTD-of-the-shifted-anchor" bound are the same BETWEEN range for the
    YEAR case; month/quarter use whole-period equality instead, matching
    today's code exactly).
    """
    if not shape:
        raise ValueError("build_shape_boolean_sql requires a non-empty shape")

    if len(shape) == 1 and shape[0].startswith("R") and shape[0].endswith("M") and shape[0][1:-1].isdigit():
        n_months = int(shape[0][1:-1])
        return f"{date_col_sql} > DATEADD(MONTH, -{n_months}, {anchor_sql}) AND {date_col_sql} <= {anchor_sql}"

    effective_anchor = anchor_sql
    lag_components = [c for c in shape if c in LAG_UNITS]
    period_components = [c for c in shape if c in PERIOD_UNITS]

    for component in lag_components:
        unit = LAG_UNITS[component]
        effective_anchor = f"DATEADD({unit}, -1, {effective_anchor})"

    if period_components:
        unit = PERIOD_UNITS[period_components[0]]
        return (
            f"{date_col_sql} >= DATE_TRUNC('{unit}', {effective_anchor}) "
            f"AND {date_col_sql} <= {effective_anchor}"
        )

    # Lag-only shape (no period-to-date component): whole-prior-unit bound.
    unit = LAG_UNITS[lag_components[-1]]
    if unit == "YEAR":
        return (
            f"{date_col_sql} >= DATE_TRUNC('YEAR', {effective_anchor}) "
            f"AND {date_col_sql} <= {effective_anchor}"
        )
    if unit == "MONTH":
        return f"YEAR({date_col_sql}) = YEAR({effective_anchor}) AND MONTH({date_col_sql}) = MONTH({effective_anchor})"
    return f"YEAR({date_col_sql}) = YEAR({effective_anchor}) AND QUARTER({date_col_sql}) = QUARTER({effective_anchor})"
