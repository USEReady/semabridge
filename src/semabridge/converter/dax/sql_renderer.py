"""DAX AST -> SQL rendering (DaxSqlRenderer), split out of dax_ast_parser unchanged.

Import it from ``semabridge.converter.dax_ast_parser`` (the facade).
"""
from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from semabridge.converter.function_registry import FunctionRegistry
from semabridge.utils.logger import get_logger
from semabridge.utils.naming import to_alias
from semabridge.target_rules.base import ReasonCode, SqlDialect
from semabridge.target_rules.snowflake import SNOWFLAKE_RULES

# Dialect used when a caller does not pass one: Snowflake, which is what
# every existing caller expects (unchanged behaviour).
_DEFAULT_DIALECT: SqlDialect = SNOWFLAKE_RULES.dialect
from semabridge.utils.relationship_graph import has_relationship_path

logger = get_logger("semabridge.converter.dax_ast_parser")

from semabridge.converter import dax_ast_parser as _parser  # noqa: E402  (facade; see bottom)
ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS = _parser.ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS
ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK = _parser.ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK
ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE = _parser.ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE
BinaryOpNode = _parser.BinaryOpNode
ColumnRefNode = _parser.ColumnRefNode
DaxAstParser = _parser.DaxAstParser
DaxNode = _parser.DaxNode
FunctionCallNode = _parser.FunctionCallNode
HASONEVALUE_GUARD_REASON = _parser.HASONEVALUE_GUARD_REASON
IdentifierNode = _parser.IdentifierNode
LiteralNode = _parser.LiteralNode
MeasureRefNode = _parser.MeasureRefNode
TABLE_CONSTRUCTOR_FUNC = _parser.TABLE_CONSTRUCTOR_FUNC
USERELATIONSHIP_REASON = _parser.USERELATIONSHIP_REASON
UnaryOpNode = _parser.UnaryOpNode
VarNode = _parser.VarNode
_CONTEXT_TRANSITION_REASON = _parser._CONTEXT_TRANSITION_REASON
_ast_contains_func = _parser._ast_contains_func
_is_empty_branch = _parser._is_empty_branch
_wrap_parens_for_op = _parser._wrap_parens_for_op
dax_numeric_literal_value = _parser.dax_numeric_literal_value
fiscal_ytd_component = _parser.fiscal_ytd_component
parse_fiscal_year_end = _parser.parse_fiscal_year_end
period_to_date_year_end_node = _parser.period_to_date_year_end_node
sanitize_column = _parser.sanitize_column
unwrap_var_body = _parser.unwrap_var_body
validate_emitted_sql = _parser.validate_emitted_sql


