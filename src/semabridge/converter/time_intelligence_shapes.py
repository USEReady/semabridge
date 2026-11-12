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
    BinaryOpNode,
    DaxAstParser,
    DaxNode,
    FunctionCallNode,
    LiteralNode,
    MeasureRefNode,
    UnaryOpNode,
    VarNode,
)

Shape = Tuple[str, ...]

# DAX function name -> canonical period-to-date component. These are the
# functions dax_ast_parser.DaxSqlRenderer._render_period_to_date handles.
PERIOD_TO_DATE_FUNCS: Dict[str, str] = {
    "TOTALYTD": "YTD",
    "TOTALMTD": "MTD",
    "TOTALQTD": "QTD",
    "DATESYTD": "YTD",
    "DATESMTD": "MTD",
    "DATESQTD": "QTD",
}

# DAX function name -> canonical lag component. These are the functions
# dax_ast_parser.DaxSqlRenderer._render_lag_period / _render_calculate's
# lag-modifier branch handle. SAMEPERIODLASTYEAR and PREVIOUSYEAR both map
# to the same "shift back one year" component, matching the renderer's own
# `interval="year"` dispatch for both.
LAG_FUNCS: Dict[str, str] = {
    "SAMEPERIODLASTYEAR": "SPLY_YEAR",
    "PREVIOUSYEAR": "PREV_YEAR",
    "PREVIOUSMONTH": "PREV_MONTH",
    "PREVIOUSQUARTER": "PREV_QUARTER",
}

# Calendar unit each component operates on — shared by the boolean-SQL
# builder below.
PERIOD_UNITS: Dict[str, str] = {"YTD": "YEAR", "MTD": "MONTH", "QTD": "QUARTER"}
LAG_UNITS: Dict[str, str] = {
    "SPLY_YEAR": "YEAR",
    "SPLY_MONTH": "MONTH",
    "SPLY_QUARTER": "QUARTER",
    "PREV_YEAR": "YEAR",
    "PREV_MONTH": "MONTH",
    "PREV_QUARTER": "QUARTER",
}
CURRENT_UNITS: Dict[str, str] = {
    "CURRENT_MONTH": "MONTH",
    "CURRENT_QUARTER": "QUARTER",
    "CURRENT_YEAR": "YEAR",
    "CURRENT_DAY": "DAY",
}


def _period_to_date_component(fn_node: FunctionCallNode) -> str:
    """Canonical period-to-date component, honoring a fiscal year end
    (TOTALYTD/DATESYTD year_end_date at a month end -> "FYTDmm")."""
    from semabridge.converter.dax_ast_parser import (
        LiteralNode as _Lit,
        fiscal_ytd_component,
        parse_fiscal_year_end,
        period_to_date_year_end_node,
    )
    func = fn_node.func.upper()
    comp = PERIOD_TO_DATE_FUNCS[func]
    ye = period_to_date_year_end_node(fn_node)
    if ye is not None and isinstance(ye, _Lit) and isinstance(ye.value, str):
        try:
            month = parse_fiscal_year_end(ye.value)
        except ValueError:
            return comp  # the translator declines this metric anyway
        if month:
            return fiscal_ytd_component(month)
    return comp


