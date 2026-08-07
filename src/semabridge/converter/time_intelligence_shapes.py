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
    if not isinstance(node, FunctionCallNode):
        # Arithmetic, IF/SWITCH, string literals, bare columns, etc. — none
        # of these represent a time-intelligence date window on their own.
        return None

    func = node.func.upper()

    if func in PERIOD_TO_DATE_FUNCS:
        # TOTALYTD-of-a-measure-that-itself-has-a-date-window is a known,
        # pre-existing renderer limitation (dax_ast_parser.py's own comment:
        # produces an always-empty CASE WHEN) — out of scope here. This
        # shape is reported as a single, non-nested component regardless of
        # what its own argument is.
        return (PERIOD_TO_DATE_FUNCS[func],)

    if func in LAG_FUNCS:
        # Bare 2-arg form: SAMEPERIODLASTYEAR(<agg-or-measure>, <date column>).
        arg0 = node.args[0] if node.args else None
        inner = _resolve_arg0_shape(arg0, dax_by_name, memo, visiting)
        outer = LAG_FUNCS[func]
        return (inner + (outer,)) if inner else (outer,)

    if func == "CALCULATE":
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

        # No lag modifier — e.g. FILTER(ALL(...))/ALLEXCEPT(...) only, or no
        # modifiers at all. Those change which rows are included, not what
        # date window the value represents, so transparently pass through
        # whatever shape (if any) the wrapped measure/expression has.
        return _resolve_arg0_shape(arg0, dax_by_name, memo, visiting)

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
    metrics = list(metrics)
    dax_by_name: Dict[str, Optional[str]] = {
        str(getattr(m, "unique_name", "") or ""): getattr(m, "expression", None)
        for m in metrics
    }

    memo: Dict[str, Optional[Shape]] = {}
    shapes: Set[Shape] = set()
    for m in metrics:
        name = str(getattr(m, "unique_name", "") or "")
        if not name:
            continue
        shape = _resolve_own_shape(name, dax_by_name, memo, set())
        if shape:
            shapes.add(shape)
    return shapes


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