class DaxSqlRenderer:

    """

    Walks a parsed DAX AST and emits Snowflake-compatible SQL.



    The renderer is deterministic: given the same AST it always emits

    the same SQL.  It raises ``DaxRenderError`` for expressions it cannot

    safely translate.



    Args:

        table_alias: SQL alias for the primary fact table (e.g. ``"SALES"``).

        date_alias: SQL alias for the Date/Calendar dimension table. Defaults

            to ``"COL_DATE"`` — the established Snowflake naming convention

            for this dimension (see snowflake_emitter.py's "Map common DAX

            name -> physical name (e.g. Date -> COL_DATE)" and

            dax_rule_translator.py's own _date_alias(), both of which already

            use this convention).

        measure_sql_map: Pre-resolved SQL for other metrics, keyed by measure name.

            Used to resolve [MeasureName] references in Tier 2/4 expressions.

    """



    class DaxRenderError(Exception):

        pass



    # Time-intelligence function mapping

    _TIME_INTEL_FUNCS = {

        "TOTALYTD": "YTD",

        "TOTALMTD": "MTD",

        "TOTALQTD": "QTD",

        "SAMEPERIODLASTYEAR": "SAMEPERIODLASTYEAR",

        "PREVIOUSYEAR": "PREVIOUSYEAR",

        "PREVIOUSMONTH": "PREVIOUSMONTH",

        "PREVIOUSQUARTER": "PREVIOUSQUARTER",

    }



    _AGG_MAP = {

        "SUM": "SUM",

        "AVERAGE": "AVG",

        "MIN": "MIN",

        "MAX": "MAX",

        "COUNT": "COUNT",

        "DISTINCTCOUNT": "COUNT(DISTINCT {col})",

        "COUNTROWS": "COUNT(*)",

        "COUNTA": "COUNT",

    }



    _ITERATOR_MAP = {

        "SUMX": "SUM",

        "AVERAGEX": "AVG",

        "MINX": "MIN",

        "MAXX": "MAX",

        "COUNTAX": "COUNT",

    }



    # DAX function calls this renderer turns into a numeric (FLOAT/aggregate)

    # SQL expression rather than an inherently boolean one. DAX allows using

    # such an expression directly as an IF condition — 0/BLANK() is falsy,

    # anything else is truthy (a common YoY/SPLY guard, e.g.

    # IF(CALCULATE([Sales], SAMEPERIODLASTYEAR('Date'[Date])), <growth calc>,

    # BLANK()) to skip the growth calc when there's no prior-year data).

    # Snowflake requires an explicit BOOLEAN for a CASE WHEN condition —

    # passing the raw FLOAT-valued aggregate directly fails with "Can not

    # convert parameter '...' of type [FLOAT] into expected type [BOOLEAN]"

    # and takes down the whole semantic-view DDL, not just this one metric.

    # See _render_if / _is_numeric_truthiness_condition.

    _NUMERIC_TRUTHINESS_FUNCS = frozenset({

        "SUM", "AVERAGE", "AVG", "COUNT", "COUNTA", "COUNTROWS", "COUNTBLANK",

        "DISTINCTCOUNT", "MIN", "MAX", "CALCULATE", "DIVIDE",

        "VALUE", "MROUND", "ROUNDUP", "ROUNDDOWN",

        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER",

        "TOTALYTD", "TOTALMTD", "TOTALQTD",

    })



    # DAX names conventionally used for the date/calendar dimension across

    # this codebase (see dax_translator.py's "'Calendar'[Date]" dimension

    # reference and dax_engine.py's date_alias="calendar" default) — a

    # ColumnRefNode naming one of these is resolved to date_alias rather

    # than the primary table.

    def _ti_date_column_sql(self, args: List[DaxNode]) -> str:
        """The dates column exactly as written in the DAX, on its own table's alias."""
        search_args = args[1:] if len(args) > 1 else args
        for arg in search_args:
            if isinstance(arg, ColumnRefNode) and arg.column:
                alias = self._resolve_table_alias(arg.table) if arg.table else self.date_alias
                return self.dialect.column_ref(alias, arg.column)
            if isinstance(arg, IdentifierNode) and getattr(arg, "name", None):
                alias = self.date_alias
                return self.dialect.column_ref(alias, arg.name)
        for arg in args:
            if isinstance(arg, ColumnRefNode) and arg.column:
                alias = self._resolve_table_alias(arg.table) if arg.table else self.date_alias
                return self.dialect.column_ref(alias, arg.column)
            if isinstance(arg, IdentifierNode) and getattr(arg, "name", None):
                alias = self.date_alias
                return self.dialect.column_ref(alias, arg.name)
        raise self.DaxRenderError("no date column found for time-intelligence function")


    def __init__(

        self,

        table_alias: str = "T",

        date_alias: str = "Date",

        measure_sql_map: Optional[Dict[str, str]] = None,

        known_measure_names: Optional[Any] = None,

        anchor_flag_map: Optional[Dict[Tuple[str, ...], str]] = None,

        primary_table_name: Optional[str] = None,

        date_table_names: Optional[Any] = None,

        allow_unshifted_fallback: bool = False,

        advisory_categories: Optional[List[str]] = None,

        table_aliases: Optional[Dict[str, str]] = None,

        relationships: Optional[Iterable[Any]] = None,

        measure_non_additive_map: Optional[Dict[str, Tuple[Optional[str], str]]] = None,

        table_key_map: Optional[Dict[str, str]] = None,

        allow_window_enrichment: bool = False,

        datasets: Optional[Iterable[Any]] = None,

        model_metadata: Optional[Any] = None,
        dataset_col_lookup: Optional[Dict[str, set]] = None,
        current_measure_name: Optional[str] = None,
        measure_dax_map: Optional[Dict[str, str]] = None,
        dialect: Optional[SqlDialect] = None,
        rules: Optional[Any] = None,
    ) -> None:

        # The target platform's rules (default Snowflake, which every
        # existing caller expects): its policies say what a declined
        # measure's placeholder SQL looks like; its dialect spells SQL
        # (quoting, casts, function names) unless `dialect` is given.
        self.rules = rules or SNOWFLAKE_RULES
        self.dialect: SqlDialect = dialect or (rules.dialect if rules is not None else _DEFAULT_DIALECT)
        # Regex pieces for reading the renderer's own SQL back (e.g. finding
        # alias.<quoted column> refs, or stripping a trailing float cast),
        # derived from the dialect so they match what it writes.
        self._ident_quote_re: str = re.escape(self.dialect.identifier_quote)
        self._float_cast_re: str = re.escape(self.dialect.float_cast_suffix)

        # Keep alias as provided (caller passes the correct form)

        self.table_alias = table_alias

        self.date_alias = date_alias
        self.current_measure_name = current_measure_name

        self.measure_sql_map = measure_sql_map or {}
        self.measure_dax_map = measure_dax_map or {}
        self._current_loo_info: Optional[Dict[str, Any]] = None

        self.measure_non_additive_map = measure_non_additive_map or {}

        self.non_additive_dimensions: List[Tuple[Optional[str], str]] = []

        self.table_key_map = table_key_map or {}

        self.allow_window_enrichment = bool(allow_window_enrichment)

        self.window_precompute_columns: List[Tuple[str, str, str, str]] = []
        self.datasets = datasets
        self.model_metadata = model_metadata
        self.dataset_col_lookup = dataset_col_lookup

        self._userel_overrides: Dict[str, str] = {}
        self._in_shifted_calculate: int = 0
        self._comparison_context: Optional[Dict[str, Any]] = None
        self.comparison_unit: Optional[str] = None
        self.comparison_comment: Optional[str] = None
        self.last_render_error: Optional[str] = None

        # The DAX table name (e.g. "KPI") that table_alias physically

        # represents for this metric, if the caller knows it. None means

        # the caller hasn't opted into table-aware column resolution yet —

        # _resolve_table_alias then preserves the historical

        # single-table-assumed behavior exactly, so callers that don't pass

        # this are unaffected.

        self.primary_table_name = primary_table_name

        self._date_table_names_cf = {
            str(n).strip().strip("'").casefold()
            for n in (date_table_names or ())
        }

        self.table_aliases = table_aliases or {}

        self._table_aliases_cf = {
            str(k).strip().strip("'").casefold(): v for k, v in self.table_aliases.items()
        }

        self.relationships = relationships

        # Shape tuple (see converter/time_intelligence_shapes.py, e.g.

        # ("YTD",), ("YTD", "SPLY_YEAR")) -> precomputed boolean flag column

        # name on the enriched view (e.g. "IS_YTD"). When a shape has an

        # entry here, _render_period_to_date/_render_lag_period reference

        # that column directly instead of building inline MAX_DATE

        # arithmetic — MAX_DATE itself is rejected by Snowflake's semantic-

        # view compiler even though the column physically exists; an

        # ordinary per-row boolean column is not. Empty (the default)

        # preserves today's inline-MAX_DATE behavior exactly — this is the

        # schema-blind dry-run/preview path's only mode, since it has no

        # live enriched view to reference flags on.

        self.anchor_flag_map: Dict[Tuple[str, ...], str] = anchor_flag_map or {}

        # Names of measures known to exist in the model but not yet resolved

        # to SQL (absent from measure_sql_map). Distinguishes "this bracket

        # reference names a real measure that just isn't translated yet"

        # (must fail closed — see _render_measure_ref) from "this bracket

        # reference names something the caller has no measure-registry

        # knowledge of at all" (the common SUM([Column]) shape, where a bare

        # bracket names a physical column — falls back to a column

        # reference, exactly as before).

        self.known_measure_names = set(known_measure_names or ())

        # DAX measure-name resolution is case-insensitive (Analysis Services

        # treats [Total Units] and [TOTAL UNITS] as the same measure) —

        # build casefolded lookups alongside the caller-provided ones so

        # _render_measure_ref matches regardless of casing, without

        # weakening the known-but-unresolved-vs-genuinely-unknown

        # distinction in _render_measure_ref below.

        self._measure_sql_map_by_casefold = {

            str(name).casefold(): sql for name, sql in self.measure_sql_map.items()

        }

        self._known_measure_names_casefold = {

            str(name).casefold() for name in self.known_measure_names

        }

        self._func_registry = FunctionRegistry()

        # Opt-in only -- False preserves the historical fail-closed

        # behavior exactly for every existing caller. Only a caller that

        # explicitly wants "ship an unshifted approximation, flagged for

        # review" instead of a hard translation failure sets this True

        # (see _render_lag_period's two DaxRenderError sites).

        self._allow_unshifted_fallback = allow_unshifted_fallback

        # Caller-provided list is mutated in place (same collector pattern

        # as DropLedger elsewhere in this codebase) so the caller can read

        # it back after render() returns; a fresh list is used internally

        # either way so this attribute is never None.

        self._advisory_categories: List[str] = (

            advisory_categories if advisory_categories is not None else []

        )

        self._var_scopes: List[Dict[str, str]] = []
        self.row_context_stack: List[str] = []

    def _extract_table_name(self, node: Any) -> Optional[str]:
        if node is None:
            return None
        if isinstance(node, IdentifierNode):
            return node.name.strip("'\"")
        if isinstance(node, ColumnRefNode):
            return (node.table or "").strip("'\"") or None
        if isinstance(node, FunctionCallNode):
            fn = node.func.upper()
            if fn in ("KEEPFILTERS", "ALL", "ALLNOBLANKROW", "REMOVEFILTERS", "FILTER") and node.args:
                return self._extract_table_name(node.args[0])
        name = getattr(node, "name", None) or getattr(node, "table", None) or getattr(node, "raw", None)
        if name and isinstance(name, str) and not name.startswith("["):
            return name.strip("'\"")
        return None

    @contextlib.contextmanager
    def _row_context(self, table_name: Optional[str]):
        if table_name:
            clean_name = str(table_name).strip().strip("'\"")
            self.row_context_stack.append(clean_name)
            try:
                yield
            finally:
                self.row_context_stack.pop()
        else:
            yield



    def _lookup_var(self, name: str) -> Optional[str]:

        clean = str(name or "").strip("[]").casefold()

        for scope in reversed(self._var_scopes):

            if clean in scope:

                return scope[clean]

        return None



    def _render_var_node(self, node: VarNode) -> str:

        new_scope: Dict[str, str] = {}

        self._var_scopes.append(new_scope)

        if not hasattr(self, "_var_ast_map"):
            self._var_ast_map = {}
        try:
            for var_name, var_expr in node.variables:
                clean_name = str(var_name or "").strip("[]").casefold()
                self._var_ast_map[clean_name] = var_expr
                if self._extract_closing_balance_dimension(var_expr) is not None:
                    continue
                try:
                    var_sql = self._render_node(var_expr)
                    if var_sql and validate_emitted_sql(var_sql):
                        new_scope[clean_name] = f"({var_sql})"
                except self.DaxRenderError:
                    pass
            body_sql = self._render_node(node.body)

            if not body_sql or not validate_emitted_sql(body_sql):

                raise self.DaxRenderError("Failed to render RETURN body")

            return body_sql

        finally:

            self._var_scopes.pop()



    def render(self, node: Optional[DaxNode]) -> Optional[str]:

        """

        Render a DAX AST node to Snowflake SQL.



        Returns None if the node cannot be safely translated.

        """

        if node is None:

            return None

        # Rule B: A measure whose DAX is only a numeric literal -> constant metric rendered as MAX(<literal>)
        num_lit = dax_numeric_literal_value(node)
        if num_lit is not None:
            return f"MAX({num_lit})"

        try:
            self._detect_and_setup_loo(node)
            inlined_for_comparison = self._inline_vars_and_measures(node)
            self._detect_and_setup_comparison(inlined_for_comparison)
            sql = self._render_node(node)
            if not validate_emitted_sql(sql):
                raise self.DaxRenderError(f"Emitted SQL contains un-translated DAX syntax: {sql}")
            # Window metrics (OVER ..., precomputed window columns) are a
            # platform capability; a platform without them declines.
            if not self.rules.capabilities.window_metrics and (
                self.window_precompute_columns or re.search(r"\bOVER\s*\(", sql, re.IGNORECASE)
            ):
                raise self.DaxRenderError(self.rules.policies.reason_text(ReasonCode.NEEDS_WINDOW))
            if self.dialect.current_date().upper() in sql.upper() and not _ast_contains_func(node, {"TODAY", "NOW"}):
                if ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE not in self._advisory_categories:
                    self._advisory_categories.append(ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE)
            return sql

        except self.DaxRenderError as exc:

            logger.debug(f"AST render failed (recoverable): {exc}")
            self.last_render_error = str(exc)

            return None

    def _extract_shift_amount(self, arg1: DaxNode) -> int:
        if isinstance(arg1, UnaryOpNode) and arg1.op == "-":
            if isinstance(arg1.operand, LiteralNode):
                try:
                    val = arg1.operand.value
                    if isinstance(val, int) and not isinstance(val, bool):
                        return -val
                    if isinstance(val, str) and re.match(r"^\d+$", val.strip()):
                        return -int(val.strip())
                except (ValueError, TypeError):
                    pass
        elif isinstance(arg1, LiteralNode):
            try:
                val = arg1.value
                if isinstance(val, int) and not isinstance(val, bool):
                    return val
                if isinstance(val, str) and re.match(r"^-?\d+$", val.strip()):
                    return int(val.strip())
            except (ValueError, TypeError):
                pass
        raise self.DaxRenderError("DATEADD shift amount must be a literal integer")

    def _inline_vars_and_measures(
        self,
        node: DaxNode,
        seen_measures: Optional[Set[str]] = None,
        var_env: Optional[Dict[str, DaxNode]] = None,
    ) -> DaxNode:
        """Inline VAR definitions and referenced measures into a single expression tree
        before detecting time comparisons or leave-one-out patterns."""
        if node is None:
            return node
        if seen_measures is None:
            seen_measures = set()
        if var_env is None:
            var_env = {}

        if isinstance(node, VarNode):
            new_env = dict(var_env)
            for vname, vexpr in node.variables:
                clean_v = str(vname or "").strip("[]").casefold()
                inlined_vexpr = self._inline_vars_and_measures(vexpr, seen_measures, new_env)
                new_env[clean_v] = inlined_vexpr
            return self._inline_vars_and_measures(node.body, seen_measures, new_env)

        if isinstance(node, IdentifierNode):
            clean_id = str(node.name or "").strip("[]").casefold()
            if clean_id in var_env:
                return var_env[clean_id]
            return node

        if isinstance(node, ColumnRefNode):
            if not node.table:
                clean_c = str(node.column or "").strip("[]").casefold()
                if clean_c in var_env:
                    return var_env[clean_c]
            return node

        if isinstance(node, MeasureRefNode):
            clean_m = str(node.name or "").strip("[]").casefold()
            if clean_m in var_env:
                return var_env[clean_m]
            measure_dax = getattr(self, "measure_dax_map", None) or {}
            dax_text = measure_dax.get(node.name) or measure_dax.get(node.name.upper()) or measure_dax.get(clean_m)
            if dax_text and clean_m not in seen_measures:
                try:
                    parsed = DaxAstParser().parse(dax_text)
                    if parsed:
                        return self._inline_vars_and_measures(parsed, seen_measures | {clean_m}, var_env)
                except Exception:
                    pass
            return node

        if isinstance(node, FunctionCallNode):
            new_args = [self._inline_vars_and_measures(a, seen_measures, var_env) for a in node.args]
            return FunctionCallNode(func=node.func, args=new_args)

        if isinstance(node, BinaryOpNode):
            new_l = self._inline_vars_and_measures(node.left, seen_measures, var_env)
            new_r = self._inline_vars_and_measures(node.right, seen_measures, var_env)
            return BinaryOpNode(left=new_l, op=node.op, right=new_r)

        if isinstance(node, UnaryOpNode):
            new_op = self._inline_vars_and_measures(node.operand, seen_measures, var_env)
            return UnaryOpNode(op=node.op, operand=new_op)

        return node

    def _detect_and_setup_loo(self, node: DaxNode) -> None:
        """Pre-scan the AST for a leave-one-out pattern:
        CALCULATE(agg, FILTER(ALL(T), T[c] <> SELECTEDVALUE(T[c]))).
        Sets up self._current_loo_info so all parts of the measure (including sibling
        variables like AllAvg) can reference the correct decomposed window expressions."""
        if node is None:
            return

        loo_found = []

        def _walk(n: DaxNode):
            if n is None or loo_found:
                return
            if isinstance(n, FunctionCallNode) and n.func.upper() == "CALCULATE" and len(n.args) >= 2:
                base_arg = n.args[0]
                for f_arg in n.args[1:]:
                    if isinstance(f_arg, FunctionCallNode) and f_arg.func.upper() == "FILTER" and len(f_arg.args) >= 2:
                        f_tbl = f_arg.args[0]
                        f_pred = f_arg.args[1]
                        if isinstance(f_tbl, FunctionCallNode) and f_tbl.func.upper() in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS"):
                            if isinstance(f_pred, BinaryOpNode) and f_pred.op in ("<>", "!="):
                                col_node, sv_node = None, None
                                if isinstance(f_pred.left, ColumnRefNode) and isinstance(f_pred.right, FunctionCallNode) and f_pred.right.func.upper() == "SELECTEDVALUE":
                                    col_node, sv_node = f_pred.left, f_pred.right
                                elif isinstance(f_pred.right, ColumnRefNode) and isinstance(f_pred.left, FunctionCallNode) and f_pred.left.func.upper() == "SELECTEDVALUE":
                                    col_node, sv_node = f_pred.right, f_pred.left
                                if col_node and sv_node and sv_node.args and isinstance(sv_node.args[0], ColumnRefNode):
                                    c1 = sanitize_column(col_node.column).upper()
                                    c2 = sanitize_column(sv_node.args[0].column).upper()
                                    if c1 == c2:
                                        t1 = getattr(col_node, "table", None) or ""
                                        loo_found.append((t1, c1, base_arg))
                                        return
            for child in getattr(n, "args", []) or []:
                _walk(child)
            for attr in ("left", "right", "operand", "body"):
                if hasattr(n, attr):
                    _walk(getattr(n, attr))
            if isinstance(n, VarNode):
                for _, ve in n.variables:
                    _walk(ve)

        _walk(node)
        if not loo_found:
            return

        t1, c1, base_arg = loo_found[0]
        inner_fn = getattr(base_arg, "func", "").upper() if isinstance(base_arg, FunctionCallNode) else ""
        if inner_fn in ("MIN", "MAX", "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK"):
            raise self.DaxRenderError("leave-one-out not decomposable")

        tbl_alias = self.table_aliases.get(t1, t1).upper() if (t1 and self.table_aliases) else self.table_alias.upper()
        dim_ref = f'{tbl_alias}."{c1}"'

        agg_col_node = base_arg.args[0] if (isinstance(base_arg, FunctionCallNode) and base_arg.args) else None
        agg_col_name = getattr(agg_col_node, "column", None) or getattr(agg_col_node, "name", None) or "AMOUNT"
        safe_agg_col = sanitize_column(agg_col_name).upper()

        fact_alias = self.table_alias.upper()
        sum_total = f'SUM({fact_alias}."{safe_agg_col}_SUM"::FLOAT) OVER (PARTITION BY EXCLUDING {dim_ref})'
        sum_own = f'{fact_alias}."{safe_agg_col}_SUM"'
        cnt_total = f'SUM({fact_alias}."{safe_agg_col}_CNT"::FLOAT) OVER (PARTITION BY EXCLUDING {dim_ref})'
        cnt_own = f'{fact_alias}."{safe_agg_col}_CNT"'

        self._current_loo_info = {
            "dim_ref": dim_ref,
            "col_name": c1,
            "agg_col": safe_agg_col,
            "inner_fn": inner_fn,
            "sum_total": sum_total,
            "sum_own": sum_own,
            "cnt_total": cnt_total,
            "cnt_own": cnt_own,
        }

    def _detect_and_setup_comparison(self, node: DaxNode) -> None:
        """Detect if this measure compares an unshifted reference [X] with a shifted reference
        CALCULATE([X], <time_func>), and prepare the comparison context so both sides receive
        matching period flags."""
        if node is None:
            return

        shifted_calls: List[Tuple[Any, str, int, str, DaxNode]] = []

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
                                shifted_calls.append((base, u, -1, mfn, n))
                            elif mfn in ("DATEADD", "PARALLELPERIOD") and len(mod.args) >= 3:
                                amt = self._extract_shift_amount(mod.args[1])
                                intv_raw = str(getattr(mod.args[2], "name", getattr(mod.args[2], "value", "YEAR"))).upper()
                                if "YEAR" in intv_raw:
                                    u = "YEAR"
                                elif "QUARTER" in intv_raw:
                                    u = "QUARTER"
                                elif "MONTH" in intv_raw:
                                    u = "MONTH"
                                elif "DAY" in intv_raw:
                                    u = "DAY"
                                else:
                                    raise self.DaxRenderError(f"Unsupported DATEADD interval '{intv_raw}'")
                                shifted_calls.append((base, u, amt, mfn, n))
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
            return

        first_call = shifted_calls[0]
        base_unit = first_call[1]
        base_amt = first_call[2]
        base_mfn = first_call[3]
        for _, u, amt, mfn, _ in shifted_calls[1:]:
            if u != base_unit or amt != base_amt:
                raise self.DaxRenderError("Conflicting time windows in comparison measure")
            if (base_mfn == "SAMEPERIODLASTYEAR") != (mfn == "SAMEPERIODLASTYEAR"):
                raise self.DaxRenderError("Conflicting time windows in comparison measure")

        target_name = None
        target_agg = None
        if isinstance(first_call[0], MeasureRefNode):
            target_name = first_call[0].name
        elif isinstance(first_call[0], FunctionCallNode):
            target_agg = first_call[0]

        shifted_calc_ids = {id(sc[4]) for sc in shifted_calls}
        found_unshifted = [False]

        def _find_unshifted(n: DaxNode):
            if id(n) in shifted_calc_ids:
                return
            if isinstance(n, MeasureRefNode):
                if target_name and n.name.casefold() == target_name.casefold():
                    found_unshifted[0] = True
            elif isinstance(n, FunctionCallNode):
                if target_agg and n.func.upper() == target_agg.func.upper():
                    found_unshifted[0] = True
                for a in n.args:
                    _find_unshifted(a)
            elif isinstance(n, BinaryOpNode):
                _find_unshifted(n.left)
                _find_unshifted(n.right)
            elif isinstance(n, UnaryOpNode):
                _find_unshifted(n.operand)
            elif isinstance(n, VarNode):
                for _, ve in n.variables:
                    _find_unshifted(ve)
                _find_unshifted(n.body)

        _find_unshifted(node)
        if not found_unshifted[0]:
            return

        curr_shape = (f"CURRENT_{base_unit}",)
        curr_flag = self.anchor_flag_map.get(curr_shape) if self.anchor_flag_map else None

        if base_mfn == "SAMEPERIODLASTYEAR":
            lag_shape = ("SPLY_YEAR",)
        elif abs(base_amt) == 1:
            lag_shape = (f"PREV_{base_unit}",)
        else:
            lag_shape = (f"SPLY_{abs(base_amt)}_{base_unit}",)

        lag_flag = self.anchor_flag_map.get(lag_shape) if self.anchor_flag_map else None
        # Only use flag-based comparison when the enriched view actually
        # declares both flags; never reference a guessed column name.
        if not curr_flag or not lag_flag:
            return

        self._comparison_context = {
            "target_name": target_name,
            "target_agg": target_agg,
            "unit": base_unit,
            "curr_flag": curr_flag,
            "lag_flag": lag_flag,
            "lag_shape": lag_shape,
        }
        self.comparison_unit = base_unit.lower()
        self.comparison_comment = f"latest {base_unit.lower()} vs previous {base_unit.lower()} only"

    def _is_operand_nullable(self, node: Optional[DaxNode], rendered_sql: str) -> bool:
        """Operand nullability decided by the OUTERMOST function:
        COUNT(...) is never nullable (even with CASE inside) -> False (non-nullable).
        Numeric/boolean literals and constants -> False (non-nullable).
        Otherwise (SUM, AVG, MIN, MAX, CASE without ELSE, arithmetic) -> True (nullable).
        """
        clean_sql = self._unwrap_str(rendered_sql)

        # 1. Outermost COUNT is never nullable (even with CASE inside)
        if re.match(r"^COUNT(?:_IF)?\s*\(", clean_sql, re.IGNORECASE):
            return False

        # 2. Numeric and boolean constants are never nullable
        if re.match(r"^-?\d+(\.\d+)?$", clean_sql):
            return False
        if clean_sql.upper() in ("TRUE", "FALSE"):
            return False

        # 3. Outer node is COUNT in AST
        if node is not None:
            unwrapped_node = unwrap_var_body(node)
            if isinstance(unwrapped_node, FunctionCallNode) and unwrapped_node.func.upper() in (
                "COUNT", "COUNTA", "COUNTROWS", "DISTINCTCOUNT"
            ):
                return False

        # 4. Any SUM/AVG/AVERAGE/MIN/MAX is nullable
        if re.search(r"\b(SUM|AVG|AVERAGE|MIN|MAX)\s*\(", clean_sql, re.IGNORECASE):
            return True

        # Check for a CASE without an ELSE
        tokens = re.findall(r"\b(CASE|ELSE|END)\b", clean_sql, re.IGNORECASE)
        if tokens:
            stack = []
            for tok in tokens:
                t = tok.upper()
                if t == "CASE":
                    stack.append(False)
                elif t == "ELSE":
                    if stack:
                        stack[-1] = True
                elif t == "END":
                    if stack:
                        has_else = stack.pop()
                        if not has_else:
                            return True

        if node is not None and self._node_contains_nullable_func(node):
            return True

        return True

    def _node_contains_nullable_func(self, node: DaxNode) -> bool:
        if isinstance(node, FunctionCallNode):
            func_u = node.func.upper()
            if func_u in ("SUM", "AVG", "AVERAGE", "MIN", "MAX"):
                return True
            return any(self._node_contains_nullable_func(a) for a in node.args)
        if isinstance(node, BinaryOpNode):
            return self._node_contains_nullable_func(node.left) or self._node_contains_nullable_func(node.right)
        if isinstance(node, UnaryOpNode):
            return self._node_contains_nullable_func(node.operand)
        if isinstance(node, VarNode):
            return any(self._node_contains_nullable_func(v[1]) for v in node.variables) or self._node_contains_nullable_func(node.body)
        return False

    def _unwrap_str(self, s: str) -> str:
        s = (s or "").strip()
        while s.startswith("(") and s.endswith(")"):
            depth = 0
            matched = True
            for c in s[:-1]:
                if c == "(": depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        matched = False
                        break
            if matched and depth == 1:
                s = s[1:-1].strip()
            else:
                break
        return s

    def _safe_coalesce_operand(self, s: str, is_nullable: bool) -> str:
        if not is_nullable:
            return s
        unwrapped = self._unwrap_str(s)
        u = unwrapped.upper()
        if (u.startswith("COALESCE(") and (u.endswith(", 0)") or u.endswith(", 0.0)"))) or (u.startswith(f"{self.dialect.iff_function.upper()}(") and u.endswith(", 0)")):
            return s
        return f"COALESCE({s}, 0)"



    def _render_node(self, node: DaxNode) -> str:

        if isinstance(node, VarNode):

            return self._render_var_node(node)

        if isinstance(node, LiteralNode):

            return self._render_literal(node)

        if isinstance(node, ColumnRefNode):

            return self._render_column_ref(node)

        if isinstance(node, MeasureRefNode):

            return self._render_measure_ref(node)

        if isinstance(node, BinaryOpNode):

            return self._render_binary(node)

        if isinstance(node, UnaryOpNode):

            return self._render_unary(node)

        if isinstance(node, FunctionCallNode):

            return self._render_function(node)

        if isinstance(node, IdentifierNode):

            var_sql = self._lookup_var(node.name)

            if var_sql is not None:

                return var_sql

            return f'"{sanitize_column(node.name)}"'

        raise self.DaxRenderError(f"Unknown node type: {type(node).__name__}")



    # ------------------------------------------------------------------



    def _render_literal(self, node: LiteralNode) -> str:

        if isinstance(node.value, bool):

            return "TRUE" if node.value else "FALSE"

        if isinstance(node.value, str):

            escaped = node.value.replace("'", "''")

            return f"'{escaped}'"

        return str(node.value)



    def _render_column_ref(self, node: ColumnRefNode) -> str:
        col_clean = (node.column or "").strip("[]")
        col_cf = col_clean.casefold()
        tbl_clean = (node.table or "").strip("'\"")
        qualified_cf = f"{tbl_clean}[{col_clean}]".casefold() if tbl_clean else None

        if not tbl_clean:
            var_sql = self._lookup_var(col_clean)
            if var_sql is not None:
                return var_sql
            if col_cf in self._measure_sql_map_by_casefold:
                val = self._measure_sql_map_by_casefold[col_cf]
                if self.rules.policies.is_placeholder(val):
                    raise self.DaxRenderError(f"depends on untranslatable part: [{col_clean}]")
                if val.strip().upper() == "NULL":
                    return "NULL"
                if self.row_context_stack:
                    return self._row_level_measure_sql(col_clean, val)
                return f"({val})"
            if col_cf in self._known_measure_names_casefold:
                raise self.DaxRenderError(
                    f"Unresolved measure reference [{col_clean}]: known measure with no resolved SQL yet"
                )
        elif qualified_cf:
            if qualified_cf in self._measure_sql_map_by_casefold:
                val = self._measure_sql_map_by_casefold[qualified_cf]
                if self.rules.policies.is_placeholder(val):
                    raise self.DaxRenderError(f"depends on untranslatable part: [{node.raw}]")
                if val.strip().upper() == "NULL":
                    return "NULL"
                return f"({val})"
            if qualified_cf in self._known_measure_names_casefold:
                raise self.DaxRenderError(
                    f"Unresolved measure reference [{node.raw}]: known measure with no resolved SQL yet"
                )

        col = sanitize_column(node.column)
        if tbl_clean:
            alias = self._resolve_table_alias(tbl_clean)
        elif self.row_context_stack:
            alias = self._resolve_table_alias(self.row_context_stack[-1])
        else:
            alias = self.table_alias
        return self.dialect.qualify(alias, col)

    def _resolve_table_alias(self, table_name: str) -> str:
        name_clean = str(table_name or "").strip().strip("'\"")
        name_cf = name_clean.casefold()

        if hasattr(self, "_userel_overrides") and self._userel_overrides:
            if name_cf in self._userel_overrides:
                return self._userel_overrides[name_cf]
            ali_cf = to_alias(name_clean).casefold()
            if ali_cf in self._userel_overrides:
                return self._userel_overrides[ali_cf]
            san_cf = sanitize_column(name_clean).casefold()
            if san_cf in self._userel_overrides:
                return self._userel_overrides[san_cf]

        if not name_clean:
            return self.table_alias

        if self.primary_table_name and name_cf == self.primary_table_name.strip().strip("'\"").casefold():
            return self.table_alias

        # No primary-table context, but the referenced table's own alias IS the
        # metric's alias (differing only in case): reuse the caller's exact
        # alias spelling so one metric never mixes `sales.` and `SALES.`.
        if (
            not self.primary_table_name
            and self.table_alias
            and to_alias(name_clean).casefold() == str(self.table_alias).casefold()
        ):
            return self.table_alias

        if hasattr(self, "_table_aliases_cf") and name_cf in self._table_aliases_cf:
            if ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS not in self._advisory_categories:
                self._advisory_categories.append(ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS)
            return self._table_aliases_cf[name_cf]

        if self.primary_table_name and self.relationships is not None:
            has_any_rel = any(
                str(getattr(r, "from_dataset", "")).casefold() == self.primary_table_name.strip("'\"").casefold()
                or str(getattr(r, "to_dataset", "")).casefold() == self.primary_table_name.strip("'\"").casefold()
                for r in self.relationships
            )
            if has_any_rel and not has_relationship_path(self.relationships, self.primary_table_name, name_clean):
                raise self.DaxRenderError(
                    f"Column reference to table '{table_name}' has no relationship path from "
                    f"'{self.primary_table_name}'"
                )

        if hasattr(self, "_table_aliases_cf") and name_cf in self._table_aliases_cf:
            if ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS not in self._advisory_categories:
                self._advisory_categories.append(ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS)
            return self._table_aliases_cf[name_cf]

        return to_alias(name_clean)



    _PRECEDENCE = {

        "OR": 10,

        "AND": 20,

        "=": 30, "<>": 30, "<": 30, "<=": 30, ">": 30, ">=": 30,

        "+": 40, "-": 40,

        "*": 50, "/": 50,

    }



    def _is_primary_table(self, table_name: Optional[str]) -> bool:
        t = str(table_name or "").strip().strip("'\"")
        if not t:
            return False
        if self.primary_table_name:
            p = self.primary_table_name.strip().strip("'\"")
            if t.casefold() == p.casefold():
                return True
        return to_alias(t).casefold() == str(self.table_alias or "").casefold() or t.casefold() == str(self.table_alias or "").casefold()

    def _row_level_measure_sql(self, name: str, measure_sql: str) -> str:
        """A measure referenced inside a row iterator (SUMX/FILTER/...) is
        evaluated with context transition: once per iterated row. When the
        iterated table is this metric's own fact table and the measure is a
        single SUM/MIN/MAX/AVG over that table's columns (or COUNT(*)), the
        per-row value is the row's own value (or 1), so the iterator can be
        rendered at row grain. Anything else needs per-row re-aggregation
        this translator cannot express -- declined."""
        tbl = self.row_context_stack[-1]
        const_probe = re.sub(r"'(?:''|[^'])*'", "''", measure_sql)
        if not self._AGG_CALL_START.search(const_probe) and not re.search(
            rf"\b[A-Za-z_][A-Za-z0-9_$]*\s*\.\s*(?:{self._ident_quote_re}|[A-Za-z_])", const_probe
        ) and not re.search(r"\b(CURRENT_DATE|CURRENT_TIMESTAMP)\b", const_probe, re.IGNORECASE):
            # A constant measure has the same value in every row context.
            return f"({self._unwrap_str(measure_sql)})"
        if not self._is_primary_table(tbl):
            raise self.DaxRenderError(
                f"not applicable: measure [{name}] is evaluated once per row of '{tbl}' (context transition) "
                "and re-aggregated; this per-group aggregation is not expressible as a single semantic-view metric"
            )
        s = self._unwrap_str(measure_sql)
        if re.fullmatch(r"COUNT\s*\(\s*\*\s*\)", s, re.IGNORECASE):
            return "1"
        m = re.match(r"^(SUM|MIN|MAX|AVG)\s*\(", s, re.IGNORECASE)
        if m:
            close = self._find_balanced_close_paren(s, m.end() - 1)
            if close == len(s) - 1:
                arg = s[m.end():close].strip()
                arg = re.sub(rf"{self._float_cast_re}\s*$", "", arg, flags=re.IGNORECASE).strip()
                refs = re.findall(rf"\b([A-Za-z_][A-Za-z0-9_$]*)\s*\.\s*(?={self._ident_quote_re}|[A-Za-z_])", re.sub(r"'(?:''|[^'])*'", "''", arg))
                if not self._AGG_CALL_START.search(arg) and all(r.casefold() == str(self.table_alias).casefold() for r in refs):
                    return f"({arg})"
        raise self.DaxRenderError(
            f"not applicable: measure [{name}] inside a row iterator requires context transition over an "
            "expression that is not a single row-level aggregate of the iterated table"
        )

    def _has_many_to_one_path(self, src: str, dst: str) -> bool:
        src_cf = str(src or "").strip("'\"").casefold()
        dst_cf = str(dst or "").strip("'\"").casefold()
        if src_cf == dst_cf:
            return True
        edges: Dict[str, Set[str]] = {}
        for r in self.relationships or []:
            if getattr(r, "is_active", getattr(r, "isActive", True)) is False:
                continue
            f = str(getattr(r, "from_dataset", None) or getattr(r, "from_table", "") or "").strip("'\"").casefold()
            t = str(getattr(r, "to_dataset", None) or getattr(r, "to_table", "") or "").strip("'\"").casefold()
            if f and t:
                edges.setdefault(f, set()).add(t)
        seen, stack = {src_cf}, [src_cf]
        while stack:
            cur = stack.pop()
            for nxt in edges.get(cur, ()):
                if nxt == dst_cf:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def _render_related(self, node: FunctionCallNode) -> str:
        if len(node.args) != 1 or not isinstance(node.args[0], ColumnRefNode) or not node.args[0].table:
            raise self.DaxRenderError("RELATED requires a single Table[Column] argument")
        target = node.args[0].table
        src = self.row_context_stack[-1] if self.row_context_stack else self.primary_table_name
        if self.relationships is not None and src and not self._has_many_to_one_path(src, target):
            raise self.DaxRenderError(
                f"not applicable: RELATED('{target}') has no active many-to-one relationship path from '{src}'"
            )
        return self._render_column_ref(node.args[0])

    def _render_countblank(self, node: FunctionCallNode) -> str:
        if len(node.args) != 1 or not isinstance(node.args[0], ColumnRefNode):
            raise self.DaxRenderError("COUNTBLANK requires a single column argument")
        col = node.args[0]
        tbl = col.table or self.primary_table_name
        if tbl and not self._is_primary_table(tbl):
            raise self.DaxRenderError(
                f"not applicable: COUNTBLANK over '{tbl}' counts rows of a different table than this metric's fact table"
            )
        col_type = self._column_data_type(tbl, col.column)
        if col_type not in ("num", "date", "bool"):
            raise self.DaxRenderError(
                "declined: COUNTBLANK on a text or untyped column (empty-string handling) is not translated"
            )
        col_sql = self._render_column_ref(col)
        # DAX returns BLANK (not 0) when no blank rows are found.
        return f"NULLIF(COUNT(*) - COUNT({col_sql}), 0)"

    def _render_date_bound(self, node: DaxNode, func_name: str) -> Optional[str]:
        if isinstance(node, FunctionCallNode):
            fn = node.func.upper()
            if fn == "BLANK" and not node.args:
                return None
            if fn == "DATE" and len(node.args) == 3 and all(
                isinstance(a, LiteralNode) and isinstance(a.value, int) and not isinstance(a.value, bool)
                for a in node.args
            ):
                y, m, d = (a.value for a in node.args)
                import datetime as _dt
                try:
                    _dt.date(y, m, d)
                except ValueError:
                    raise self.DaxRenderError(f"not applicable: invalid DATE({y},{m},{d}) in {func_name}")
                return f"DATE '{y:04d}-{m:02d}-{d:02d}'"
            if fn in ("TODAY", "NOW") and not node.args:
                return self.dialect.current_date()
        raise self.DaxRenderError(
            f"not applicable: {func_name} bounds must be literal dates (DATE(y,m,d)), TODAY() or BLANK()"
        )

    def _render_datesbetween(self, node: FunctionCallNode) -> str:
        if len(node.args) != 3:
            raise self.DaxRenderError("DATESBETWEEN requires 3 arguments")
        d_sql = self._render_node(node.args[0])
        lo = self._render_date_bound(node.args[1], "DATESBETWEEN")
        hi = self._render_date_bound(node.args[2], "DATESBETWEEN")
        conds = []
        if lo:
            conds.append(f"{d_sql} >= {lo}")
        if hi:
            conds.append(f"{d_sql} <= {hi}")
        return " AND ".join(conds) if conds else "TRUE"

    def _render_datesinperiod(self, node: FunctionCallNode) -> str:
        if len(node.args) != 4:
            raise self.DaxRenderError("DATESINPERIOD requires 4 arguments")
        dates, start, n_node, unit_node = node.args
        n = self._extract_shift_amount(n_node)
        unit = str(getattr(unit_node, "name", getattr(unit_node, "value", "")) or "").strip("'\" ").upper()
        if unit not in ("DAY", "MONTH", "QUARTER", "YEAR"):
            raise self.DaxRenderError(f"not applicable: DATESINPERIOD interval '{unit}' not supported")
        d_sql = self._render_node(dates)
        anchor = None
        if isinstance(start, FunctionCallNode) and start.func.upper() in ("MAX", "LASTDATE") and start.args:
            # Last visible date of the filter context.
            if n < 0 and unit == "MONTH":
                flag = self.anchor_flag_map.get((f"R{abs(n)}M",)) if self.anchor_flag_map else None
                if flag:
                    return self.dialect.qualify(self.table_alias, flag)
            anchor = self.dialect.current_date()
        else:
            anchor = self._render_date_bound(start, "DATESINPERIOD")
            if anchor is None:
                raise self.DaxRenderError("not applicable: DATESINPERIOD start date is BLANK()")
        if n == 0:
            return "FALSE"
        if n < 0:
            return f"{d_sql} > {self.dialect.date_add(unit, n, anchor)} AND {d_sql} <= {anchor}"
        return f"{d_sql} >= {anchor} AND {d_sql} < {self.dialect.date_add(unit, n, anchor)}"

    _ALL_LIKE_FUNCS = ("ALL", "ALLEXCEPT", "REMOVEFILTERS", "ALLNOBLANKROW", "ALLSELECTED")

    def _render_calc_filter_predicate(self, f: DaxNode) -> str:
        """Row-level SQL predicate for one CALCULATE filter argument that is a
        boolean filter (column comparison / IN / NOT / ISBLANK / KEEPFILTERS /
        FILTER(table, pred) / DATESBETWEEN / DATESINPERIOD)."""
        if isinstance(f, (BinaryOpNode, UnaryOpNode)):
            return self._render_node(f)
        if isinstance(f, FunctionCallNode):
            fn = f.func.upper()
            if fn == "KEEPFILTERS":
                if not f.args:
                    raise self.DaxRenderError("KEEPFILTERS requires 1 argument")
                return self._render_calc_filter_predicate(f.args[0])
            if fn == "FILTER" and len(f.args) >= 2:
                t = f.args[0]
                if isinstance(t, FunctionCallNode) and t.func.upper() in self._ALL_LIKE_FUNCS:
                    tbl_name = self._extract_table_name(t.args[0]) if t.args else None
                    if not tbl_name or self._is_primary_table(tbl_name):
                        raise self.DaxRenderError(
                            "not applicable: FILTER(ALL(<fact table>), ...) removes every filter on the fact table "
                            "and is not expressible as a row-level CASE filter"
                        )
                iter_tbl = self._extract_table_name(t)
                with self._row_context(iter_tbl):
                    return self._render_node(f.args[1])
            if fn in ("NOT", "ISBLANK", "TRUE", "FALSE"):
                return self._render_node(f)
            if fn == "DATESBETWEEN":
                return self._render_datesbetween(f)
            if fn == "DATESINPERIOD":
                return self._render_datesinperiod(f)
            if fn == "USERELATIONSHIP":
                raise self.DaxRenderError(USERELATIONSHIP_REASON)
            raise self.DaxRenderError(f"not applicable: CALCULATE filter modifier {fn} is not supported")
        raise self.DaxRenderError(
            f"CALCULATE filter argument of type {type(f).__name__} "
            "is not a recognized filter modifier or comparison predicate"
        )

    def _render_measure_ref(self, node: MeasureRefNode) -> str:

        var_sql = self._lookup_var(node.name)

        if var_sql is not None:

            return var_sql

        name_cf = node.name.casefold()
        if name_cf in self._measure_sql_map_by_casefold:
            val = self._measure_sql_map_by_casefold[name_cf]
            if self.rules.policies.is_placeholder(val):
                raise self.DaxRenderError(f"depends on untranslatable part: [{node.name}]")
            if val.strip().upper() == "NULL":
                return "NULL"
            if self.row_context_stack:
                return self._row_level_measure_sql(node.name, val)
            if (
                getattr(self, "_comparison_context", None)
                and self._comparison_context.get("target_name")
                and name_cf == self._comparison_context["target_name"].casefold()
                and not getattr(self, "_in_shifted_calculate", 0)
            ):
                curr_flag = self._comparison_context["curr_flag"]
                filtered = self._inject_case_filter_into_rendered_aggregate(
                    val, self.dialect.qualify(self.table_alias, curr_flag)
                )
                if filtered:
                    return f"({filtered})"
            return f"({val})"

        if name_cf in self._known_measure_names_casefold:

            # The caller's model tracks this as a real measure name, so

            # rendering it as a column would reference a physical column

            # that doesn't exist (Bug: measure-references-measure leaking

            # as a column reference). Fail closed so the caller can defer

            # to a later pass with fuller measure context, instead of

            # "succeeding" with wrong SQL.

            raise self.DaxRenderError(

                f"Unresolved measure reference [{node.name}]: known measure with no resolved SQL yet"

            )

        # The caller has no measure-registry knowledge of this name at all —

        # the common case is a bare [Column] used as an aggregation

        # argument (e.g. SUM([Amount])), where the parser cannot distinguish

        # a column from a measure at the token level. Falling back to a

        # column reference here preserves that long-standing, widely-relied

        # upon shape; it is only wrong for genuine unresolved measure

        # references, which are caught by the branch above instead.

        # Priority: innermost iterated table > home table
        if self.row_context_stack:
            target_table = self.row_context_stack[-1]
            alias = self._resolve_table_alias(target_table)
        else:
            target_table = self.primary_table_name or self.table_alias
            alias = self.table_alias

        known_cols = None
        if getattr(self, "dataset_col_lookup", None):
            for ds_k, cols in self.dataset_col_lookup.items():
                al = (self.table_aliases.get(ds_k) or ds_k).upper() if getattr(self, "table_aliases", None) else ds_k.upper()
                if al == alias.upper() or ds_k.upper() == str(target_table).upper() or ds_k.upper() == alias.upper():
                    known_cols = {c.upper() for c in cols}
                    break
        if known_cols is None and getattr(self, "datasets", None):
            for ds in self.datasets:
                ds_name = getattr(ds, "unique_name", getattr(ds, "name", ""))
                al = (self.table_aliases.get(ds_name) or ds_name).upper() if getattr(self, "table_aliases", None) else ds_name.upper()
                if al == alias.upper() or ds_name.upper() == str(target_table).upper() or ds_name.upper() == alias.upper():
                    known_cols = {getattr(c, "unique_name", getattr(c, "name", "")).upper() for c in (getattr(ds, "columns", []) or [])}
                    break

        if known_cols is not None:
            node_clean = re.sub(r'[^A-Z0-9]', '', node.name.upper())
            matching_col = None
            for kc in known_cols:
                if re.sub(r'[^A-Z0-9]', '', kc) == node_clean:
                    matching_col = kc
                    break
            if not matching_col:
                raise self.DaxRenderError(
                    f"Unresolved column or measure reference [{node.name}] in table '{alias}'"
                )
            col = sanitize_column(matching_col)
            return self.dialect.qualify(alias, col)

        col = sanitize_column(node.name)
        return self.dialect.qualify(alias, col)



    # ------------------------------------------------------------------
    # DAX comparison semantics (BLANK rules)
    # ------------------------------------------------------------------
    _CMP_FLIP = {"=": "=", "<>": "<>", "<": ">", ">": "<", "<=": ">=", ">=": "<="}

    def _literal_kind(self, node: Optional[DaxNode]) -> Optional[Tuple[str, Any]]:
        """('num'|'text'|'bool'|'blank', python value) for a literal operand."""
        if isinstance(node, LiteralNode):
            if isinstance(node.value, bool):
                return ("bool", node.value)
            if isinstance(node.value, (int, float)):
                return ("num", node.value)
            if isinstance(node.value, str):
                return ("text", node.value)
        if isinstance(node, UnaryOpNode) and node.op == "-" and isinstance(node.operand, LiteralNode):
            if isinstance(node.operand.value, (int, float)) and not isinstance(node.operand.value, bool):
                return ("num", -node.operand.value)
        if isinstance(node, FunctionCallNode) and not node.args:
            fn = node.func.upper()
            if fn == "BLANK":
                return ("blank", None)
            if fn in ("TRUE", "FALSE"):
                return ("bool", fn == "TRUE")
        return None

    def _column_data_type(self, table: Optional[str], column: str) -> Optional[str]:
        """'num'|'text'|'bool'|'date' from the model's dataset metadata, or None."""
        datasets = self.datasets or getattr(self.model_metadata, "datasets", None) or []
        tbl_cf = str(table or "").strip("'\"").casefold()
        col_cf = str(column or "").strip("[]").casefold()
        col_san = sanitize_column(col_cf).casefold() if col_cf else ""
        for ds in datasets:
            names = {
                str(getattr(ds, a, "") or "").strip("'\"").casefold()
                for a in ("unique_name", "name", "label")
            }
            names |= {to_alias(n).casefold() for n in list(names) if n}
            if tbl_cf and tbl_cf not in names and to_alias(tbl_cf).casefold() not in names:
                continue
            for col in getattr(ds, "columns", []) or []:
                cn = {str(getattr(col, a, "") or "").casefold() for a in ("unique_name", "name", "label")}
                if col_cf not in cn and col_san not in {sanitize_column(c).casefold() for c in cn if c}:
                    continue
                dt = getattr(col, "data_type", None)
                dt = str(getattr(dt, "value", dt) or "").lower()
                explicit = "data_type" in (getattr(col, "model_fields_set", None) or {"data_type"})
                if dt in ("integer", "decimal", "float", "int", "double", "number", "int64", "currency"):
                    return "num"
                if dt in ("boolean", "bool"):
                    return "bool"
                if dt in ("date", "datetime", "time", "timestamp"):
                    return "date"
                if dt in ("string", "text") and (explicit or getattr(col, "source_type", "")):
                    return "text"
                return None
            if tbl_cf:
                return None
        return None

    _NUMERIC_RESULT_FUNCS = frozenset({
        "SUM", "AVERAGE", "COUNT", "COUNTA", "COUNTROWS", "COUNTBLANK", "DISTINCTCOUNT",
        "DISTINCTCOUNTNOBLANK", "SUMX", "AVERAGEX", "COUNTX", "COUNTAX", "DIVIDE", "ABS", "ROUND",
        "ROUNDUP", "ROUNDDOWN", "INT", "VALUE", "TOTALYTD", "TOTALQTD", "TOTALMTD", "YEAR",
        "MONTH", "DAY", "QUARTER", "WEEKDAY", "WEEKNUM", "HOUR", "MINUTE", "SECOND", "DATEDIFF",
        "MROUND", "CEILING", "FLOOR", "LEN", "POWER", "SQRT", "MOD", "RANKX",
    })
    _DATE_RESULT_FUNCS = frozenset({"DATE", "TODAY", "NOW", "EOMONTH", "EDATE", "DATEVALUE", "LASTDATE", "FIRSTDATE"})
    _TEXT_RESULT_FUNCS = frozenset({"FORMAT", "CONCATENATE", "LEFT", "RIGHT", "MID", "UPPER", "LOWER", "TRIM", "SUBSTITUTE", "REPLACE"})

    def _expr_type(self, node: Optional[DaxNode], depth: int = 0) -> Optional[str]:
        if node is None or depth > 20:
            return None
        lk = self._literal_kind(node)
        if lk is not None:
            return None if lk[0] == "blank" else lk[0]
        if isinstance(node, MeasureRefNode):
            if self._lookup_var(node.name) is not None:
                var_ast = getattr(self, "_var_ast_map", {}).get(node.name.strip("[]").casefold())
                return self._expr_type(var_ast, depth + 1) if var_ast is not None else None
            if node.name.casefold() in self._measure_sql_map_by_casefold:
                return "num"
            tbl = self.row_context_stack[-1] if self.row_context_stack else self.primary_table_name
            return self._column_data_type(tbl, node.name)
        if isinstance(node, ColumnRefNode):
            if not node.table:
                if self._lookup_var(node.column) is not None:
                    var_ast = getattr(self, "_var_ast_map", {}).get(node.column.strip("[]").casefold())
                    return self._expr_type(var_ast, depth + 1) if var_ast is not None else None
                if node.column.strip("[]").casefold() in self._measure_sql_map_by_casefold:
                    return "num"
            tbl = node.table or (self.row_context_stack[-1] if self.row_context_stack else self.primary_table_name)
            return self._column_data_type(tbl, node.column)
        if isinstance(node, IdentifierNode):
            var_ast = getattr(self, "_var_ast_map", {}).get(str(node.name).strip("[]").casefold())
            return self._expr_type(var_ast, depth + 1) if var_ast is not None else None
        if isinstance(node, VarNode):
            return self._expr_type(node.body, depth + 1)
        if isinstance(node, UnaryOpNode):
            return "bool" if node.op.upper() == "NOT" else self._expr_type(node.operand, depth + 1)
        if isinstance(node, BinaryOpNode):
            if node.op in ("+", "-", "*", "/", "^"):
                return "num"
            if node.op == "&":
                return "text"
            return "bool"
        if isinstance(node, FunctionCallNode):
            fn = node.func.upper()
            if fn in self._NUMERIC_RESULT_FUNCS:
                return "num"
            if fn in self._DATE_RESULT_FUNCS:
                return "date"
            if fn in self._TEXT_RESULT_FUNCS:
                return "text"
            if fn in ("MIN", "MAX", "MINX", "MAXX", "CALCULATE", "COALESCE", "SELECTEDVALUE", "RELATED"):
                idx = 1 if fn in ("MINX", "MAXX") else 0
                if len(node.args) > idx:
                    t = self._expr_type(node.args[idx], depth + 1)
                    if t is None and fn == "CALCULATE":
                        return None
                    return t
            if fn in ("IF",) and len(node.args) >= 2:
                for a in node.args[1:]:
                    t = self._expr_type(a, depth + 1)
                    if t:
                        return t
            if fn in ("ISBLANK", "NOT", "AND", "OR", "HASONEVALUE", "ISFILTERED", "ISINSCOPE", "CONTAINSSTRING"):
                return "bool"
        return None

    def _negate(self, cond_sql: str) -> str:
        """DAX NOT over a predicate. A DAX comparison is never BLANK, so a
        SQL NULL produced by a comparison stands for DAX FALSE; NOT must turn
        it into TRUE (plain SQL NOT NULL is NULL, i.e. false)."""
        return f"NOT COALESCE({cond_sql}, FALSE)"

    @staticmethod
    def _blank_equiv_sql(kind: str) -> Tuple[str, Any]:
        return {"num": ("0", 0), "text": ("''", ""), "bool": ("FALSE", False)}[kind]

    @staticmethod
    def _py_compare(a: Any, op: str, b: Any) -> bool:
        try:
            if isinstance(a, str) and isinstance(b, str):
                a, b = a.casefold(), b.casefold()
            return {
                "=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b,
            }[op]
        except TypeError:
            return False

    def _render_in(self, node: BinaryOpNode) -> str:
        right = node.right
        if not (isinstance(right, FunctionCallNode) and right.func == TABLE_CONSTRUCTOR_FUNC):
            raise self.DaxRenderError("not applicable: IN over a table expression (only a literal list { ... } is supported)")
        items = []
        for item in right.args:
            lk = self._literal_kind(item)
            if lk is None:
                raise self.DaxRenderError("not applicable: IN list contains a non-literal value")
            if lk[0] == "blank" or (lk[0] in ("num", "text", "bool") and lk[1] == self._blank_equiv_sql(lk[0])[1]):
                raise self.DaxRenderError(
                    "not applicable: IN list containing a blank-equivalent value (BLANK(), 0, \"\" or FALSE) "
                    "has BLANK-matching semantics that are not translated"
                )
            items.append(self._render_node(item))
        if not items:
            return "FALSE"
        x_sql = self._render_node(node.left)
        return f"{x_sql} IN ({', '.join(items)})"

    def _render_comparison(self, node: BinaryOpNode) -> str:
        """Render a DAX comparison following DAX BLANK rules:
        BLANK = 0 / BLANK = "" / BLANK = FALSE are TRUE, and BLANK compares
        as 0 (numbers) or "" (text) under every relational operator."""
        op = node.op
        lk, rk = self._literal_kind(node.left), self._literal_kind(node.right)
        if lk is not None and rk is not None:
            if lk[0] == "blank" or rk[0] == "blank":
                other = rk if lk[0] == "blank" else lk
                if other[0] == "blank":
                    return "TRUE" if op in ("=", "<=", ">=") else "FALSE"
                beq = self._blank_equiv_sql(other[0])[1] if other[0] in ("num", "text", "bool") else None
                a, b = (beq, other[1]) if lk[0] == "blank" else (other[1], beq)
                return "TRUE" if self._py_compare(a, op, b) else "FALSE"
            return f"{self._render_node(node.left)} {op} {self._render_node(node.right)}"
        if lk is not None and rk is None:
            x_node, lit, op = node.right, lk, self._CMP_FLIP[op]
            lit_node = node.left
        elif rk is not None:
            x_node, lit, lit_node = node.left, rk, node.right
        else:
            # Neither side is a literal.
            left = self._render_node(node.left)
            right = self._render_node(node.right)
            if isinstance(node.left, BinaryOpNode) and node.left.op in self._CMP_FLIP:
                left = f"({left})"
            if isinstance(node.right, BinaryOpNode) and node.right.op in self._CMP_FLIP:
                right = f"({right})"
            lt, rt = self._expr_type(node.left), self._expr_type(node.right)
            if lt == "num" and rt == "num":
                left = self._safe_coalesce_operand(left, self._is_operand_nullable(node.left, left))
                right = self._safe_coalesce_operand(right, self._is_operand_nullable(node.right, right))
            elif lt == "text" and rt == "text":
                if self._is_operand_nullable(node.left, left):
                    left = f"COALESCE({left}, '')"
                if self._is_operand_nullable(node.right, right):
                    right = f"COALESCE({right}, '')"
            return f"{left} {op} {right}"

        x_sql = self._render_node(x_node)
        if isinstance(x_node, BinaryOpNode) and x_node.op in self._CMP_FLIP:
            x_sql = f"({x_sql})"
        if x_sql.strip().upper() in ("NULL", "(NULL)"):
            # A BLANK operand (e.g. an unresolvable parameter measure).
            if lit[0] == "blank":
                return "TRUE" if op in ("=", "<=", ">=") else "FALSE"
            beq_sql, _ = self._blank_equiv_sql(lit[0])
            return f"COALESCE({x_sql}, {beq_sql}) {op} {self._render_node(lit_node)}"
        if lit[0] == "blank":
            x_type = self._expr_type(x_node)
            if x_type in ("num", "text", "bool"):
                beq_sql, _ = self._blank_equiv_sql(x_type)
                return f"COALESCE({x_sql}, {beq_sql}) {op} {beq_sql}"
            if x_type == "date" and op in ("=", "<>"):
                return f"{x_sql} IS NULL" if op == "=" else f"{x_sql} IS NOT NULL"
            raise self.DaxRenderError(
                "declined: comparison with BLANK() on an expression whose data type is unknown "
                "(DAX treats BLANK as equal to 0 for numbers and to \"\" for text); "
                "use ISBLANK() for a pure blank test"
            )
        lit_sql = self._render_node(lit_node)
        beq_sql, beq_val = self._blank_equiv_sql(lit[0])
        if self._py_compare(beq_val, op, lit[1]) and self._is_operand_nullable(x_node, x_sql):
            return f"COALESCE({x_sql}, {beq_sql}) {op} {lit_sql}"
        return f"{x_sql} {op} {lit_sql}"

    def _render_binary(self, node: BinaryOpNode) -> str:
        if node.op.upper() == "IN":
            return self._render_in(node)
        if node.op == "&":
            raise self.DaxRenderError("not applicable: text result (& concatenation)")
        if node.op in self._CMP_FLIP:
            return self._render_comparison(node)
        left = self._render_node(node.left)
        right = self._render_node(node.right)

        op_clean = node.op.upper()

        parent_prec = self._PRECEDENCE.get(op_clean, 0)



        if isinstance(node.left, BinaryOpNode):

            left_prec = self._PRECEDENCE.get(node.left.op.upper(), 0)

            if left_prec < parent_prec:

                left = f"({left})"



        if isinstance(node.right, BinaryOpNode):

            right_prec = self._PRECEDENCE.get(node.right.op.upper(), 0)

            if right_prec < parent_prec or (op_clean in ("-", "/") and right_prec <= parent_prec):

                right = f"({right})"



        op_map = {

            "=": "=", "<>": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">=",

            "+": "+", "-": "-", "*": "*", "/": "/",

            "AND": "AND", "OR": "OR",

        }

        sql_op = op_map.get(op_clean, node.op)

        def _needs_coalesce_zero(expr_str: str) -> bool:
            s = expr_str.strip()
            while s.startswith("(") and s.endswith(")"):
                s = s[1:-1].strip()
            if s.upper() == "NULL":
                return True
            if re.search(r'\*\s*\(?\s*NULL\s*\)?\b', expr_str, re.IGNORECASE):
                return True
            return False

        if sql_op in ("AND", "OR"):
            return f"({left}) {sql_op} ({right})"

        def _is_numeric_literal(n: Optional[DaxNode], s: str) -> bool:
            if n is not None and isinstance(n, LiteralNode):
                try:
                    float(str(n.value).strip())
                    return True
                except (ValueError, TypeError):
                    pass
            clean_s = (s or "").strip()
            while clean_s.startswith("(") and clean_s.endswith(")"):
                clean_s = clean_s[1:-1].strip()
            try:
                float(clean_s)
                return True
            except (ValueError, TypeError):
                return False

        if sql_op in ("+", "-"):
            is_left_nullable = self._is_operand_nullable(node.left, left)
            is_right_nullable = self._is_operand_nullable(node.right, right)

            if not is_left_nullable and not is_right_nullable:
                return f"{left} {sql_op} {right}"

            clean_left = self._unwrap_str(left)
            clean_right = self._unwrap_str(right)

            left_cond = f"({clean_left})"
            right_cond = f"({clean_right})"

            coalesced_l = self._safe_coalesce_operand(left, is_left_nullable)
            coalesced_r = self._safe_coalesce_operand(right, is_right_nullable)

            return f"CASE WHEN {left_cond} IS NULL AND {right_cond} IS NULL THEN NULL ELSE {coalesced_l} {sql_op} {coalesced_r} END"

        if sql_op in ("=", "<>", "<", "<=", ">", ">="):
            if _needs_coalesce_zero(left):
                left = f"COALESCE({left}, 0)"
            if _needs_coalesce_zero(right):
                right = f"COALESCE({right}, 0)"
            return f"{left} {sql_op} {right}"

        if sql_op == "/":
            left = _wrap_parens_for_op(left)
            right_expr = right
            if right_expr.startswith("(") and right_expr.endswith(")"):
                depth = 0
                balanced = True
                for ch in right_expr[:-1]:
                    if ch == "(": depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            balanced = False
                            break
                if balanced:
                    right_expr = right_expr[1:-1]
            if right_expr.strip().upper() == "NULL":
                return f"{left} / NULL"
            if left.strip().upper() == "NULL":
                return "NULL"
            if "PARTITION BY EXCLUDING" in right_expr:
                if isinstance(node.left, MeasureRefNode):
                    left = f'{self.table_alias.upper()}."{sanitize_column(node.left.name)}"'
                elif isinstance(node.left, FunctionCallNode):
                    rendered_left = self._render_node(node.left)
                    for m_k, m_sql in self.measure_sql_map.items():
                        if m_sql.strip().upper() in (rendered_left.strip().upper(), f"({rendered_left.strip().upper()})"):
                            left = f'{self.table_alias.upper()}."{sanitize_column(m_k)}"'
                            break
            if self._is_nonzero_constant(node.right, right_expr) or right_expr.strip().upper().startswith("NULLIF("):
                return f"{left} / {right_expr}"
            return f"{left} / NULLIF({right_expr}, 0)"

        return f"{left} {sql_op} {right}"



    def _render_unary(self, node: UnaryOpNode) -> str:

        inner = self._render_node(node.operand)

        if node.op == "-":

            return f"(-{inner})"

        if node.op.upper() == "NOT":
            if isinstance(node.operand, FunctionCallNode) and node.operand.func.upper() == "ISBLANK":
                if len(node.operand.args) == 1:
                    arg_sql = self._render_node(node.operand.args[0])
                    return f"({arg_sql} IS NOT NULL)"
            return self._negate(f"({inner})")

        return inner



    def _render_function(self, node: FunctionCallNode) -> str:

        func = node.func.upper()



        if func in ("TRUE", "FALSE"):

            return func



        # CALCULATE (lazy/CASE-based evaluation of filters)

        if func == "CALCULATE":

            return self._render_calculate(node.args)



        # Time Intelligence (lazy OVER/window/LAG function rendering)

        if func in {"TOTALYTD", "TOTALMTD", "TOTALQTD"}:

            return self._render_period_to_date(func, node.args)



        if func in {"SAMEPERIODLASTYEAR", "PREVIOUSYEAR"}:

            return self._render_lag_period(node.args, interval="year", amount=-1, func_name=func)



        if func == "PREVIOUSMONTH":

            return self._render_lag_period(node.args, interval="month", amount=-1, func_name=func)



        if func == "PREVIOUSQUARTER":

            return self._render_lag_period(node.args, interval="quarter", amount=-1, func_name=func)



        if func == "DATEADD":

            return self._render_dateadd(node.args)



        # Iterators (SUMX, AVERAGEX, MINX, MAXX, COUNTAX)

        if func in self._ITERATOR_MAP:

            if len(node.args) < 2:

                raise self.DaxRenderError(f"Iterator {func} requires at least 2 arguments")

            sql_func = self._ITERATOR_MAP[func]

            table_arg = node.args[0]

            expr_arg = node.args[1]

            # Iteration grain check:
            # Table / FILTER(T) / KEEPFILTERS(T) -> row grain (unroll allowed).
            # VALUES / DISTINCT / SUMMARIZE / ALL(T[c]) -> group grain -> not one metric -> NULL
            chk_arg = table_arg
            while isinstance(chk_arg, FunctionCallNode) and chk_arg.func.upper() in ("KEEPFILTERS", "FILTER"):
                chk_arg = chk_arg.args[0] if chk_arg.args else None

            if isinstance(chk_arg, FunctionCallNode):
                fn_grain = chk_arg.func.upper()
                if fn_grain in ("VALUES", "DISTINCT", "SUMMARIZE", "SUMMARIZECOLUMNS"):
                    col_desc = ""
                    if chk_arg.args:
                        col_desc = getattr(chk_arg.args[0], "column", getattr(chk_arg.args[0], "name", str(chk_arg.args[0])))
                    raise self.DaxRenderError(
                        f"not applicable: {func} over {fn_grain}({col_desc or 'dimension values'}) evaluates its "
                        "expression once per distinct value (context transition) and then re-aggregates; "
                        "this multi-level aggregation is not expressible as a single semantic-view metric"
                    )
                if fn_grain in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS") and chk_arg.args:
                    if isinstance(chk_arg.args[0], ColumnRefNode) or "[" in str(chk_arg.args[0]):
                        col_desc = getattr(chk_arg.args[0], "column", str(chk_arg.args[0]))
                        # Special case: MAXX/MINX over ALL(T[c]) where expression is the column itself
                        # is a scalar MAX/MIN of the column, not a group aggregation.
                        is_same_col = False
                        if func in ("MINX", "MAXX") and isinstance(expr_arg, ColumnRefNode):
                            col_names = [getattr(a, "column", "").upper() for a in chk_arg.args]
                            if expr_arg.column.upper() in col_names:
                                is_same_col = True
                        if not is_same_col:
                            raise self.DaxRenderError(
                                f"BY_DESIGN_EXCLUDED: aggregation over groups of {col_desc} "
                                "requires multi-level grouping not supported in a single semantic metric"
                            )

            # Check if first arg is FILTER(table, predicate)
            if isinstance(table_arg, FunctionCallNode) and table_arg.func.upper() == "FILTER":
                if len(table_arg.args) < 2:
                    raise self.DaxRenderError("FILTER requires table and predicate arguments")
                filter_tbl_node = table_arg.args[0]
                filter_tbl_name = self._extract_table_name(filter_tbl_node) or self._extract_table_name(table_arg)
                with self._row_context(filter_tbl_name):
                    pred_sql = self._render_node(table_arg.args[1])
                    expr_sql = self._render_node(expr_arg)

                if func == "COUNTAX" and expr_sql in ("TRUE", "true", "True", "1"):
                    case_expr = f"CASE WHEN {pred_sql} THEN 1 END"
                else:
                    case_expr = f"CASE WHEN {pred_sql} THEN {expr_sql} END"
                cast = self.dialect.float_cast_suffix if sql_func in ("SUM", "AVG") else ""
                return f"{sql_func}({case_expr}{cast})"

            iter_tbl = self._extract_table_name(table_arg)
            with self._row_context(iter_tbl):
                expr_sql = self._render_node(expr_arg)

            if func == "COUNTAX" and expr_sql in ("TRUE", "true", "True", "1"):

                return "COUNT(*)"

            if func == "AVERAGEX" and expr_sql:

                cleaned = expr_sql.strip()

                while cleaned.startswith("(") and cleaned.endswith(")"):

                    depth = 0

                    encloses = False

                    for idx, ch in enumerate(cleaned):

                        if ch == "(":

                            depth += 1

                        elif ch == ")":

                            depth -= 1

                            if depth == 0:

                                encloses = (idx == len(cleaned) - 1)

                                break

                    if encloses:

                        cleaned = cleaned[1:-1].strip()

                    else:

                        break

                cleaned = re.sub(rf"{self._float_cast_re}$", "", cleaned, flags=re.IGNORECASE).strip()

                sum_unroll = re.match(r"^SUM\s*\((.*)\)$", cleaned, re.IGNORECASE | re.DOTALL)

                if sum_unroll:

                    inner_row_expr = sum_unroll.group(1).strip()

                    inner_row_expr = re.sub(rf"{self._float_cast_re}$", "", inner_row_expr, flags=re.IGNORECASE).strip()

                    return f"AVG({inner_row_expr})"

            cast = self.dialect.float_cast_suffix if sql_func in ("SUM", "AVG") else ""

            return f"{sql_func}({expr_sql}{cast})"



        if func == "KEEPFILTERS":
            if not node.args:
                raise self.DaxRenderError("KEEPFILTERS requires 1 argument")
            return self._render_node(node.args[0])
        if func == "USERELATIONSHIP":
            raise self.DaxRenderError(USERELATIONSHIP_REASON)
        if func == TABLE_CONSTRUCTOR_FUNC:
            raise self.DaxRenderError("not applicable: table constructor { ... } used as a value")
        if func == "COALESCE":
            if len(node.args) < 2:
                raise self.DaxRenderError("COALESCE requires at least 2 arguments")
            return "COALESCE(" + ", ".join(self._render_node(a) for a in node.args) + ")"
        if func == "RELATED":
            return self._render_related(node)
        if func == "COUNTBLANK":
            return self._render_countblank(node)



        if func == "NOT":

            if len(node.args) != 1:

                raise self.DaxRenderError("NOT requires 1 argument")

            inner = node.args[0]

            if isinstance(inner, FunctionCallNode) and inner.func.upper() == "ISBLANK":

                if len(inner.args) != 1:

                    raise self.DaxRenderError("ISBLANK requires 1 argument")

                arg_sql = self._render_node(inner.args[0])

                return f"({arg_sql} IS NOT NULL)"

            arg_sql = self._render_node(inner)
            return self._negate(f"({arg_sql})")



        if func == "DATEDIFF":

            if len(node.args) != 3:

                raise self.DaxRenderError("DATEDIFF requires 3 arguments (start_date, end_date, interval)")

            start_sql = self._render_node(node.args[0])

            end_sql = self._render_node(node.args[1])

            interval_node = node.args[2]

            interval_str = getattr(interval_node, "name", None) or getattr(interval_node, "raw", None) or str(interval_node)

            interval_clean = interval_str.strip("'\" ").upper()

            return self.dialect.date_diff(interval_clean, start_sql, end_sql)



        # Aggregations (SUM, AVERAGE, MIN, MAX, COUNT, etc.)

        if func in self._AGG_MAP:

            if func == "COUNTROWS":

                return self._render_countrows(node)

            if not node.args:

                raise self.DaxRenderError(f"Aggregation {func} requires at least 1 argument")

            return self._render_aggregation(func, node.args[0])



        # IF
        if func == "IF":
            return self._render_if(node.args)

        # TREATAS
        if func == "TREATAS":
            raise self.DaxRenderError("not applicable: virtual relationship (TREATAS) not supported in semantic views")

        # Registry-driven templates (if configured)

        rendered_args = [self._render_node(a) for a in node.args]

        if func == "DIVIDE":

            return self._render_divide(node.args)



        registry_sql = self._func_registry.render(

            func,

            rendered_args,

            table_alias=self.table_alias,

            date_alias=self.date_alias,

        )

        if registry_sql:

            return registry_sql



        # IF

        if func == "IF":

            return self._render_if(node.args)



        # SWITCH

        if func == "SWITCH":

            return self._render_switch(node.args)



        # IFERROR

        if func == "IFERROR":

            return self._render_iferror(node.args)



        # ISBLANK

        if func == "ISBLANK":

            if len(node.args) != 1:

                raise self.DaxRenderError("ISBLANK requires 1 argument")

            arg_sql = self._render_node(node.args[0])

            return f"({arg_sql} IS NULL)"



        # BLANK() -> SQL NULL (matches ISBLANK's own "x IS NULL" mapping

        # just above, and the same BLANK()->NULL rule already documented

        # for Tier 5's prompt — just never implemented in this renderer).

        if func == "BLANK":

            return "NULL"



        # CONCATENATE

        if func == "CONCATENATE":

            if len(node.args) >= 2:

                left = self._render_node(node.args[0])

                right = self._render_node(node.args[1])

                return f"CONCAT({left}, {right})"



        # VALUE -> TRY_TO_NUMBER

        if func == "VALUE":

            if not node.args:

                raise self.DaxRenderError("VALUE requires at least 1 argument")

            arg_sql = self._render_node(node.args[0])

            return self.dialect.try_to_number(arg_sql)



        # LEFT / RIGHT (optional length defaults to 1 in DAX)

        if func in ("LEFT", "RIGHT"):

            if not node.args:

                raise self.DaxRenderError(f"{func} requires at least 1 argument")

            s = self._render_node(node.args[0])

            n = self._render_node(node.args[1]) if len(node.args) > 1 else "1"

            return f"{func}({s}, {n})"



        # MID -> SUBSTR (1-based, same as Snowflake)

        if func == "MID":

            if len(node.args) < 3:

                raise self.DaxRenderError("MID requires 3 arguments")

            s = self._render_node(node.args[0])

            start = self._render_node(node.args[1])

            num = self._render_node(node.args[2])

            return f"SUBSTR({s}, {start}, {num})"



        # MROUND

        if func == "MROUND":

            if len(node.args) < 2:

                raise self.DaxRenderError("MROUND requires 2 arguments")

            x_sql = self._render_node(node.args[0])

            n_sql = self._render_node(node.args[1])

            return f"ROUND(({x_sql}) / ({n_sql})) * ({n_sql})"



        # ROUNDUP / ROUNDDOWN (negative-aware away-from-zero / towards-zero)

        if func in ("ROUNDUP", "ROUNDDOWN"):

            if not node.args:

                raise self.DaxRenderError(f"{func} requires at least 1 argument")

            x_sql = self._render_node(node.args[0])

            sql_fn = "CEIL" if func == "ROUNDUP" else "FLOOR"

            if len(node.args) >= 2:

                d_sql = self._render_node(node.args[1])

                return f"SIGN({x_sql}) * {sql_fn}(ABS({x_sql}), {d_sql})"

            return f"SIGN({x_sql}) * {sql_fn}(ABS({x_sql}))"



        # SELECTEDVALUE(col, [alt])

        if func == "SELECTEDVALUE":

            if not node.args:

                raise self.DaxRenderError("SELECTEDVALUE requires at least 1 argument")

            col_sql = self._render_node(node.args[0])

            alt_sql = self._render_node(node.args[1]) if len(node.args) > 1 else "NULL"

            is_null_flag = self.dialect.iff(f"{col_sql} IS NULL", "1", "0")
            return self.dialect.iff(
                f"COUNT(DISTINCT {col_sql}) + MAX({is_null_flag}) = 1", f"MAX({col_sql})", alt_sql
            )



        # String and math functions with direct Snowflake equivalents

        if func in ("LASTDATE", "FIRSTDATE"):
            agg_name = "MAX" if func == "LASTDATE" else "MIN"
            if node.args:
                arg_sql = self._render_node(node.args[0])
                return f"{agg_name}({arg_sql})"

        _direct_sql = {

            "LEN": "LENGTH", "TRIM": "TRIM",

            "UPPER": "UPPER", "LOWER": "LOWER",

            "YEAR": "YEAR", "MONTH": "MONTH", "DAY": "DAY",

            "HOUR": "HOUR", "MINUTE": "MINUTE", "SECOND": "SECOND",

            "ABS": "ABS", "CEILING": "CEIL", "FLOOR": "FLOOR",

            "ROUND": "ROUND",

            "INT": "FLOOR", "NOW": self.dialect.current_timestamp(), "TODAY": self.dialect.current_date(),

        }

        if func in _direct_sql:

            sql_func = _direct_sql[func]

            rendered_args = ", ".join(self._render_node(a) for a in node.args)

            if not node.args:

                return f"{sql_func}"

            return f"{sql_func}({rendered_args})"



        # Unsupported — raise so caller can fall back

        raise self.DaxRenderError(f"Unsupported DAX function: {func}")



    # ------------------------------------------------------------------

    # Helpers for specific patterns

    # ------------------------------------------------------------------



    def _render_countrows(self, node: FunctionCallNode) -> str:
        if not node.args:
            return "COUNT(*)"
        arg0 = node.args[0]
        # 1. IdentifierNode (table name): e.g. COUNTROWS('Orders')
        if isinstance(arg0, IdentifierNode):
            name = arg0.name.strip("'\"")
            if "[" in name or "." in name:
                raise self.DaxRenderError(f"non-table argument to COUNTROWS: '{arg0.name}'")
            return "COUNT(*)"

        if isinstance(arg0, FunctionCallNode):
            fn = arg0.func.upper()
            if fn == "KEEPFILTERS" and arg0.args:
                arg0 = arg0.args[0]
                fn = getattr(arg0, "func", "").upper() if isinstance(arg0, FunctionCallNode) else ""
                if isinstance(arg0, IdentifierNode):
                    return "COUNT(*)"

            # 2. FILTER(T, cond): COUNT(CASE WHEN <cond> THEN 1 END)
            if fn == "FILTER" and len(arg0.args) >= 2:
                iter_tbl = self._extract_table_name(arg0.args[0])
                with self._row_context(iter_tbl):
                    cond_sql = self._render_node(arg0.args[1])
                return f"COUNT(CASE WHEN {cond_sql} THEN 1 END)"

            # 3. VALUES(T[c]) or DISTINCT(T[c]): COUNT(DISTINCT c)
            if fn in ("VALUES", "DISTINCT") and arg0.args:
                col_arg = arg0.args[0]
                if isinstance(col_arg, ColumnRefNode):
                    col_sql = self._render_column_ref(col_arg)
                    return f"COUNT(DISTINCT {col_sql})"
                col_sql = self._render_node(col_arg)
                return f"COUNT(DISTINCT {col_sql})"

            if fn in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS"):
                if not arg0.args:
                    return "COUNT(*)"
                first = arg0.args[0]
                if isinstance(first, IdentifierNode):
                    return "COUNT(*)"
                elif isinstance(first, ColumnRefNode):
                    col_sql = self._render_column_ref(first)
                    return f"COUNT(DISTINCT {col_sql})"

        raise self.DaxRenderError(
            f"non-table argument to COUNTROWS: cannot count rows of {type(arg0).__name__}"
        )

    def _render_aggregation(self, func: str, arg: DaxNode) -> str:
        # Fan-out check: Never aggregate a dimension column with SUM/AVG/COUNT on the fact table.
        # Must not fire for a table's own columns, measure-only tables, or COUNT(DISTINCT)/MIN/MAX.
        if func.upper() in ("SUM", "AVG", "AVERAGE", "AVERAGEX", "COUNT", "COUNTA"):
            is_measure_table = (self.table_alias or "").upper() in ("METRICS", "MEASURES", "_MEASURES", "KEY_MEASURES", "ALL_MEASURES")
            if not is_measure_table:
                dim_table = None
                dim_col = None
                if isinstance(arg, ColumnRefNode):
                    t = (arg.table or "").strip("'\"")
                    if t and t.casefold() != (self.table_alias or "").casefold():
                        dim_table = t
                        dim_col = arg.column
                    elif not t and arg.column:
                        for rel in (self.relationships or []):
                            to_tbl = getattr(rel, "to_dataset", None) or getattr(rel, "to_table", None)
                            from_tbl = getattr(rel, "from_dataset", None) or getattr(rel, "from_table", None)
                            if to_tbl and from_tbl and from_tbl.casefold() == (self.table_alias or "").casefold():
                                prefix = f"{to_tbl}_".casefold()
                                if arg.column.casefold().startswith(prefix):
                                    dim_table = to_tbl
                                    dim_col = arg.column[len(prefix):]
                                    break

                if dim_table and dim_col:
                    is_dim = False
                    for rel in (self.relationships or []):
                        to_tbl = getattr(rel, "to_dataset", None) or getattr(rel, "to_table", None)
                        from_tbl = getattr(rel, "from_dataset", None) or getattr(rel, "from_table", None)
                        if to_tbl and from_tbl:
                            if to_tbl.casefold() == dim_table.casefold() and from_tbl.casefold() == (self.table_alias or "").casefold():
                                is_dim = True
                                break
                    if is_dim:
                        raise self.DaxRenderError(
                            f"BY_DESIGN_EXCLUDED: aggregating dimension column '{dim_table}[{dim_col}]' "
                            f"with {func.upper()} on fact table '{self.table_alias}' causes fan-out duplication "
                            "(measure should aggregate on dimension table directly)"
                        )

        col_sql = self._render_node(arg)

        template = self._AGG_MAP[func]

        

        # Snowflake: cast to FLOAT for SUM/AVG to handle BOOLEAN columns safely

        cast = self.dialect.float_cast_suffix if func in ("SUM", "AVERAGE", "AVERAGEX", "AVG") else ""

        

        if "{col}" in template:

            return template.format(col=f"{col_sql}{cast}")

        return f"{template}({col_sql}{cast})"



    def _is_nonzero_constant(self, node: Optional[DaxNode], rendered_sql: str) -> bool:
        if node is not None and isinstance(node, LiteralNode):
            try:
                val = float(str(node.value).strip())
                return val != 0.0
            except (ValueError, TypeError):
                pass
        s = (rendered_sql or "").strip()
        while s.startswith("(") and s.endswith(")"):
            s = s[1:-1].strip()
        try:
            val = float(s)
            return val != 0.0
        except (ValueError, TypeError):
            return False

    def _render_divide(self, args: List[DaxNode]) -> str:
        if len(args) < 2:
            raise self.DaxRenderError("DIVIDE requires at least 2 arguments")
        den = self._render_node(args[1])
        den_expr = f"({den})" if isinstance(args[1], BinaryOpNode) and args[1].op in ("+", "-") else den
        num = self._render_node(args[0])
        num_expr = f"({num})" if isinstance(args[0], BinaryOpNode) and args[0].op in ("+", "-") else num
        if "PARTITION BY EXCLUDING" in den_expr:
            if isinstance(args[0], MeasureRefNode):
                m_alias = sanitize_column(args[0].name)
                num_expr = f'{self.table_alias.upper()}."{m_alias}"'
            elif isinstance(args[0], FunctionCallNode):
                rendered_num = self._render_node(args[0])
                matched_m = None
                for m_k, m_sql in self.measure_sql_map.items():
                    if m_sql.strip().upper() in (rendered_num.strip().upper(), f"({rendered_num.strip().upper()})"):
                        matched_m = m_k
                        break
                if matched_m:
                    m_alias = sanitize_column(matched_m)
                    num_expr = f'{self.table_alias.upper()}."{m_alias}"'
                else:
                    num_expr = rendered_num
            else:
                num = self._render_node(args[0])
                num_expr = f"({num})" if isinstance(args[0], BinaryOpNode) and args[0].op in ("+", "-") else num
        else:
            num = self._render_node(args[0])
            num_expr = f"({num})" if isinstance(args[0], BinaryOpNode) and args[0].op in ("+", "-") else num
        num_expr = _wrap_parens_for_op(num_expr)
        den_expr = _wrap_parens_for_op(den_expr)
        if self._is_nonzero_constant(args[1], den_expr):
            return f"{num_expr} / {den_expr}"
        if len(args) > 2:
            alt_node = args[2]
            if isinstance(alt_node, FunctionCallNode) and alt_node.func.upper() == "BLANK":
                return f"{num_expr} / NULLIF({den_expr}, 0)"
            alt = self._render_node(alt_node)
            if alt.upper() in ("NULL", "NONE", "", "BLANK()"):
                return f"{num_expr} / NULLIF({den_expr}, 0)"
            return self.dialect.iff(f"COALESCE({den_expr}, 0) = 0", alt, f"{num_expr} / {den_expr}")
        return f"{num_expr} / NULLIF({den_expr}, 0)"

    def _render_if(self, args: List[DaxNode]) -> str:

        if len(args) < 2:

            raise self.DaxRenderError("IF requires at least 2 arguments")

        cond_node = args[0]

        # Quick-measure error guard unwrap:
        # IF(..., ERROR(...), <calc>) -> <calc>
        # IF(..., <calc>, ERROR(...)) -> <calc>
        if len(args) >= 3:
            if isinstance(args[1], FunctionCallNode) and args[1].func.upper() == "ERROR":
                return self._render_node(args[2])
            if isinstance(args[2], FunctionCallNode) and args[2].func.upper() == "ERROR":
                return self._render_node(args[1])

        # Template helper guard unwrap: IF([_ShowValueForDates], <calc>) -> <calc>
        if isinstance(cond_node, MeasureRefNode):
            clean_m = cond_node.name.strip("[]").lower()
            if clean_m in ("_showvaluefordates", "showvaluefordates"):
                return self._render_node(args[1])

        # HASONEVALUE guard: IF(HASONEVALUE(col), calc[, alt]) returns calc only
        # where exactly one value of col is visible (and BLANK/alt at totals).
        # Mapping it to NON ADDITIVE BY col changes total/subtotal values, so
        # it is declined (see HASONEVALUE_GUARD_REASON).
        if isinstance(cond_node, FunctionCallNode) and cond_node.func.upper() in ("HASONEVALUE", "ISINSCOPE", "ISFILTERED", "HASONEFILTER"):
            raise self.DaxRenderError(HASONEVALUE_GUARD_REASON)

        cond = self._render_node(cond_node)

        if self._is_numeric_truthiness_condition(cond_node):

            cond = f"(({cond}) IS NOT NULL AND ({cond}) <> 0)"

        then_node = args[1]
        else_node = args[2] if len(args) > 2 else None

        # Rule A: IF(cond, <numeric>, "") or IF(cond, <numeric>, BLANK()) -> CASE WHEN cond THEN <numeric> END
        if _is_empty_branch(else_node):
            true_val = self._render_node(then_node)
            return f"CASE WHEN {cond} THEN {true_val} END"

        if _is_empty_branch(then_node) and else_node is not None:
            false_val = self._render_node(else_node)
            return f"CASE WHEN {self._negate(f'({cond})')} THEN {false_val} END"

        true_val = self._render_node(then_node)

        false_val = self._render_node(else_node) if else_node is not None else "NULL"

        return f"CASE WHEN {cond} THEN {true_val} ELSE {false_val} END"



    def _is_numeric_truthiness_condition(self, node: DaxNode) -> bool:

        """True when `node` is something this renderer turns into a numeric

        SQL expression rather than an inherently boolean one (see

        _NUMERIC_TRUTHINESS_FUNCS). Deliberately conservative: only

        recognizes function calls, arithmetic, and measure references this

        renderer itself is responsible for turning numeric, so a bare

        column reference — which may already be a genuine BOOLEAN column in

        the model — is left untouched rather than risk wrapping something

        Snowflake would then reject the other way (BOOLEAN compared to 0).



        A MeasureRefNode counts only when it resolves through

        measure_sql_map to another measure's own (numeric, aggregate) SQL —

        e.g. IF([Gross Margin SPLY], [YoY GM Var]/[Gross Margin SPLY],

        BLANK()), the common YoY/SPLY "skip if no prior-period data" guard,

        where [Gross Margin SPLY] is itself CALCULATE(SUM(...),

        SAMEPERIODLASTYEAR(...)). An unresolved reference falls through to

        _render_measure_ref's own bare-column fallback, which may be a

        genuine boolean column — left alone here for the same reason as any

        other ColumnRefNode.

        """

        if isinstance(node, FunctionCallNode):

            return node.func.upper() in self._NUMERIC_TRUTHINESS_FUNCS

        if isinstance(node, BinaryOpNode):

            return node.op in ("+", "-", "*", "/")

        if isinstance(node, MeasureRefNode):

            return node.name.casefold() in self._measure_sql_map_by_casefold

        return False



    def _render_iferror(self, args: List[DaxNode]) -> str:

        if len(args) < 2:

            raise self.DaxRenderError("IFERROR requires 2 arguments")

        expr = self._render_node(args[0])

        fallback = self._render_node(args[1])

        as_number = self.dialect.try_to_number(self.dialect.to_varchar(expr))
        return self.dialect.iff(f"{as_number} IS NULL", fallback, expr)



    def _render_switch(self, args: List[DaxNode]) -> str:

        if len(args) < 3:

            raise self.DaxRenderError("SWITCH requires at least 3 arguments")

        selector = args[0]
        is_searched = (
            (isinstance(selector, LiteralNode) and selector.value is True)
            or (isinstance(selector, FunctionCallNode) and selector.func.upper() == "TRUE" and not selector.args)
        )
        cases = []
        i = 1
        while i + 1 < len(args):
            if is_searched:
                # SWITCH(TRUE(), cond1, v1, ...) -> searched CASE WHEN cond1 THEN v1
                cond = self._render_node(args[i])
                if self._is_numeric_truthiness_condition(args[i]):
                    cond = f"(({cond}) IS NOT NULL AND ({cond}) <> 0)"
            else:
                cond = self._render_comparison(BinaryOpNode(op="=", left=selector, right=args[i]))
            result = self._render_node(args[i + 1])
            cases.append(f"WHEN {cond} THEN {result}")
            i += 2

        else_clause = f"ELSE {self._render_node(args[-1])}" if len(args) % 2 == 0 else "ELSE NULL"

        return "CASE " + " ".join(cases) + f" {else_clause} END"



    def _rewrite_filtered_aggregate(self, agg_node: FunctionCallNode, condition_sql: str) -> str:

        func = agg_node.func.upper()

        if func not in self._AGG_MAP and func not in self._ITERATOR_MAP:

            raise self.DaxRenderError(f"Function {func} is not a recognized direct aggregation")



        if self._AGG_CALL_START.search(condition_sql):

            raise self.DaxRenderError(

                f"Filter condition contains an aggregate function, which cannot be nested inside {func}: {condition_sql}"

            )

        

        if func == "COUNTROWS":
            if condition_sql and condition_sql.strip() not in ("1=1", "TRUE", "1 = 1", "(1=1)", "(TRUE)"):
                return f"COUNT(CASE WHEN {condition_sql} THEN 1 END)"
            return "COUNT(*)"



        if func in self._ITERATOR_MAP:

            sql_func = self._ITERATOR_MAP[func]

            if len(agg_node.args) < 2:

                raise self.DaxRenderError(f"Iterator {func} requires at least 2 arguments")

            expr_sql = self._render_node(agg_node.args[1])

            if func == "COUNTAX" and expr_sql in ("TRUE", "true", "True", "1"):

                case_expr = f"CASE WHEN {condition_sql} THEN 1 END"

            else:

                case_expr = f"CASE WHEN {condition_sql} THEN {expr_sql} END"

            cast = self.dialect.float_cast_suffix if sql_func in ("SUM", "AVG") else ""

            return f"{sql_func}({case_expr}{cast})"

            

        if not agg_node.args:

            raise self.DaxRenderError(f"Aggregation {func} requires at least 1 argument")

            

        col_sql = self._render_node(agg_node.args[0])

        case_expr = f"CASE WHEN {condition_sql} THEN {col_sql} END"

        

        if func == "DISTINCTCOUNT":

            return f"COUNT(DISTINCT {case_expr})"

            

        sql_func = "AVG" if func == "AVERAGE" else func

        if sql_func == "COUNTA":

            sql_func = "COUNT"



        cast = self.dialect.float_cast_suffix if func in ("SUM", "AVERAGE") else ""

        return f"{sql_func}({case_expr}{cast})"



    _AGG_CALL_START = re.compile(r"\b(SUM|AVG|MIN|MAX|COUNT)\s*\(", re.IGNORECASE)



    @staticmethod

    def _find_balanced_close_paren(expr: str, open_paren_index: int) -> Optional[int]:

        """Return the index of the ')' matching the '(' at

        open_paren_index, tracking nesting depth and skipping single-

        quoted string literals. None if the parens never balance."""

        depth = 0

        in_string = False

        for i in range(open_paren_index, len(expr)):

            ch = expr[i]

            if ch == "'":

                in_string = not in_string

                continue

            if in_string:

                continue

            if ch == "(":

                depth += 1

            elif ch == ")":

                depth -= 1

                if depth == 0:

                    return i

        return None



    def _inject_case_filter_into_rendered_aggregate(self, rendered_sql: str, condition_sql: str) -> Optional[str]:

        """Rewrite an already-rendered SQL expression (e.g. from inlining a

        referenced measure's own SQL) so every leaf SUM/AVG/MIN/MAX/COUNT

        call in it is filtered by `condition_sql`, instead of wrapping the

        whole already-aggregated expression in a second outer aggregate.



        Finds and rewrites EVERY bare aggregate call wherever it appears —

        not just at the top level — so this handles a measure defined as

        [A] - [B] (each side its own aggregate), a ratio like

        DIVIDE([A], [B]), an IF(...)-guarded ratio, or any other

        combination of aggregates, uniformly: applying a row-level date

        filter to a formula built purely from same-grain aggregates is

        mathematically equivalent whether applied to the whole formula or

        to each of its aggregate leaves individually, regardless of what

        arithmetic/conditional structure combines them. Only the

        aggregates' own arguments are rewritten; everything else (IF/CASE

        structure, division, comparisons) is left untouched.



        Returns None (fail closed) if `rendered_sql` contains a NESTED

        aggregate (a call whose own argument contains another aggregate

        call) — that shape means this isn't simple leaf-level aggregation,

        and rewriting it here would risk producing SUM(...SUM(...)...),

        which Snowflake (and this codebase's own metric-SQL rules)

        disallow — or if no aggregate call is found at all (nothing to

        filter).

        """

        clean = self._unwrap_str(rendered_sql or "")
        if not clean or self._AGG_CALL_START.search(condition_sql):
            return None
        if re.search(r"\bOVER\s*\(", clean, re.IGNORECASE):
            return None  # window metric -- filtering inside it changes its meaning



        calls: List[Tuple[int, int, str, str]] = []

        for m in self._AGG_CALL_START.finditer(clean):

            func = m.group(1).upper()

            open_idx = m.end() - 1

            close_idx = self._find_balanced_close_paren(clean, open_idx)

            if close_idx is None:

                return None  # unbalanced parens — malformed, fail closed

            arg = clean[open_idx + 1 : close_idx].strip()

            calls.append((m.start(), close_idx, func, arg))



        if not calls:

            return None



        # A call nested inside another call's argument span means this

        # isn't a flat leaf-level formula (e.g. SUM(AVG(x))) — fail closed

        # rather than guess which one should actually be filtered.

        for i, (s1, e1, _f1, _a1) in enumerate(calls):

            for j, (s2, e2, _f2, _a2) in enumerate(calls):

                if i != j and s2 > s1 and e2 < e1:

                    return None



        # Any column reference OUTSIDE an aggregate call is a citation of
        # another metric (or a precomputed helper column) -- it would stay
        # unfiltered, so the rewrite would be wrong. Fail closed.
        outside = clean
        for start, end, _f, _a in sorted(calls, key=lambda c: c[0], reverse=True):
            outside = outside[:start] + " 0 " + outside[end + 1:]
        outside = re.sub(r"'(?:''|[^'])*'", "''", outside)
        if re.search(rf"\b[A-Za-z_][A-Za-z0-9_$]*\s*\.\s*(?:{self._ident_quote_re}|[A-Za-z_])", outside):
            return None
        rewritten = clean
        for start, end, func, arg in sorted(calls, key=lambda c: c[0], reverse=True):
            if func == "COUNT" and re.match(r"^DISTINCT\b", arg, re.IGNORECASE):
                inner = arg[len("DISTINCT"):].strip()
                replacement = f"COUNT(DISTINCT CASE WHEN {condition_sql} THEN {inner} END)"
                rewritten = rewritten[:start] + replacement + rewritten[end + 1 :]
                continue

            if arg == "*":

                key_col = None

                if self.table_key_map and self.primary_table_name:

                    key_col = self.table_key_map.get(self.primary_table_name.strip("'").casefold())

                if key_col:

                    san_col = sanitize_column(key_col)

                    col_ref = f"{self.table_alias}.{san_col}" if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", san_col) else self.dialect.qualify(self.table_alias, san_col)

                    replacement = f"{func}(CASE WHEN {condition_sql} THEN {col_ref} END)"

                else:

                    replacement = f"{func}(CASE WHEN {condition_sql} THEN 1 END)"

            else:

                replacement = f"{func}(CASE WHEN {condition_sql} THEN {arg} ELSE NULL END)"

            rewritten = rewritten[:start] + replacement + rewritten[end + 1 :]



        return rewritten



    def _extract_closing_balance_dimension(self, node: DaxNode) -> Optional[Tuple[Optional[str], str]]:
        """Detect closing-balance / last-period filter patterns:
          FILTER(ALL(table[col]), col = MAX(col))
          FILTER(table, col = MAX(col))
          col = MAX(col)
          LASTNONBLANK(table[col], ...)
          LASTNONBLANK(ALL(table[col]), ...)
        Returns (table_name, column_name) if matched, else None.
        """
        if isinstance(node, IdentifierNode):
            var_name = node.name.strip("[]").casefold()
            if hasattr(self, "_var_ast_map") and var_name in self._var_ast_map:
                node = self._var_ast_map[var_name]

        if isinstance(node, FunctionCallNode) and node.func.upper() == "LASTNONBLANK" and len(node.args) >= 1:
            first_arg = node.args[0]
            if isinstance(first_arg, FunctionCallNode) and first_arg.func.upper() in ("ALL", "ALLEXCEPT", "VALUES") and first_arg.args:
                first_arg = first_arg.args[0]
            if isinstance(first_arg, ColumnRefNode):
                return (first_arg.table, first_arg.column)
            if isinstance(first_arg, IdentifierNode):
                return (None, first_arg.name)

        pred_node = node
        if isinstance(node, FunctionCallNode) and node.func.upper() == "FILTER" and len(node.args) >= 2:
            pred_node = node.args[1]

        if isinstance(pred_node, BinaryOpNode) and pred_node.op in ("=", "=="):
            dim = self._match_col_equals_max(pred_node.left, pred_node.right) or self._match_col_equals_max(pred_node.right, pred_node.left)
            if dim:
                return dim

        return None



    def _match_col_equals_max(self, col_node: DaxNode, max_node: DaxNode) -> Optional[Tuple[Optional[str], str]]:

        if isinstance(col_node, ColumnRefNode) and isinstance(max_node, FunctionCallNode):

            if max_node.func.upper() == "MAX" and len(max_node.args) == 1:

                arg = max_node.args[0]

                if isinstance(arg, ColumnRefNode):

                    c1 = col_node.column.strip("[]'\"").casefold()

                    c2 = arg.column.strip("[]'\"").casefold()

                    if c1 == c2:

                        t1 = col_node.table.strip("[]'\"").casefold() if col_node.table else None

                        t2 = arg.table.strip("[]'\"").casefold() if arg.table else None

                        if t1 is None or t2 is None or t1 == t2:

                            return (col_node.table or arg.table, col_node.column)

        return None




    def _extract_agg_column_and_func(self, node: DaxNode) -> Optional[Tuple[str, Optional[str], str]]:
        if isinstance(node, FunctionCallNode):
            func = node.func.upper()
            if func == "COUNTROWS":
                return ("COUNT", None, "*")
            if func in ("SUM", "AVG", "AVERAGE", "COUNT", "MIN", "MAX"):
                if node.args:
                    arg = node.args[0]
                    if isinstance(arg, ColumnRefNode):
                        clean_col = (arg.column or "").strip("[]")
                        return (func, arg.table, clean_col)
                    elif isinstance(arg, MeasureRefNode):
                        return self._resolve_measure_agg_info(arg.name)
        elif isinstance(node, MeasureRefNode):
            return self._resolve_measure_agg_info(node.name)
        return None

    def _resolve_measure_agg_info(self, measure_name: str) -> Optional[Tuple[str, Optional[str], str]]:
        sql = self.measure_sql_map.get(measure_name) or self._measure_sql_map_by_casefold.get(measure_name.casefold())
        if sql:
            if re.search(r'\bCOUNT\s*\(\s*(\*|1)\s*\)', sql, re.IGNORECASE):
                return ("COUNT", None, "*")
            m = re.search(r'\b(SUM|AVG|COUNT|MIN|MAX)\s*\(\s*(?:([a-zA-Z0-9_]+)\.)?\"?([a-zA-Z0-9_]+)\"?\s*\)', sql, re.IGNORECASE)
            if m:
                return (m.group(1).upper(), m.group(2), m.group(3))
        return None

    def _get_emitted_dimensions_for_table(self, tbl_name: str, alias: str) -> List[str]:
        dims: List[str] = []
        if self.datasets:
            for ds in self.datasets:
                ds_name = getattr(ds, "unique_name", getattr(ds, "name", ""))
                if ds_name.casefold() == tbl_name.casefold():
                    cols = getattr(ds, "columns", []) or []
                    for col_item in cols:
                        c_name = getattr(col_item, "unique_name", getattr(col_item, "name", ""))
                        if c_name and not c_name.upper().startswith(("ALL_", "__SB_")):
                            safe_col = sanitize_column(c_name)
                            dim_ref = f'{alias}."{safe_col}"'
                            if dim_ref not in dims:
                                dims.append(dim_ref)
                    if dims:
                        return dims
        if getattr(self, "dataset_col_lookup", None):
            for ds_k, cols in self.dataset_col_lookup.items():
                if ds_k.casefold() == tbl_name.casefold():
                    for c_name in sorted(cols):
                        if c_name and not str(c_name).upper().startswith(("ALL_", "__SB_")):
                            safe_col = sanitize_column(c_name)
                            dim_ref = f'{alias}."{safe_col}"'
                            if dim_ref not in dims:
                                dims.append(dim_ref)
                    if dims:
                        return dims
        return dims

    def _get_minimal_groupable_dimensions(self, tbl_name: str, alias: str) -> List[str]:
        candidate_cols: List[str] = []
        if self.datasets:
            for ds in self.datasets:
                ds_name = getattr(ds, "unique_name", getattr(ds, "name", ""))
                if ds_name.casefold() == tbl_name.casefold():
                    cols = getattr(ds, "columns", []) or []
                    for col_item in cols:
                        c_name = getattr(col_item, "unique_name", getattr(col_item, "name", ""))
                        if c_name:
                            candidate_cols.append(c_name)
                    if candidate_cols:
                        break
        if not candidate_cols and getattr(self, "dataset_col_lookup", None):
            for ds_k, cols in self.dataset_col_lookup.items():
                if ds_k.casefold() == tbl_name.casefold():
                    candidate_cols = list(cols)
                    break

        rel_cols = set()
        if self.relationships:
            for rel in self.relationships:
                f_tbl = getattr(rel, "from_dataset", None) or getattr(rel, "fromTable", "")
                t_tbl = getattr(rel, "to_dataset", None) or getattr(rel, "toTable", "")
                if f_tbl and str(f_tbl).casefold() == tbl_name.casefold():
                    for c in (getattr(rel, "from_columns", None) or [getattr(rel, "fromColumn", None)]):
                        if c: rel_cols.add(re.sub(r"[^A-Z0-9]", "", str(c).upper()))
                if t_tbl and str(t_tbl).casefold() == tbl_name.casefold():
                    for c in (getattr(rel, "to_columns", None) or [getattr(rel, "toColumn", None)]):
                        if c: rel_cols.add(re.sub(r"[^A-Z0-9]", "", str(c).upper()))

        NUMERIC_TYPES = frozenset({"INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL", "NUMBER"})
        pk_cols = set()
        col_type_map = {}
        if self.datasets:
            for ds in self.datasets:
                ds_name = getattr(ds, "unique_name", getattr(ds, "name", ""))
                if ds_name.casefold() == tbl_name.casefold():
                    pks = getattr(ds, "primary_key", []) or []
                    if isinstance(pks, (str, bytes)): pks = [pks]
                    for pk in pks:
                        pk_cols.add(re.sub(r"[^A-Z0-9]", "", str(pk).upper()))
                    for col_item in getattr(ds, "columns", []) or []:
                        c_name = getattr(col_item, "unique_name", getattr(col_item, "name", ""))
                        if c_name:
                            c_norm = re.sub(r"[^A-Z0-9]", "", c_name.upper())
                            if getattr(col_item, "is_key", False) or getattr(col_item, "is_pk", False) or getattr(col_item, "is_unique", False):
                                pk_cols.add(c_norm)
                            dt = getattr(col_item, "data_type", None) or getattr(col_item, "type", None)
                            dt_str = str(dt.value if hasattr(dt, "value") else dt or "").upper()
                            col_type_map[c_norm] = dt_str
                    break

        report_layout_cols = set()
        if self.model_metadata:
            pres_meta = getattr(self.model_metadata, "presentation_metadata", None) or (self.model_metadata.get("presentation_metadata") if isinstance(self.model_metadata, dict) else None) or []
            for item in pres_meta:
                if isinstance(item, dict):
                    f_tbl = item.get("table")
                    if not f_tbl or str(f_tbl).casefold() == tbl_name.casefold():
                        f_col = item.get("field")
                        if f_col:
                            report_layout_cols.add(re.sub(r"[^A-Z0-9]", "", str(f_col).upper()))

        clean_candidates: List[str] = []
        for c in candidate_cols:
            cu = c.upper()
            if cu.startswith(("ALL_", "__SB_", "ROWNUMBER", "_")):
                continue
            norm_c = re.sub(r"[^A-Z0-9]", "", cu)
            if norm_c in rel_cols and len(candidate_cols) > 1:
                continue
            if norm_c in pk_cols and len(candidate_cols) > 1:
                continue
            dt_upper = col_type_map.get(norm_c, "")
            if dt_upper in NUMERIC_TYPES or any(num_t in dt_upper for num_t in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL", "NUMBER")):
                continue
            clean_candidates.append(c)

        if not clean_candidates:
            non_rel_non_pk = [c for c in candidate_cols if re.sub(r"[^A-Z0-9]", "", c.upper()) not in rel_cols and re.sub(r"[^A-Z0-9]", "", c.upper()) not in pk_cols]
            clean_candidates = non_rel_non_pk if non_rel_non_pk else candidate_cols
            if not clean_candidates:
                return []

        def rank_col(col_name: str) -> int:
            norm_c = re.sub(r"[^A-Z0-9]", "", col_name.upper())
            if norm_c in report_layout_cols:
                return 0
            if getattr(self, "current_measure_name", None):
                m_norm = re.sub(r"[^A-Z0-9]", "", self.current_measure_name.upper())
                if norm_c in m_norm and len(norm_c) >= 3:
                    return 1
            tbl_norm = re.sub(r"[^A-Z0-9]", "", tbl_name.upper().rstrip("S"))
            if norm_c == tbl_norm:
                return 2
            dt_upper = col_type_map.get(norm_c, "")
            if dt_upper in ("STRING", "TEXT", "VARCHAR", "CHAR") or any(txt_t in dt_upper for txt_t in ("STRING", "TEXT", "CHAR")):
                return 3
            return 4

        clean_candidates.sort(key=rank_col)
        best_col = clean_candidates[0]
        safe_col = sanitize_column(best_col)
        return [f'{alias}."{safe_col}"']

    def _has_nested_window(self, sql_expr: str, visited: Optional[Set[str]] = None) -> bool:
        if not sql_expr:
            return False
        if "OVER (" in sql_expr.upper() or "OVER(" in sql_expr.upper():
            return True
        if visited is None:
            visited = set()
        norm_map = {re.sub(r'[^A-Za-z0-9]', '', k.upper()): v for k, v in self.measure_sql_map.items()} if self.measure_sql_map else {}
        for ref in re.findall(r'"([A-Za-z0-9_]+)"', sql_expr):
            norm_ref = re.sub(r'[^A-Za-z0-9]', '', ref.upper())
            if norm_ref in visited:
                continue
            visited.add(norm_ref)
            ref_sql = norm_map.get(norm_ref)
            if ref_sql and self._has_nested_window(ref_sql, visited):
                return True
        return False

    def _render_calculate(self, args: List[DaxNode]) -> str:

        """

        Translate CALCULATE(agg_expr, filter1, filter2, ...) to a subquery or CASE expression.



        Supported filter modifiers:

          - ALL(table)          → ignore all filters for that table

          - ALLEXCEPT(t, col)   → OVER (PARTITION BY col)

          - FILTER(tbl, pred)   → WHERE pred

          - simple comparisons  → WHERE comparison

        """

        if not args:

            raise self.DaxRenderError("CALCULATE requires at least 1 argument")

        if len(args) >= 2:
            from semabridge.converter.whole_table_all import match_whole_table_all_ast
            calc_node = FunctionCallNode(func="CALCULATE", args=args)
            pat = match_whole_table_all_ast(calc_node, fact_table=self.primary_table_name or self.table_alias)
            if pat:
                # Both forms below are platform capabilities: a leave-one-out
                # window total, or a helper column precomputed on an
                # enriched view. A platform without them declines here.
                caps = self.rules.capabilities
                if not (caps.window_metrics if getattr(self, "_current_loo_info", None) else caps.enriched_helper_columns):
                    raise self.DaxRenderError(self.rules.policies.reason_text(ReasonCode.NEEDS_WINDOW))
                # Leave-One-Out (LOO) total expression substitution:
                # When CALCULATE(..., ALL(FactTable)) is evaluated inside a leave-one-out context (e.g. CSAT Impact),
                # directly use the precomputed total calculation expression rather than referencing an external helper column.
                loo = getattr(self, "_current_loo_info", None)
                if loo:
                    filter_arg = args[1]
                    if isinstance(filter_arg, FunctionCallNode) and filter_arg.func.upper() in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):
                        if filter_arg.args:
                            arg0 = filter_arg.args[0]
                            tbl = getattr(arg0, "name", getattr(arg0, "table", str(arg0))).strip("'\"")
                            fact_alias = (self.table_alias or "").upper()
                            fact_tbl = (self.primary_table_name or self.table_alias or "").upper()
                            if tbl.upper() in (fact_alias, fact_tbl) or (self.table_aliases and self.table_aliases.get(tbl, tbl).upper() == fact_alias):
                                if loo.get("inner_fn") in ("AVERAGE", "AVERAGEX"):
                                    return f'({loo["sum_total"]} / NULLIF({loo["cnt_total"]}, 0))'
                                elif loo.get("inner_fn") in ("COUNT", "COUNTROWS", "COUNTA"):
                                    return f'{loo["cnt_total"]}'
                                else:
                                    return f'{loo["sum_total"]}'
                tgt_alias = (self.table_alias or self.primary_table_name or "f").upper()
                return f'MAX({tgt_alias}."{pat.helper_name}")'

        userel_overrides = {}
        filter_args = args[1:]
        filtered_args = [args[0]]
        for filter_arg in args[1:]:
            if isinstance(filter_arg, FunctionCallNode) and filter_arg.func.upper() == "USERELATIONSHIP":
                # The semantic view always joins through the active
                # relationship; outer filters/groupings on the role-playing
                # dimension would keep flowing through it, so no CASE
                # rewrite reproduces DAX here.
                raise self.DaxRenderError(USERELATIONSHIP_REASON)
            filtered_args.append(filter_arg)

        prev_userel = getattr(self, "_userel_overrides", {})
        self._userel_overrides = {**prev_userel, **userel_overrides}

        try:
            agg_expr = self._render_node(filtered_args[0])
            if not agg_expr or agg_expr.strip().upper() == "NULL" or self.rules.policies.is_placeholder(agg_expr):
                raise self.DaxRenderError("depends on untranslatable part: base aggregation is untranslatable")

            filter_clauses: List[str] = []

            partition_cols: List[str] = []

            is_window = False

            # If base is a measure reference, check if it brings a non-additive dimension

            if isinstance(filtered_args[0], MeasureRefNode):

                m_name = filtered_args[0].name

                for k, v in self.measure_non_additive_map.items():

                    if k.casefold() == m_name.casefold():

                        if v and v not in self.non_additive_dimensions:

                            self.non_additive_dimensions.append(v)

                        break

            # Flatten CALCULATETABLE arguments into filter list
            expanded_filters: List[DaxNode] = []
            for f in filtered_args[1:]:
                if isinstance(f, FunctionCallNode) and f.func.upper() == "CALCULATETABLE":
                    expanded_filters.extend(f.args)
                else:
                    expanded_filters.append(f)

            # Time-intelligence modifiers (DATESYTD/.../SAMEPERIODLASTYEAR/
            # PREVIOUSx/DATEADD/PARALLELPERIOD). Every OTHER filter argument
            # is ANDed into the same window -- never dropped.
            ti_mods = []
            for filter_arg in expanded_filters:
                if isinstance(filter_arg, FunctionCallNode):
                    fname = filter_arg.func.upper()
                    if fname in ("DATESYTD", "DATESQTD", "DATESMTD", "TOTALYTD", "TOTALQTD", "TOTALMTD",
                                 "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER"):
                        ti_mods.append(filter_arg)
                    elif fname in ("DATEADD", "PARALLELPERIOD") and len(filter_arg.args) >= 3:
                        ti_mods.append(filter_arg)
            if len(ti_mods) > 1:
                raise self.DaxRenderError(
                    "not applicable: more than one time-intelligence filter in a single CALCULATE"
                )
            if ti_mods:
                ti = ti_mods[0]
                extra_preds: List[str] = []
                for other in expanded_filters:
                    if other is ti:
                        continue
                    if self._extract_closing_balance_dimension(other):
                        raise self.DaxRenderError(
                            "not applicable: time-intelligence filter combined with a closing-balance filter"
                        )
                    if isinstance(other, FunctionCallNode) and other.func.upper() in self._ALL_LIKE_FUNCS:
                        raise self.DaxRenderError(
                            "not applicable: time-intelligence filter combined with ALL/ALLEXCEPT/REMOVEFILTERS"
                        )
                    extra_preds.append(self._render_calc_filter_predicate(other))
                fname = ti.func.upper()
                date_arg = ti.args[0] if ti.args else None
                if date_arg is None:
                    raise self.DaxRenderError(f"{ti.func} requires a date-column argument")
                if fname in ("DATESYTD", "DATESQTD", "DATESMTD", "TOTALYTD", "TOTALQTD", "TOTALMTD"):
                    ti_sql = self._render_period_to_date(
                        fname, [filtered_args[0], date_arg], year_end_node=period_to_date_year_end_node(ti)
                    )
                else:
                    lag_interval, lag_amount = "year", -1
                    if fname in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR"):
                        lag_interval = "year"
                    elif fname == "PREVIOUSQUARTER":
                        lag_interval = "quarter"
                    elif fname == "PREVIOUSMONTH":
                        lag_interval = "month"
                    else:
                        lag_amount = self._extract_shift_amount(ti.args[1])
                        intv_raw = str(getattr(ti.args[2], "name", getattr(ti.args[2], "value", "year"))).lower()
                        if "year" in intv_raw:
                            lag_interval = "year"
                        elif "quarter" in intv_raw:
                            lag_interval = "quarter"
                        elif "month" in intv_raw:
                            lag_interval = "month"
                        elif "day" in intv_raw:
                            lag_interval = "day"
                        else:
                            raise self.DaxRenderError(f"Unsupported DATEADD interval '{intv_raw}'")
                    self._in_shifted_calculate += 1
                    try:
                        ti_sql = self._render_lag_period(
                            [filtered_args[0], date_arg], interval=lag_interval, amount=lag_amount, func_name=fname
                        )
                    finally:
                        self._in_shifted_calculate -= 1
                if not extra_preds:
                    return ti_sql
                where = " AND ".join(f"({p})" for p in extra_preds)
                combined = self._inject_case_filter_into_rendered_aggregate(ti_sql, where)
                if not combined:
                    raise self.DaxRenderError(
                        "not applicable: could not combine the time-intelligence window with the other CALCULATE filters"
                    )
                return combined
            for filter_arg in expanded_filters:

                # Closing-balance pattern check: handled via NON ADDITIVE BY instead of row-level predicate

                cb_dim = self._extract_closing_balance_dimension(filter_arg)

                if cb_dim:

                    if cb_dim not in self.non_additive_dimensions:

                        self.non_additive_dimensions.append(cb_dim)

                    continue

                if isinstance(filter_arg, FunctionCallNode):

                    fname = filter_arg.func.upper()

                    if fname in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):

                        # ALL(table) strips all filters → no WHERE, use window over full table

                        is_window = True

                    elif fname == "ALLEXCEPT":

                        # ALLEXCEPT(table, col1, col2,...) → PARTITION BY col1, col2

                        is_window = True

                        for col_arg in filter_arg.args[1:]:

                            if isinstance(col_arg, ColumnRefNode):

                                partition_cols.append(f'{self.table_alias}."{sanitize_column(col_arg.column)}"')

                            elif isinstance(col_arg, MeasureRefNode):

                                partition_cols.append(f'"{sanitize_column(col_arg.name)}"')

                    elif fname == "FILTER":

                        # FILTER(table, predicate) → extract predicate as WHERE clause

                        if len(filter_arg.args) >= 2:

                            f_tbl = filter_arg.args[0]
                            f_pred = filter_arg.args[1]
                            
                            # Generic Leave-One-Out (LOO) or Whole-table filtered aggregate:
                            # FILTER(ALL(<own fact>), ...)
                            if (isinstance(f_tbl, FunctionCallNode) and 
                                f_tbl.func.upper() in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS")):
                                f_tbl_arg = f_tbl.args[0] if f_tbl.args else None
                                f_tbl_raw = getattr(f_tbl_arg, "name", None) or getattr(f_tbl_arg, "table", None) or (str(f_tbl_arg) if f_tbl_arg else "")
                                f_tbl_name = f_tbl_raw.strip("'\"") if f_tbl_raw else (self.primary_table_name or self.table_alias or "")
                                fact_name = (self.primary_table_name or self.table_alias or "").strip("'\"")
                                is_own_fact = (f_tbl_name.upper() == fact_name.upper() or 
                                               self.table_aliases.get(f_tbl_name, f_tbl_name).strip("'\"").upper() == fact_name.upper())
                                
                                if is_own_fact and isinstance(f_pred, BinaryOpNode) and f_pred.op in ("<>", "!="):
                                    col_node, sv_node = None, None
                                    if isinstance(f_pred.left, ColumnRefNode) and isinstance(f_pred.right, FunctionCallNode) and f_pred.right.func.upper() == "SELECTEDVALUE":
                                        col_node, sv_node = f_pred.left, f_pred.right
                                    elif isinstance(f_pred.right, ColumnRefNode) and isinstance(f_pred.left, FunctionCallNode) and f_pred.left.func.upper() == "SELECTEDVALUE":
                                        col_node, sv_node = f_pred.right, f_pred.left
                                    
                                    if col_node and sv_node and sv_node.args and isinstance(sv_node.args[0], ColumnRefNode):
                                        c1 = sanitize_column(col_node.column).upper()
                                        c2 = sanitize_column(sv_node.args[0].column).upper()
                                        if c1 == c2:
                                            safe_col = c1
                                            fact_alias = self.table_alias.upper()
                                            dim_ref = f'{fact_alias}."{safe_col}"'
                                            base_arg = filtered_args[0]
                                            inner_fn = getattr(base_arg, "func", "").upper() if isinstance(base_arg, FunctionCallNode) else ""
                                            if inner_fn in ("MIN", "MAX", "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK"):
                                                raise self.DaxRenderError("leave-one-out not decomposable")
                                            
                                            agg_col_node = base_arg.args[0] if (isinstance(base_arg, FunctionCallNode) and base_arg.args) else None
                                            agg_col_name = getattr(agg_col_node, "column", None) or getattr(agg_col_node, "name", None) or "AMOUNT"
                                            safe_agg_col = sanitize_column(agg_col_name).upper()

                                            sum_total = f'SUM({fact_alias}."{safe_agg_col}_SUM"::FLOAT) OVER (PARTITION BY EXCLUDING {dim_ref})'
                                            sum_own = f'{fact_alias}."{safe_agg_col}_SUM"'
                                            cnt_total = f'SUM({fact_alias}."{safe_agg_col}_CNT"::FLOAT) OVER (PARTITION BY EXCLUDING {dim_ref})'
                                            cnt_own = f'{fact_alias}."{safe_agg_col}_CNT"'

                                            self._current_loo_info = {
                                                "dim_ref": dim_ref,
                                                "col_name": c1,
                                                "agg_col": safe_agg_col,
                                                "inner_fn": inner_fn,
                                                "sum_total": sum_total,
                                                "sum_own": sum_own,
                                                "cnt_total": cnt_total,
                                                "cnt_own": cnt_own,
                                            }

                                            if inner_fn in ("AVERAGE", "AVERAGEX"):
                                                return f'(({sum_total} - {sum_own}) / NULLIF({cnt_total} - {cnt_own}, 0))'
                                            elif inner_fn in ("COUNT", "COUNTROWS", "COUNTA"):
                                                return f'({cnt_total} - {cnt_own})'
                                            else:
                                                return f'({sum_total} - {sum_own})'

                                if is_own_fact:
                                    raise self.DaxRenderError(
                                        "BY_DESIGN_EXCLUDED: whole-table filtered aggregate requires enriched view precompute"
                                    )

                            iter_tbl = self._extract_table_name(f_tbl)
                            with self._row_context(iter_tbl):
                                pred = self._render_node(filter_arg.args[1])

                            filter_clauses.append(pred)

                        else:

                            raise self.DaxRenderError("FILTER requires table and predicate arguments")

                    else:
                        filter_clauses.append(self._render_calc_filter_predicate(filter_arg))
                else:
                    # Direct comparison / NOT / IN inside CALCULATE, e.g. 'Date'[Year] = 2024
                    filter_clauses.append(self._render_calc_filter_predicate(filter_arg))

            if is_window:
                if True:
                    excl_dims = []
                    fact_alias = (self.table_alias or "").upper()
                    fact_tbl = (self.primary_table_name or self.table_alias or "").upper()

                    for filter_arg in filtered_args[1:]:
                        if not isinstance(filter_arg, FunctionCallNode):
                            continue
                        fmod = filter_arg.func.upper()
                        if fmod in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):
                            if not filter_arg.args:
                                clean_base = "SUM_AMOUNT"
                                base_arg = filtered_args[0]
                                if isinstance(base_arg, MeasureRefNode):
                                    clean_base = re.sub(r'[^A-Za-z0-9_]', '_', base_arg.name).upper()
                                elif isinstance(base_arg, FunctionCallNode):
                                    col_part = getattr(base_arg.args[0], "column", str(base_arg.args[0])) if base_arg.args else ""
                                    clean_base = f"{base_arg.func.upper()}_{re.sub(r'[^A-Za-z0-9_]', '_', col_part).upper()}".strip("_")
                                clean_base = re.sub(r'^_?ALL_', '', clean_base)
                                tgt_alias = (fact_alias or fact_tbl or "f").upper()
                                return f'MAX({tgt_alias}."_ALL_{clean_base}")'
                            if (len(filter_arg.args) >= 3 and 
                                isinstance(filter_arg.args[0], ColumnRefNode) and 
                                getattr(filter_arg.args[1], "name", "") == "."):
                                date_col_node = filter_arg.args[0]
                                level_node = filter_arg.args[2]
                                level_name = getattr(level_node, "name", str(level_node))
                                t_name = date_col_node.table or self.table_alias
                                t_alias = self.table_aliases.get(t_name, t_name).upper() if self.table_aliases else t_name.upper()
                                dims_found = self._get_emitted_dimensions_for_table(t_name, t_alias)
                                norm_lvl = re.sub(r"[^A-Z0-9]", "", level_name.upper())
                                exact_matches = []
                                substr_matches = []
                                for d in dims_found:
                                    col_part = d.split(".")[-1].strip('"')
                                    norm_col = re.sub(r"[^A-Z0-9]", "", col_part.upper())
                                    if norm_col == norm_lvl:
                                        exact_matches.append(d)
                                    elif norm_lvl in norm_col:
                                        substr_matches.append((len(norm_col), d))
                                if exact_matches:
                                    matched_dim = exact_matches[0]
                                elif substr_matches:
                                    substr_matches.sort(key=lambda x: x[0])
                                    matched_dim = substr_matches[0][1]
                                else:
                                    matched_dim = None
                                if matched_dim:
                                    if matched_dim not in excl_dims:
                                        excl_dims.append(matched_dim)
                                else:
                                    raise self.DaxRenderError(f"no matching date hierarchy dimension for {level_name}")
                                continue

                            for arg in filter_arg.args:
                                if isinstance(arg, ColumnRefNode):
                                    tbl = arg.table or self.table_alias
                                    if self.table_aliases and tbl in self.table_aliases:
                                        tbl = self.table_aliases[tbl]
                                    safe_col = sanitize_column(arg.column)
                                    d_ref = f'{tbl.upper()}."{safe_col}"'
                                    if d_ref not in excl_dims:
                                        excl_dims.append(d_ref)
                                elif isinstance(arg, IdentifierNode):
                                    tbl_name = arg.name.strip('"\'')
                                    alias = self.table_aliases.get(tbl_name, tbl_name).upper() if self.table_aliases else tbl_name.upper()
                                    if alias == fact_alias or tbl_name.upper() == fact_tbl:
                                        loo = getattr(self, "_current_loo_info", None)
                                        if loo:
                                            if loo.get("inner_fn") in ("AVERAGE", "AVERAGEX"):
                                                return f'({loo["sum_total"]} / NULLIF({loo["cnt_total"]}, 0))'
                                            elif loo.get("inner_fn") in ("COUNT", "COUNTROWS", "COUNTA"):
                                                return f'{loo["cnt_total"]}'
                                            else:
                                                return f'{loo["sum_total"]}'
                                        clean_base = "TOTAL"
                                        base_arg = filtered_args[0]
                                        if isinstance(base_arg, MeasureRefNode):
                                            clean_base = re.sub(r'[^A-Za-z0-9_]', '_', base_arg.name).upper()
                                        elif isinstance(base_arg, FunctionCallNode):
                                            col_part = getattr(base_arg.args[0], "column", str(base_arg.args[0])) if base_arg.args else ""
                                            clean_base = f"{base_arg.func.upper()}_{re.sub(r'[^A-Za-z0-9_]', '_', col_part).upper()}".strip("_")
                                        elif hasattr(base_arg, "name"):
                                            clean_base = re.sub(r'[^A-Za-z0-9_]', '_', base_arg.name).upper()
                                        clean_base = re.sub(r'^_?ALL_', '', clean_base)
                                        tgt_alias = (fact_alias or fact_tbl or "f").upper()
                                        return f'MAX({tgt_alias}."_ALL_{clean_base}")'
                                    dims_found = self._get_minimal_groupable_dimensions(tbl_name, alias)
                                    for d in dims_found:
                                        if d not in excl_dims:
                                            excl_dims.append(d)
                        elif fmod == "ALLEXCEPT":
                            if not filter_arg.args:
                                continue
                            tbl_node = filter_arg.args[0]
                            tbl_name = (getattr(tbl_node, "table", None) or getattr(tbl_node, "name", None) or str(tbl_node)).strip('"\'')
                            alias = self.table_aliases.get(tbl_name, tbl_name).upper() if self.table_aliases else tbl_name.upper()
                            part_dims = []
                            keep_names = set()
                            for k_arg in filter_arg.args[1:]:
                                if isinstance(k_arg, ColumnRefNode):
                                    t = k_arg.table or tbl_name
                                    t_al = self.table_aliases.get(t, t).upper() if self.table_aliases else t.upper()
                                    col = sanitize_column(k_arg.column)
                                    part_dims.append(f'{t_al}."{col}"')
                                    keep_names.add(re.sub(r"[^A-Z0-9]", "", k_arg.column.upper()))
                                elif isinstance(k_arg, IdentifierNode):
                                    col = sanitize_column(k_arg.name)
                                    part_dims.append(f'{alias}."{col}"')
                                    keep_names.add(re.sub(r"[^A-Z0-9]", "", k_arg.name.upper()))

                            is_fact = (alias == fact_alias or tbl_name.upper() == fact_tbl)
                            if part_dims:
                                part_clause = ", ".join(part_dims)
                                base_arg = filtered_args[0]
                                if isinstance(base_arg, MeasureRefNode):
                                    m_alias = sanitize_column(base_arg.name)
                                    metric_ref = f'{self.table_alias.upper()}."{m_alias}"::FLOAT'
                                    return f'SUM({metric_ref}) OVER (PARTITION BY {part_clause})'
                                elif isinstance(base_arg, FunctionCallNode):
                                    fn_u = base_arg.func.upper()
                                    col_node = base_arg.args[0] if base_arg.args else None
                                    col_part = getattr(col_node, "column", None) or getattr(col_node, "name", "AMT") if col_node else "AMT"
                                    col_tbl = getattr(col_node, "table", None)
                                    target_alias = (self.table_aliases.get(col_tbl, col_tbl).upper() if self.table_aliases else col_tbl.upper()) if col_tbl else fact_alias
                                    c_alias = sanitize_column(col_part)
                                    if fn_u in ("AVERAGE", "AVERAGEX"):
                                        return f'SUM({target_alias}."{c_alias}_SUM"::FLOAT) OVER (PARTITION BY {part_clause}) / NULLIF(SUM({target_alias}."{c_alias}_CNT"::FLOAT) OVER (PARTITION BY {part_clause}), 0)'
                                    elif fn_u in ("COUNT", "COUNTROWS", "COUNTA"):
                                        return f'SUM({target_alias}."{c_alias}_CNT"::FLOAT) OVER (PARTITION BY {part_clause})'
                                    elif fn_u in ("SUM", "SUMX"):
                                        return f'SUM({target_alias}."{c_alias}_SUM"::FLOAT) OVER (PARTITION BY {part_clause})'
                                    elif fn_u in ("MIN", "MAX"):
                                        return f'{fn_u}({target_alias}."{c_alias}_{fn_u}"::FLOAT) OVER (PARTITION BY {part_clause})'
                                    else:
                                        raise self.DaxRenderError(f"ALLEXCEPT requires an additive base metric, got '{base_arg.func}'")

                            if is_fact:
                                raise self.DaxRenderError(_CONTEXT_TRANSITION_REASON)
                            target_tables = [tbl_name]
                            if is_fact and self.relationships:
                                for rel in self.relationships:
                                    from_tbl = getattr(rel, "from_dataset", None) or getattr(rel, "fromTable", "")
                                    to_tbl = getattr(rel, "to_dataset", None) or getattr(rel, "toTable", "")
                                    is_active = getattr(rel, "is_active", getattr(rel, "isActive", True))
                                    if is_active and from_tbl and from_tbl.casefold() == tbl_name.casefold() and to_tbl:
                                        if to_tbl not in target_tables:
                                            target_tables.append(to_tbl)

                            for t_name in target_tables:
                                t_alias = self.table_aliases.get(t_name, t_name).upper() if self.table_aliases else t_name.upper()
                                dims_found = self._get_minimal_groupable_dimensions(t_name, t_alias)
                                for d in dims_found:
                                    col_part = d.split(".")[-1].strip('"')
                                    if re.sub(r"[^A-Z0-9]", "", col_part.upper()) not in keep_names:
                                        if d not in excl_dims:
                                            excl_dims.append(d)

                    if excl_dims:
                        excl_clause = ", ".join(excl_dims)
                        base_arg = filtered_args[0]
                        if isinstance(base_arg, MeasureRefNode):
                            m_name = base_arg.name
                            sql_cand = self.measure_sql_map.get(m_name) or self.measure_sql_map.get(m_name.casefold())
                            if sql_cand:
                                if self._has_nested_window(sql_cand):
                                    raise self.DaxRenderError("nested window over a non-additive measure")
                                cand_u = sql_cand.upper()
                                if any(bad in cand_u for bad in ("AVG(", "AVERAGE(", "MEDIAN(", "STDDEV(", "VARIANCE(", "COUNT(DISTINCT")) or "/" in cand_u:
                                    m_alias = sanitize_column(m_name)
                                    ratio = f'SUM({fact_alias}."{m_alias}_NUM"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}) / NULLIF(SUM({fact_alias}."{m_alias}_DEN"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}), 0)'
                                    m_wrap = re.match(r'^ROUND\s*\(\s*(.+?)\s*(\*\s*\d+\s*,\s*\d+)\s*\)\s*/\s*(\d+)$', sql_cand.strip(), re.IGNORECASE | re.DOTALL)
                                    if m_wrap:
                                        return f'ROUND(({ratio}) {m_wrap.group(2)}) / {m_wrap.group(3)}'
                                    m_round_only = re.match(r'^ROUND\s*\(\s*(.+?)\s*,\s*(\d+)\s*\)$', sql_cand.strip(), re.IGNORECASE | re.DOTALL)
                                    if m_round_only:
                                        return f'ROUND(({ratio}), {m_round_only.group(2)})'
                                    return ratio
                            m_alias = sanitize_column(m_name)
                            metric_ref = f'{self.table_alias.upper()}."{m_alias}"::FLOAT'
                            return f'SUM({metric_ref}) OVER (PARTITION BY EXCLUDING {excl_clause})'
                        elif isinstance(base_arg, FunctionCallNode):
                            fn_u = base_arg.func.upper()
                            if fn_u in ("ROUND", "ROUNDUP", "ROUNDDOWN") and base_arg.args:
                                inner_calc = self._render_calculate([base_arg.args[0]] + list(filter_args))
                                dec = self._render_node(base_arg.args[1]) if len(base_arg.args) > 1 else "0"
                                return f"ROUND(({inner_calc}), {dec})"
                            if fn_u in ("AVERAGE", "AVERAGEX"):
                                col_node = base_arg.args[0] if base_arg.args else None
                                col_part = getattr(col_node, "column", None) or getattr(col_node, "name", "AMT") if col_node else "AMT"
                                col_tbl = getattr(col_node, "table", None)
                                target_alias = (self.table_aliases.get(col_tbl, col_tbl).upper() if self.table_aliases else col_tbl.upper()) if col_tbl else fact_alias
                                c_alias = sanitize_column(col_part)
                                return f'SUM({target_alias}."{c_alias}_SUM"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}) / NULLIF(SUM({target_alias}."{c_alias}_CNT"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}), 0)'
                            if fn_u == "DIVIDE":
                                if len(base_arg.args) >= 2 and isinstance(base_arg.args[1], LiteralNode) and isinstance(base_arg.args[1].value, (int, float)) and base_arg.args[1].value != 0:
                                    inner_calc = self._render_calculate([base_arg.args[0]] + list(filter_args))
                                    return f"({inner_calc}) / {base_arg.args[1].value}"
                                m_alias = sanitize_column(getattr(self, "current_measure_name", "") or "M")
                                if not m_alias or m_alias == "M" or m_alias.upper() == fact_alias.upper():
                                    m_alias = sanitize_column(self.table_alias)
                                return f'SUM({fact_alias}."{m_alias}_NUM"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}) / NULLIF(SUM({fact_alias}."{m_alias}_DEN"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}), 0)'
                            if fn_u not in ("SUM", "COUNT", "COUNTROWS", "COUNTA", "MIN", "MAX"):
                                raise self.DaxRenderError(f"Share-of-total requires an additive base metric (SUM, COUNT, COUNTROWS); got non-additive '{base_arg.func}'")
                            agg_op = "MIN" if fn_u == "MIN" else ("MAX" if fn_u == "MAX" else "SUM")
                            rendered_base = self._render_node(base_arg)
                            matched_m = None
                            for m_k, m_sql in self.measure_sql_map.items():
                                if m_sql.strip().upper() in (rendered_base.strip().upper(), f"({rendered_base.strip().upper()})"):
                                    matched_m = m_k
                                    break
                            if matched_m:
                                m_alias = sanitize_column(matched_m)
                                metric_ref = f'{self.table_alias.upper()}."{m_alias}"::FLOAT'
                                return f'{agg_op}({metric_ref}) OVER (PARTITION BY EXCLUDING {excl_clause})'
                            return f'{agg_op}({rendered_base}::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause})'
                        elif isinstance(base_arg, BinaryOpNode) and base_arg.op in ("+", "-"):
                            r_left = self._render_calculate([base_arg.left] + list(filter_args))
                            r_right = self._render_calculate([base_arg.right] + list(filter_args))
                            return f"({r_left} {base_arg.op} {r_right})"
                        elif isinstance(base_arg, BinaryOpNode) and base_arg.op == "*":
                            if isinstance(base_arg.right, LiteralNode) and isinstance(base_arg.right.value, (int, float)):
                                inner_calc = self._render_calculate([base_arg.left] + list(filter_args))
                                return f"({inner_calc}) * {base_arg.right.value}"
                            elif isinstance(base_arg.left, LiteralNode) and isinstance(base_arg.left.value, (int, float)):
                                inner_calc = self._render_calculate([base_arg.right] + list(filter_args))
                                return f"{base_arg.left.value} * ({inner_calc})" 

                raise self.DaxRenderError(
                    "not applicable: needs window metric"
                )

            elif filter_clauses:
                where = " AND ".join(
                    f"({c})" if (len(filter_clauses) > 1 and re.search(r"\bOR\b", c, re.IGNORECASE)) else c
                    for c in filter_clauses
                )

                if isinstance(filtered_args[0], FunctionCallNode) and (

                    filtered_args[0].func.upper() in self._AGG_MAP or filtered_args[0].func.upper() in self._ITERATOR_MAP

                ):

                    return self._rewrite_filtered_aggregate(filtered_args[0], where)

                rewritten = self._inject_case_filter_into_rendered_aggregate(agg_expr, where)

                if rewritten:

                    return rewritten

                raise self.DaxRenderError(

                    f"Base aggregation {filtered_args[0]} is too complex to rewrite as CASE-based filtered aggregate"

                )

            else:

                return agg_expr
        finally:
            self._userel_overrides = prev_userel




    # Must stay in sync with converter/time_intelligence_shapes.py's

    # PERIOD_TO_DATE_FUNCS / LAG_FUNCS — duplicated locally (not imported)

    # to avoid a circular import (that module imports node classes from

    # this one).

    _PERIOD_TO_DATE_COMPONENT = {
        "TOTALYTD": "YTD", "TOTALMTD": "MTD", "TOTALQTD": "QTD",
        "DATESYTD": "YTD", "DATESMTD": "MTD", "DATESQTD": "QTD",
    }

    _PERIOD_TO_DATE_UNIT = {
        "TOTALYTD": "YEAR", "TOTALMTD": "MONTH", "TOTALQTD": "QUARTER",
        "DATESYTD": "YEAR", "DATESMTD": "MONTH", "DATESQTD": "QUARTER",
    }



    def _render_period_to_date(
        self, func: str, args: List[DaxNode], year_end_node: Optional[DaxNode] = None
    ) -> str:
        """
        Translate TOTALYTD / TOTALMTD / TOTALQTD (and DATESxTD modifiers) to
        Snowflake CASE WHEN scalar aggregates (no window functions).
        Pattern: TOTALYTD(expression, dates[, filter][, year_end_date])

        Shape-flag mode references a precomputed boolean flag column on the
        enriched view (anchored to the data's last date); otherwise the
        window is anchored to CURRENT_DATE() and an advisory is recorded
        (see render()). Extra filter arguments are ANDed into the window;
        a fiscal year end that is a month end shifts the year start; any
        other year end is declined.
        """
        if not args:
            raise self.DaxRenderError(f"{func} requires at least 1 argument")
        if len(args) < 2:
            raise self.DaxRenderError(f"{func} requires a dates argument")
        extra_filter_nodes: List[DaxNode] = []
        rest = list(args[2:])
        # `Date[Date]` with an unquoted table name that is also a function
        # name lexes as Identifier + [Date]: that bracket is the dates
        # column, not a filter argument.
        if rest and isinstance(args[1], IdentifierNode) and isinstance(rest[0], MeasureRefNode):
            rest = rest[1:]
        if func in ("TOTALYTD", "TOTALQTD", "TOTALMTD"):
            for extra in rest:
                if func == "TOTALYTD" and isinstance(extra, LiteralNode) and isinstance(extra.value, str):
                    year_end_node = extra
                else:
                    extra_filter_nodes.append(extra)
        date_col = self._ti_date_column_sql(args[:2])
        component = self._PERIOD_TO_DATE_COMPONENT.get(func)
        unit = self._PERIOD_TO_DATE_UNIT.get(func)
        fiscal_month: Optional[int] = None
        if year_end_node is not None:
            if not (isinstance(year_end_node, LiteralNode) and isinstance(year_end_node.value, str)):
                raise self.DaxRenderError("not applicable: fiscal year-end not supported (non-literal year_end_date)")
            try:
                fiscal_month = parse_fiscal_year_end(year_end_node.value)
            except ValueError as exc:
                raise self.DaxRenderError(f"not applicable: fiscal year-end not supported ({exc})")
            if fiscal_month and unit != "YEAR":
                raise self.DaxRenderError("not applicable: fiscal year-end not supported for this function")
        if fiscal_month:
            component = fiscal_ytd_component(fiscal_month)
        flag_name = self.anchor_flag_map.get((component,)) if component else None
        if flag_name:
            condition_sql = self.dialect.qualify(self.table_alias, flag_name)
        else:
            # No flag column provisioned for this shape (schema-blind
            # dry-run/preview, or enrichment couldn't anchor this fact
            # table) -- anchor to CURRENT_DATE(); render() records the
            # ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE advisory.
            max_date = self.dialect.current_date()
            if unit is None:
                raise self.DaxRenderError(f"Unhandled period-to-date func: {func}")
            if fiscal_month:
                start_sql = (
                    self.dialect.date_add("MONTH", fiscal_month, self.dialect.date_trunc("YEAR", self.dialect.date_add("MONTH", f"-{fiscal_month}", max_date)))
                )
            else:
                start_sql = self.dialect.date_trunc(unit, max_date)
            condition_sql = f"{date_col} >= {start_sql} AND {date_col} <= {max_date}"
        if extra_filter_nodes:
            preds = [self._render_calc_filter_predicate(n) for n in extra_filter_nodes]
            condition_sql = condition_sql + "".join(f" AND ({p})" for p in preds)
        # Unwrap the inner aggregation to build a CASE WHEN expression
        agg_node = args[0]
        if isinstance(agg_node, FunctionCallNode) and agg_node.func.upper() in self._AGG_MAP:
            inner_func = agg_node.func.upper()
            if inner_func in ("COUNTROWS", "DISTINCTCOUNT"):
                return self._rewrite_filtered_aggregate(agg_node, condition_sql)
            col_sql = self._render_node(agg_node.args[0]) if agg_node.args else self._no_arg_aggregate()
            sql_func = "AVG" if inner_func == "AVERAGE" else inner_func
            if sql_func == "COUNTA":
                sql_func = "COUNT"
            cast = self.dialect.float_cast_suffix if inner_func in ("SUM", "AVERAGE") else ""
            return f"{sql_func}(CASE WHEN {condition_sql} THEN {col_sql}{cast} END)"
        # Base argument isn't a direct aggregation call -- typically a
        # measure reference. Inject the window into ITS aggregate leaves
        # rather than wrapping an already-aggregated expression.
        agg_sql = self._render_node(agg_node)
        rewritten = self._inject_case_filter_into_rendered_aggregate(agg_sql, condition_sql)
        if rewritten:
            return rewritten
        raise self.DaxRenderError(
            f"{func}: base expression '{agg_sql}' is not a simple aggregate this "
            "renderer can safely apply a date-range filter to without risking a "
            "nested aggregate"
        )

    # Must stay in sync with converter/time_intelligence_shapes.py's LAG_FUNCS.

    _LAG_COMPONENT = {"year": "SPLY_YEAR", "quarter": "SPLY_QUARTER", "month": "SPLY_MONTH", "day": "SPLY_DAY"}



    def _lag_period_unshifted_fallback_or_raise(self, agg_sql: str, error_message: str) -> str:

        """Called at a lag-period DaxRenderError site instead of raising

        directly. Default (allow_unshifted_fallback=False): raises exactly

        as before -- no behavior change for any existing caller. Opted in:

        ships `agg_sql` completely unmodified (the referenced measure's

        real, valid SQL, just not shifted to the requested prior period)

        and records ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK instead

        of failing the whole metric closed. The caller (dax_translator.py)

        is responsible for surfacing that category so a human reviews this

        specific metric before trusting it -- this method only decides

        whether to ship the approximation, never hides that it did.

        """

        if not self._allow_unshifted_fallback:

            raise self.DaxRenderError(error_message)

        if ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK not in self._advisory_categories:

            self._advisory_categories.append(ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK)

        return agg_sql



    # Functions returning the FULL shifted period (not a same-period-to-date
    # shift): PREVIOUSYEAR/QUARTER/MONTH and PARALLELPERIOD.
    _FULL_PERIOD_LAG_FUNCS = frozenset({"PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSMONTH", "PARALLELPERIOD"})

    @staticmethod
    def lag_shape_component(func_name: Optional[str], interval: str, amount: int) -> str:
        """Canonical shape component -- identical to time_intelligence_shapes'
        discovery naming, so flag lookups and nested-shape composition agree."""
        unit = interval.upper()
        fn = (func_name or "").upper()
        n = abs(int(amount))
        if fn == "SAMEPERIODLASTYEAR":
            return "SPLY_YEAR"
        if fn in ("PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSMONTH"):
            return f"PREV_{unit}"
        if fn == "PARALLELPERIOD":
            return f"PREV_{unit}" if n == 1 else f"PREV_{n}_{unit}"
        return f"SPLY_{unit}" if n == 1 else f"SPLY_{n}_{unit}"

    def _render_lag_period(
        self, args: List[DaxNode], interval: str, amount: int, func_name: Optional[str] = None
    ) -> str:
        """
        Translate SAMEPERIODLASTYEAR / DATEADD (same period shifted) and
        PREVIOUSxxx / PARALLELPERIOD (FULL shifted period) to scalar CASE WHEN
        bounded aggregates. Snowflake METRICS clause does NOT allow window
        functions (OVER / LAG).
        """
        if not args:
            raise self.DaxRenderError("Period function requires arguments")
        if len(args) != 2:
            raise self.DaxRenderError("Period function expects (aggregate, date_column) arguments")
        date_col = self._ti_date_column_sql(args)
        max_date = self.dialect.current_date()
        unit = interval.upper()
        full_period = (func_name or "").upper() in self._FULL_PERIOD_LAG_FUNCS
        shift_expr = self.dialect.date_add(unit, amount, max_date)
        agg_node = args[0]
        lag_component = self.lag_shape_component(func_name, interval, amount)
        if getattr(self, "_comparison_context", None) and self._comparison_context.get("lag_flag"):
            base_flag = self._comparison_context["lag_flag"]
            lag_component = (self._comparison_context.get("lag_shape") or (lag_component,))[0]
        else:
            n = abs(int(amount))
            candidates = [lag_component]
            if lag_component == f"SPLY_{unit}":
                candidates.append(f"SPLY_1_{unit}")
            # For MONTH/QUARTER/DAY a same-period shift and a full-period
            # shift select the same rows, so either flag is acceptable; for
            # YEAR they differ (year-to-date vs full year) and are never mixed.
            if unit != "YEAR":
                candidates += [f"PREV_{unit}" if n == 1 else f"PREV_{n}_{unit}",
                               f"SPLY_{unit}" if n == 1 else f"SPLY_{n}_{unit}"]
            base_flag = None
            if self.anchor_flag_map:
                for cand in candidates:
                    if self.anchor_flag_map.get((cand,)):
                        base_flag = self.anchor_flag_map[(cand,)]
                        break

        def _inline_condition() -> str:
            if unit == "DAY":
                return f"{date_col} = {shift_expr}"
            if unit == "YEAR":
                if full_period:
                    return f"YEAR({date_col}) = YEAR({shift_expr})"
                return (
                    f"YEAR({date_col}) = YEAR({shift_expr}) "
                    f"AND {date_col} BETWEEN {self.dialect.date_add('YEAR', amount, self.dialect.date_trunc('YEAR', max_date))} "
                    f"AND {shift_expr}"
                )
            if unit == "QUARTER":
                return (
                    f"YEAR({date_col}) = YEAR({shift_expr}) "
                    f"AND QUARTER({date_col}) = QUARTER({shift_expr})"
                )
            if unit == "MONTH":
                return (
                    f"YEAR({date_col}) = YEAR({shift_expr}) "
                    f"AND MONTH({date_col}) = MONTH({shift_expr})"
                )
            raise self.DaxRenderError(f"not applicable: period shift by '{interval}' not supported")

        if isinstance(agg_node, FunctionCallNode) and agg_node.func.upper() in self._AGG_MAP:
            inner_func = agg_node.func.upper()
            condition_sql = self.dialect.qualify(self.table_alias, base_flag) if base_flag else _inline_condition()
            if inner_func in ("COUNTROWS", "DISTINCTCOUNT"):
                return self._rewrite_filtered_aggregate(agg_node, condition_sql)
            col_sql = self._render_node(agg_node.args[0]) if agg_node.args else self._no_arg_aggregate()
            sql_func = "AVG" if inner_func == "AVERAGE" else inner_func
            if sql_func == "COUNTA":
                sql_func = "COUNT"
            cast = self.dialect.float_cast_suffix if inner_func in ("SUM", "AVERAGE") else ""
            return f"{sql_func}(CASE WHEN {condition_sql} THEN {col_sql}{cast} END)"
        # Base argument isn't a direct aggregation call -- e.g. a measure
        # reference such as SAMEPERIODLASTYEAR([Total Sales], 'Date'[Date]).
        agg_sql = self._render_node(agg_node)
        if self.anchor_flag_map:
            # Shape-flag mode: nested composition swaps the inner shape's flag
            # for the combined shape's flag (see time_intelligence_shapes).
            for inner_shape, inner_flag in self.anchor_flag_map.items():
                inner_ref = self.dialect.qualify(self.table_alias, inner_flag)
                if inner_ref not in agg_sql:
                    continue
                combined_flag = self.anchor_flag_map.get(inner_shape + (lag_component,))
                if combined_flag:
                    return agg_sql.replace(inner_ref, self.dialect.qualify(self.table_alias, combined_flag))
                raise self.DaxRenderError(
                    f"Period function needs a flag column for the nested shape "
                    f"{inner_shape + (lag_component,)!r}, but none was provisioned -- "
                    "the shape-discovery pass and the enriched view must agree on "
                    "every nested time-intelligence composition actually used"
                )
            if base_flag:
                rewritten = self._inject_case_filter_into_rendered_aggregate(
                    agg_sql, self.dialect.qualify(self.table_alias, base_flag)
                )
                if rewritten:
                    return rewritten
                return self._lag_period_unshifted_fallback_or_raise(
                    agg_sql,
                    f"Period function base expression '{agg_sql}' is not a simple aggregate this "
                    "renderer can safely apply a date-range filter to without risking a nested aggregate",
                )
        # The referenced measure already anchors its own window (e.g. a YTD
        # measure). Same-period shifts move the anchor back by the lag;
        # full-period functions re-anchor the inner window at the LAST day
        # of the shifted period (DAX: the inner measure is evaluated over the
        # whole previous period, so its LASTDATE is that period's end).
        if full_period and unit in ("YEAR", "QUARTER", "MONTH"):
            anchor_replacement = self.dialect.last_day(shift_expr, unit)
        elif full_period:
            anchor_replacement = None
        else:
            anchor_replacement = shift_expr
        if max_date in agg_sql:
            if anchor_replacement is None:
                raise self.DaxRenderError("not applicable: full-period shift of an anchored measure by this interval")
            return agg_sql.replace(max_date, anchor_replacement)
        legacy_max_date = self.dialect.qualify(self.table_alias, "MAX_DATE")
        if legacy_max_date in agg_sql:
            legacy_shift = self.dialect.date_add(unit, amount, legacy_max_date)
            if full_period:
                if unit not in ("YEAR", "QUARTER", "MONTH"):
                    raise self.DaxRenderError("not applicable: full-period shift of an anchored measure by this interval")
                legacy_shift = self.dialect.last_day(legacy_shift, unit)
            return agg_sql.replace(legacy_max_date, legacy_shift)
        rewritten = self._inject_case_filter_into_rendered_aggregate(agg_sql, _inline_condition())
        if rewritten:
            return rewritten
        return self._lag_period_unshifted_fallback_or_raise(
            agg_sql,
            f"Period function base expression '{agg_sql}' is not a simple aggregate this "
            "renderer can safely apply a date-range filter to without risking a nested aggregate",
        )

    def _no_arg_aggregate(self) -> str:
        raise self.DaxRenderError("aggregate without an argument inside a time-intelligence function")

    def _render_dateadd(self, args: List[DaxNode]) -> str:

        """Translate DATEADD(dates, n_intervals, interval) to Snowflake DATEADD."""

        if len(args) < 3:

            raise self.DaxRenderError("DATEADD requires 3 arguments")

        # DAX signature: DATEADD(<dates>, <number>, <interval>)

        date_col = self._render_node(args[0])

        amount = self._render_node(args[1])

        interval_node = args[2]

        interval_str = (

            interval_node.name.lower()

            if isinstance(interval_node, IdentifierNode)

            else str(interval_node)

        )

        return self.dialect.date_add(interval_str, amount, date_col)


# Expose through the dax_ast_parser facade (both import orders work).
_parser.DaxSqlRenderer = DaxSqlRenderer