_FYTD_RE = re.compile(r"^FYTD(\d\d)$")
_PREV_N_RE = re.compile(r"^PREV_(\d+)_([A-Z]+)$")
_SPLY_N_RE = re.compile(r"^SPLY_(\d+)_([A-Z]+)$")


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

    if isinstance(node, BinaryOpNode):
        left_s = _resolve_node_shape(node.left, dax_by_name, memo, visiting)
        right_s = _resolve_node_shape(node.right, dax_by_name, memo, visiting)
        return left_s or right_s

    if isinstance(node, UnaryOpNode):
        return _resolve_node_shape(node.operand, dax_by_name, memo, visiting)

    if isinstance(node, VarNode):
        for _vname, vexpr in node.variables:
            s = _resolve_node_shape(vexpr, dax_by_name, memo, visiting)
            if s:
                return s
        return _resolve_node_shape(node.body, dax_by_name, memo, visiting)

    if not isinstance(node, FunctionCallNode):
        return None

    func = node.func.upper()

    if func in PERIOD_TO_DATE_FUNCS:
        return (_period_to_date_component(node),)

    if func in LAG_FUNCS:
        arg0 = node.args[0] if node.args else None
        inner = _resolve_arg0_shape(arg0, dax_by_name, memo, visiting)
        outer = LAG_FUNCS[func]
        return (inner + (outer,)) if inner else (outer,)

    if func == "CALCULATE":
        # Structurally precise rolling months detection on AST repr string
        full_node_str = str(node)
        if "MONTHINDEX" in full_node_str.upper():
            m_r = re.search(r"MONTHINDEX.*?(?:op='-'|value=-)\s*(\d+)", full_node_str, re.IGNORECASE | re.DOTALL)
            if not m_r:
                m_r = re.search(r"MONTHINDEX.*?value=-?(\d+)", full_node_str, re.IGNORECASE | re.DOTALL)
            if m_r:
                n_months = int(m_r.group(1))
                return (f"R{n_months}M",)

        # Unpack CALCULATETABLE modifiers if present
        modifiers = []
        for mod in node.args[1:]:
            if isinstance(mod, FunctionCallNode) and mod.func.upper() == "CALCULATETABLE":
                modifiers.extend(mod.args)
            else:
                modifiers.append(mod)

        def _get_lag_shape(fn_node: FunctionCallNode) -> str:
            if fn_node.func.upper() in LAG_FUNCS:
                return LAG_FUNCS[fn_node.func.upper()]
            intv = "YEAR"
            if len(fn_node.args) >= 3:
                intv = str(getattr(fn_node.args[2], "name", getattr(fn_node.args[2], "value", "YEAR"))).upper()
            amt = 1
            if len(fn_node.args) >= 2:
                arg1 = fn_node.args[1]
                if isinstance(arg1, UnaryOpNode) and arg1.op == "-":
                    try:
                        amt = abs(int(getattr(arg1.operand, "value", 1)))
                    except Exception:
                        amt = 1
                else:
                    try:
                        amt = abs(int(getattr(arg1, "value", 1)))
                    except Exception:
                        amt = 1

            unit = "YEAR"
            if "YEAR" in intv:
                unit = "YEAR"
            elif "QUARTER" in intv:
                unit = "QUARTER"
            elif "MONTH" in intv:
                unit = "MONTH"
            elif "DAY" in intv:
                unit = "DAY"

            if fn_node.func.upper() == "PARALLELPERIOD":
                if amt == 1:
                    return f"PREV_{unit}"
                return f"PREV_{amt}_{unit}"

            if amt == 1:
                return f"SPLY_{unit}"
            return f"SPLY_{amt}_{unit}"

        p2d_modifier: Optional[FunctionCallNode] = None
        lag_modifier: Optional[FunctionCallNode] = None
        for modifier in modifiers:
            if isinstance(modifier, FunctionCallNode):
                m_fn = modifier.func.upper()
                if m_fn in PERIOD_TO_DATE_FUNCS:
                    p2d_modifier = modifier
                elif m_fn in LAG_FUNCS or m_fn in ("DATEADD", "PARALLELPERIOD"):
                    lag_modifier = modifier
                elif m_fn == "DATESINPERIOD" and len(modifier.args) == 4:
                    # DATESINPERIOD(dates, MAX/LASTDATE(dates), -n, MONTH): a
                    # rolling n-month window ending at the context's last
                    # date -- the same rows as the R{n}M flag.
                    start, n_node, unit_node = modifier.args[1], modifier.args[2], modifier.args[3]
                    unit = str(getattr(unit_node, "name", getattr(unit_node, "value", "")) or "").upper()
                    n_val = None
                    if isinstance(n_node, UnaryOpNode) and n_node.op == "-":
                        n_val = -int(getattr(n_node.operand, "value", 0) or 0)
                    elif isinstance(getattr(n_node, "value", None), int):
                        n_val = n_node.value
                    if (
                        unit == "MONTH" and n_val is not None and n_val < 0
                        and isinstance(start, FunctionCallNode) and start.func.upper() in ("MAX", "LASTDATE")
                    ):
                        return (f"R{abs(n_val)}M",)

        arg0 = node.args[0] if node.args else None
        inner = _resolve_arg0_shape(arg0, dax_by_name, memo, visiting)

        if p2d_modifier is not None and lag_modifier is not None:
            p2d_shape = _period_to_date_component(p2d_modifier)
            lag_shape = _get_lag_shape(lag_modifier)
            return (p2d_shape, lag_shape)

        if lag_modifier is not None:
            outer = _get_lag_shape(lag_modifier)
            return (inner + (outer,)) if inner else (outer,)

        if p2d_modifier is not None:
            outer = _period_to_date_component(p2d_modifier)
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


def detect_comparison_shapes(node: Optional[DaxNode]) -> Set[Shape]:
    """Detect if an AST represents a time-shifted comparison measure comparing
    an unshifted reference with a shifted reference (e.g. [X] - CALCULATE([X], DATEADD(...))).
    Returns both the current period shape and the matching previous period shape."""
    if node is None:
        return set()

    shifted_calls: List[Tuple[Any, str, int, str]] = []

    def _find_shifted(n: DaxNode):
        if isinstance(n, FunctionCallNode):
            fn = n.func.upper()
            if fn == "CALCULATE" and len(n.args) >= 2:
                base = n.args[0]
                for mod in n.args[1:]:
                    if isinstance(mod, FunctionCallNode):
                        mfn = mod.func.upper()
                        if mfn in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER"):
                            u = "YEAR" if "YEAR" in mfn else ("MONTH" if "MONTH" in mfn else "QUARTER")
                            shifted_calls.append((base, u, -1, mfn))
                        elif mfn in ("DATEADD", "PARALLELPERIOD") and len(mod.args) >= 3:
                            intv_raw = str(getattr(mod.args[2], "name", getattr(mod.args[2], "value", "YEAR"))).upper()
                            u = "YEAR" if "YEAR" in intv_raw else ("QUARTER" if "QUARTER" in intv_raw else ("MONTH" if "MONTH" in intv_raw else "DAY"))
                            amt = -1
                            arg1 = mod.args[1]
                            if isinstance(arg1, UnaryOpNode) and arg1.op == "-":
                                try:
                                    amt = -abs(int(getattr(arg1.operand, "value", 1)))
                                except Exception:
                                    amt = -1
                            elif isinstance(arg1, LiteralNode):
                                try:
                                    amt = int(getattr(arg1, "value", -1))
                                except Exception:
                                    amt = -1
                            shifted_calls.append((base, u, amt, mfn))
            for a in n.args:
                _find_shifted(a)
        elif isinstance(n, BinaryOpNode):
            _find_shifted(n.left)
            _find_shifted(n.right)
        elif isinstance(n, UnaryOpNode):
            _find_shifted(n.operand)
        elif isinstance(n, VarNode):
            for _, ve in n.variables:
                _find_shifted(ve)
            _find_shifted(n.body)

    _find_shifted(node)
    if not shifted_calls:
        return set()

    target_names = set()
    has_target = False
    for base, _, _, _ in shifted_calls:
        if isinstance(base, MeasureRefNode):
            target_names.add(base.name.upper())
        elif isinstance(base, FunctionCallNode):
            has_target = True

    found_unshifted = [False]

    def _find_unshifted(n: DaxNode, inside_shifted: bool = False):
        if inside_shifted:
            return
        if isinstance(n, FunctionCallNode):
            fn = n.func.upper()
            if fn == "CALCULATE" and len(n.args) >= 2:
                is_shifted_call = False
                for mod in n.args[1:]:
                    if isinstance(mod, FunctionCallNode) and mod.func.upper() in (
                        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER", "DATEADD", "PARALLELPERIOD"
                    ):
                        is_shifted_call = True
                        break
                if is_shifted_call:
                    for a in n.args:
                        _find_unshifted(a, True)
                    return
            if has_target and fn in ("SUM", "AVG", "AVERAGE", "MIN", "MAX"):
                found_unshifted[0] = True
            for a in n.args:
                _find_unshifted(a, False)
        elif isinstance(n, MeasureRefNode):
            if n.name.upper() in target_names:
                found_unshifted[0] = True
        elif isinstance(n, BinaryOpNode):
            _find_unshifted(n.left, False)
            _find_unshifted(n.right, False)
        elif isinstance(n, UnaryOpNode):
            _find_unshifted(n.operand, False)
        elif isinstance(n, VarNode):
            for _, ve in n.variables:
                _find_unshifted(ve, False)
            _find_unshifted(n.body, False)

    _find_unshifted(node)
    if not found_unshifted[0]:
        return set()

    res = set()
    for _, u, amt, mfn in shifted_calls:
        res.add((f"CURRENT_{u}",))
        n = abs(int(amt))
        if mfn == "SAMEPERIODLASTYEAR":
            res.add(("SPLY_YEAR",))
        elif mfn in ("PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER", "PARALLELPERIOD"):
            res.add((f"PREV_{u}" if n == 1 else f"PREV_{n}_{u}",))
        else:
            res.add((f"SPLY_{u}" if n == 1 else f"SPLY_{n}_{u}",))
    return res


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
    discovered = set(metrics_with_time_intelligence_shapes(metrics).values())
    parser = DaxAstParser()
    for m in metrics:
        expr = getattr(m, "expression", None)
        if not expr or not str(expr).strip():
            continue
        try:
            ast = parser.parse(expr)
            if ast:
                discovered |= detect_comparison_shapes(ast)
        except Exception:
            pass
    return discovered


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

    if len(shape) == 1 and shape[0] in CURRENT_UNITS:
        unit = CURRENT_UNITS[shape[0]]
        if unit == "MONTH":
            return f"YEAR({date_col_sql}) = YEAR({anchor_sql}) AND MONTH({date_col_sql}) = MONTH({anchor_sql})"
        if unit == "QUARTER":
            return f"YEAR({date_col_sql}) = YEAR({anchor_sql}) AND QUARTER({date_col_sql}) = QUARTER({anchor_sql})"
        if unit == "YEAR":
            return f"YEAR({date_col_sql}) = YEAR({anchor_sql})"
        if unit == "DAY":
            return f"{date_col_sql} = {anchor_sql}"

    if len(shape) == 1 and shape[0].startswith("R") and shape[0].endswith("M") and shape[0][1:-1].isdigit():
        n_months = int(shape[0][1:-1])
        return f"{date_col_sql} > DATEADD(MONTH, -{n_months}, {anchor_sql}) AND {date_col_sql} <= {anchor_sql}"

    effective_anchor = anchor_sql
    lag_components = [
        c for c in shape
        if c in LAG_UNITS or bool(_SPLY_N_RE.match(c)) or bool(_PREV_N_RE.match(c))
    ]
    period_components = [c for c in shape if c in PERIOD_UNITS or _FYTD_RE.match(c)]
    if not lag_components and not period_components:
        raise ValueError(f"build_shape_boolean_sql: unsupported shape {shape!r}")

    for component in lag_components:
        m_amt = _SPLY_N_RE.match(component) or _PREV_N_RE.match(component)
        if m_amt:
            amt = int(m_amt.group(1))
            unit = m_amt.group(2)
        else:
            amt = 1
            unit = LAG_UNITS.get(component, "YEAR")
        effective_anchor = f"DATEADD({unit}, -{amt}, {effective_anchor})"
        if component.startswith("PREV_") and unit in ("YEAR", "QUARTER", "MONTH"):
            # PREVIOUSx / PARALLELPERIOD return the WHOLE shifted period: an
            # inner period-to-date window is evaluated as of its last day.
            effective_anchor = f"LAST_DAY({effective_anchor}, '{unit}')"

    if period_components:
        comp = period_components[0]
        m_fy = _FYTD_RE.match(comp)
        if m_fy:
            k = int(m_fy.group(1))
            start = f"DATEADD(MONTH, {k}, DATE_TRUNC('YEAR', DATEADD(MONTH, -{k}, {effective_anchor})))"
            return f"{date_col_sql} >= {start} AND {date_col_sql} <= {effective_anchor}"
        unit = PERIOD_UNITS[comp]
        return (
            f"{date_col_sql} >= DATE_TRUNC('{unit}', {effective_anchor}) "
            f"AND {date_col_sql} <= {effective_anchor}"
        )

    # Lag-only shape (no period-to-date component): whole-prior-unit bound.
    last_comp = lag_components[-1]
    m_amt = _SPLY_N_RE.match(last_comp) or _PREV_N_RE.match(last_comp)
    if m_amt:
        amt = int(m_amt.group(1))
        unit = m_amt.group(2)
    else:
        amt = 1
        unit = LAG_UNITS.get(last_comp, "YEAR")

    if unit == "DAY":
        return f"{date_col_sql} = {effective_anchor}"
    if unit == "MONTH":
        return f"YEAR({date_col_sql}) = YEAR({effective_anchor}) AND MONTH({date_col_sql}) = MONTH({effective_anchor})"
    if unit == "QUARTER":
        return f"YEAR({date_col_sql}) = YEAR({effective_anchor}) AND QUARTER({date_col_sql}) = QUARTER({effective_anchor})"
    if unit == "YEAR":
        if last_comp.startswith("PREV_"):
            return f"YEAR({date_col_sql}) = YEAR({effective_anchor})"
        return (
            f"{date_col_sql} >= DATE_TRUNC('YEAR', {effective_anchor}) "
            f"AND {date_col_sql} <= {effective_anchor}"
        )

    return (
        f"{date_col_sql} >= DATE_TRUNC('{unit}', {effective_anchor}) "
        f"AND {date_col_sql} <= {effective_anchor}"
    )
