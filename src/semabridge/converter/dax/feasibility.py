"""Feasibility / failure classifiers for DAX ASTs (can this measure be translated
faithfully, and if not, why), split out of dax_ast_parser unchanged.

Import public names from ``semabridge.converter.dax_ast_parser`` (the facade).
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
from semabridge.utils.relationship_graph import has_relationship_path

logger = get_logger("semabridge.converter.dax_ast_parser")

from semabridge.converter import dax_ast_parser as _parser  # noqa: E402  (facade; see bottom)
BinaryOpNode = _parser.BinaryOpNode
ColumnRefNode = _parser.ColumnRefNode
DaxAstParser = _parser.DaxAstParser
DaxNode = _parser.DaxNode
FunctionCallNode = _parser.FunctionCallNode
HASONEVALUE_GUARD_REASON = _parser.HASONEVALUE_GUARD_REASON
IdentifierNode = _parser.IdentifierNode
LiteralNode = _parser.LiteralNode
MeasureRefNode = _parser.MeasureRefNode
USERELATIONSHIP_REASON = _parser.USERELATIONSHIP_REASON
UnaryOpNode = _parser.UnaryOpNode
VarNode = _parser.VarNode
parse_fiscal_year_end = _parser.parse_fiscal_year_end
period_to_date_year_end_node = _parser.period_to_date_year_end_node
sanitize_column = _parser.sanitize_column
unwrap_var_body = _parser.unwrap_var_body







# ---------------------------------------------------------------------------

# Root-shape classifiers — used to distinguish "cannot translate this DAX"

# from "this DAX isn't a valid aggregate metric to begin with" (a modeling

# classification, not a translation gap). Both are pure AST-shape checks:

# no field/model/dataset names are ever inspected.

# ---------------------------------------------------------------------------



# Shared marker prefix for sync_failure_reason on by-design-excluded metrics.

# Any later pass that might otherwise re-attempt translating an unresolved

# metric (Tier-5 batching, cross-metric dependency resolution, etc.) must

# check for this prefix and skip — these metrics were never meant to be

# translated, so re-attempting could silently un-exclude them.

BY_DESIGN_EXCLUDED_PREFIX = "BY_DESIGN_EXCLUDED"





def _ast_contains_func(node: Optional[DaxNode], names: Set[str]) -> bool:
    """True if any FunctionCallNode in `node` has a name in `names`."""
    upper = {n.upper() for n in names}
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if isinstance(n, FunctionCallNode):
            if n.func.upper() in upper:
                return True
            stack.extend(n.args)
        elif isinstance(n, BinaryOpNode):
            stack.extend([n.left, n.right])
        elif isinstance(n, UnaryOpNode):
            stack.append(n.operand)
        elif isinstance(n, VarNode):
            stack.extend(v for _, v in n.variables)
            stack.append(n.body)
    return False


def _iter_children(n: DaxNode) -> List[DaxNode]:
    if isinstance(n, FunctionCallNode):
        return list(n.args)
    if isinstance(n, BinaryOpNode):
        return [n.left, n.right]
    if isinstance(n, UnaryOpNode):
        return [n.operand]
    if isinstance(n, VarNode):
        return [v for _, v in n.variables] + [n.body]
    return []


_GROUP_GRAIN_TABLE_FUNCS = frozenset({
    "VALUES", "DISTINCT", "SUMMARIZE", "SUMMARIZECOLUMNS", "ADDCOLUMNS", "GROUPBY",
    "TOPN", "CROSSJOIN", "UNION", "DATESYTD", "DATESMTD", "DATESQTD", "DATESINPERIOD",
    "DATESBETWEEN", "CALCULATETABLE", "SELECTCOLUMNS",
})
_SCOPE_GUARD_FUNCS = frozenset({"HASONEVALUE", "HASONEFILTER", "ISINSCOPE", "ISFILTERED", "ISCROSSFILTERED"})
_ITERATOR_FUNCS = frozenset({"SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "PRODUCTX", "CONCATENATEX"})


def dax_semantic_fidelity_failure_reason(
    parsed_ast: Optional[DaxNode],
    primary_table_name: Optional[str] = None,
) -> Optional[str]:
    """Structural pre-check for DAX shapes whose exact semantics no
    translation tier can reproduce in a semantic-view metric. Returns a
    specific, user-readable 'not applicable: ...' reason, or None.

    Runs before every translation tier so no regex/rule tier can ship an
    approximation for these shapes."""
    if parsed_ast is None:
        return None

    def _walk(n: Optional[DaxNode]) -> Optional[str]:
        if n is None:
            return None
        if isinstance(n, FunctionCallNode):
            fn = n.func.upper()
            if fn == "USERELATIONSHIP":
                return USERELATIONSHIP_REASON
            if fn == "IF" and len(n.args) >= 3:
                # IF(<guard>, ERROR(...), calc) quick-measure unwrap: only the
                # non-error branch is translated.
                for err_idx, keep_idx in ((1, 2), (2, 1)):
                    br = n.args[err_idx]
                    if isinstance(br, FunctionCallNode) and br.func.upper() == "ERROR":
                        return _walk(n.args[keep_idx])
            if fn in _SCOPE_GUARD_FUNCS:
                return HASONEVALUE_GUARD_REASON
            if fn in ("TOTALYTD", "DATESYTD"):
                ye = period_to_date_year_end_node(n)
                if ye is not None:
                    if not (isinstance(ye, LiteralNode) and isinstance(ye.value, str)):
                        return "not applicable: fiscal year-end not supported (non-literal year_end_date)"
                    try:
                        parse_fiscal_year_end(ye.value)
                    except ValueError as exc:
                        return f"not applicable: fiscal year-end not supported ({exc})"
            if fn == "TOPN":
                return "not applicable: TOPN (a top-N set that depends on the query context) is not supported"
            if fn in _ITERATOR_FUNCS and n.args:
                tbl = n.args[0]
                while isinstance(tbl, FunctionCallNode) and tbl.func.upper() in ("FILTER", "KEEPFILTERS") and tbl.args:
                    tbl = tbl.args[0]
                if isinstance(tbl, FunctionCallNode):
                    tfn = tbl.func.upper()
                    group_grain = tfn in _GROUP_GRAIN_TABLE_FUNCS or (
                        tfn in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS", "ALLSELECTED")
                        and tbl.args and isinstance(tbl.args[0], ColumnRefNode)
                    )
                    if group_grain:
                        expr = n.args[1] if len(n.args) > 1 else None
                        same_col_minmax = (
                            fn in ("MINX", "MAXX") and isinstance(expr, ColumnRefNode)
                            and tfn in ("VALUES", "DISTINCT", "ALL", "ALLNOBLANKROW")
                            and any(
                                isinstance(a, ColumnRefNode) and a.column.casefold() == expr.column.casefold()
                                for a in tbl.args
                            )
                        )
                        if not same_col_minmax:
                            what = _dax_text(tbl)
                            return (
                                f"not applicable: {fn} over {what} evaluates its expression once per group "
                                "(context transition) and re-aggregates; not expressible as a single "
                                "semantic-view metric"
                            )
                elif isinstance(tbl, IdentifierNode) and primary_table_name and len(n.args) > 1:
                    t = tbl.name.strip("'\"")
                    if t.casefold() != primary_table_name.strip("'\"").casefold() and (
                        _node_contains_measure_ref(n.args[1]) or _ast_contains_func(
                            n.args[1], {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTROWS", "DISTINCTCOUNT", "CALCULATE"}
                        )
                    ):
                        return (
                            f"not applicable: {fn} over '{t}' evaluates a measure once per '{t}' row "
                            "(context transition) and re-aggregates; not expressible as a single semantic-view metric"
                        )
        for c in _iter_children(n):
            r = _walk(c)
            if r:
                return r
        return None

    return _walk(parsed_ast)


def dax_has_overriding_calculate_filter(parsed_ast: Optional[DaxNode]) -> bool:
    """True when a CALCULATE (or TOTALxTD filter argument) applies a filter
    that DAX evaluates as a context OVERRIDE on a column (boolean predicate,
    FILTER(ALL(...)), DATESBETWEEN/DATESINPERIOD, time-intelligence date
    filters). The SUM(CASE WHEN ...) translation intersects instead, which is
    only equal when the query does not group/filter by that same column.
    KEEPFILTERS(...) and FILTER(<table>, ...) are true intersections."""
    if parsed_ast is None:
        return False

    def _is_override(f: DaxNode) -> bool:
        if isinstance(f, (BinaryOpNode, UnaryOpNode)):
            return True
        if isinstance(f, FunctionCallNode):
            fn = f.func.upper()
            if fn == "KEEPFILTERS":
                return False
            if fn == "FILTER" and f.args:
                t = f.args[0]
                return isinstance(t, FunctionCallNode) and t.func.upper() in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS", "ALLSELECTED")
            if fn in ("NOT", "ISBLANK", "DATESBETWEEN", "DATESINPERIOD", "DATESYTD", "DATESQTD", "DATESMTD",
                      "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSMONTH", "DATEADD",
                      "PARALLELPERIOD", "TOTALYTD", "TOTALQTD", "TOTALMTD"):
                return True
        return False

    stack = [parsed_ast]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if isinstance(n, FunctionCallNode):
            fn = n.func.upper()
            if fn == "CALCULATE" and any(_is_override(a) for a in n.args[1:]):
                return True
            if fn in ("TOTALYTD", "TOTALQTD", "TOTALMTD"):
                return True
            if fn in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSMONTH"):
                return True
        stack.extend(_iter_children(n))
    return False


def is_by_design_excluded(sync_failure_reason: Optional[str]) -> bool:
    """True if `sync_failure_reason` marks a metric as by-design excluded
    (constant expression / string-producing root / non-additive dependency / not applicable)
    rather than a genuine, still-open DAX translation failure."""
    reason_str = str(sync_failure_reason or "").lower()
    return (
        reason_str.startswith(BY_DESIGN_EXCLUDED_PREFIX.lower())
        or "not applicable" in reason_str
        or "depends on non-additive" in reason_str
    )


_STRING_PRODUCING_FUNCS = {
    "CONCATENATE", "LEFT", "RIGHT", "MID", "FORMAT", "SUBSTITUTE", "REPT",
    "TRIM", "UPPER", "LOWER", "EXACT", "FIND", "SEARCH", "REPLACE",
    "CONCATENATEX", "COMBINEVALUES", "UNICHAR", "FIXED",
}


def _is_empty_branch(node: Optional[DaxNode]) -> bool:
    """True if node represents an empty DAX branch: empty string '', BLANK(), or None."""
    if node is None:
        return True
    if isinstance(node, LiteralNode):
        val = node.value
        if isinstance(val, str) and val.strip() == "":
            return True
        if val is None:
            return True
    if isinstance(node, FunctionCallNode) and node.func.upper() == "BLANK":
        return True
    return False


def dax_numeric_literal_value(node_or_dax: Union[DaxNode, str]) -> Optional[str]:
    """If node_or_dax represents only a numeric literal (integer or float, positive or negative),
    returns its string representation. Otherwise returns None.
    Empty expressions, text literals, and booleans return None."""
    if isinstance(node_or_dax, str):
        if not node_or_dax or not node_or_dax.strip():
            return None
        try:
            node = DaxAstParser().parse(node_or_dax)
        except Exception:
            return None
    else:
        node = node_or_dax

    if node is None:
        return None
    node = unwrap_var_body(node)

    if isinstance(node, LiteralNode):
        val = node.value
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return str(val)
    elif isinstance(node, UnaryOpNode) and node.op in ("+", "-"):
        if isinstance(node.operand, LiteralNode):
            val = node.operand.value
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return f"{node.op}{val}"
    return None


def _is_text_producing_node(node: DaxNode) -> bool:
    if isinstance(node, LiteralNode):
        if isinstance(node.value, str):
            # An empty string literal "" is an empty branch/blank in DAX, not a real text result
            return node.value.strip() != ""
        return False
    if isinstance(node, BinaryOpNode):
        if node.op == "&":
            return True
        if node.op in ("+", "-", "*", "/", "^"):
            return False
        if node.op in ("=", "<>", "<", "<=", ">", ">="):
            return False
    if isinstance(node, FunctionCallNode):
        func = node.func.upper()
        if func in _STRING_PRODUCING_FUNCS:
            return True
        if func == "IF" and len(node.args) >= 2:
            then_b = node.args[1]
            else_b = node.args[2] if len(node.args) >= 3 else None
            # Rule A: IF(cond, <numeric>, "") or IF(cond, <numeric>, BLANK()) -> CASE WHEN cond THEN <numeric> END.
            # Only when the non-empty branch is numeric; real text values stay NULL ("text result").
            if _is_empty_branch(else_b):
                return _is_text_producing_node(then_b)
            if _is_empty_branch(then_b):
                return _is_text_producing_node(else_b) if else_b is not None else False
            return _is_text_producing_node(then_b) or _is_text_producing_node(else_b)
        if func == "SWITCH":
            for i in range(2, len(node.args), 2):
                if _is_text_producing_node(node.args[i]):
                    return True
            if len(node.args) % 2 == 0 and len(node.args) > 2:
                if _is_text_producing_node(node.args[-1]):
                    return True
    if isinstance(node, VarNode):
        return _is_text_producing_node(node.body)
    return False


def _refs_any_data(node: DaxNode) -> bool:
    if isinstance(node, (ColumnRefNode, MeasureRefNode)):
        return True
    if isinstance(node, BinaryOpNode):
        return _refs_any_data(node.left) or _refs_any_data(node.right)
    if isinstance(node, UnaryOpNode):
        return _refs_any_data(node.operand)
    if isinstance(node, FunctionCallNode):
        func = node.func.upper()
        if func in ("COUNTROWS", "COUNTAX", "SUMX", "AVERAGEX", "MINX", "MAXX", "SELECTEDVALUE"):
            return True
        return any(_refs_any_data(a) for a in node.args)
    if isinstance(node, VarNode):
        return any(_refs_any_data(v) for _, v in node.variables) or _refs_any_data(node.body)
    return False


def _has_treatas(node: Optional[DaxNode]) -> bool:
    if node is None:
        return False
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() == "TREATAS":
                return True
            stack.extend(cur.args)
        elif isinstance(cur, BinaryOpNode):
            stack.extend([cur.left, cur.right])
        elif isinstance(cur, UnaryOpNode):
            stack.append(cur.operand)
        elif isinstance(cur, VarNode):
            for _, expr in getattr(cur, "variables", []):
                stack.append(expr)
            stack.append(getattr(cur, "body", None))
    return False


def _has_ranking_function(node: DaxNode) -> bool:
    res = False
    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() in ("RANKX", "ISINSCOPE", "RANK", "DENSE_RANK", "ROWNUMBER"):
                res = True
                return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def _has_time_intel_function(node: DaxNode) -> bool:
    res = False
    time_funcs = {
        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER",
        "DATEADD", "PARALLELPERIOD", "TOTALYTD", "TOTALMTD", "TOTALQTD",
    }
    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() in time_funcs:
                res = True
                return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def _extract_closing_balance_dim(node: DaxNode) -> Optional[Tuple[Optional[str], str]]:
    pred_node = node
    if isinstance(node, FunctionCallNode) and node.func.upper() == "FILTER" and len(node.args) >= 2:
        pred_node = node.args[1]
    if isinstance(pred_node, BinaryOpNode) and pred_node.op in ("=", "=="):
        if isinstance(pred_node.left, ColumnRefNode) and isinstance(pred_node.right, FunctionCallNode) and pred_node.right.func.upper() == "MAX":
            return (pred_node.left.table, pred_node.left.column)
        if isinstance(pred_node.right, ColumnRefNode) and isinstance(pred_node.left, FunctionCallNode) and pred_node.left.func.upper() == "MAX":
            return (pred_node.right.table, pred_node.right.column)
    return None


def _has_closing_balance(node: DaxNode) -> bool:
    if isinstance(node, FunctionCallNode) and node.func.upper() == "CALCULATE":
        for a in node.args[1:]:
            if _extract_closing_balance_dim(a):
                return True
    return False


def _has_selectedvalue_in_filter_all(node: DaxNode) -> bool:
    res = False
    def _has_selectedvalue(n):
        if n is None:
            return False
        if isinstance(n, FunctionCallNode):
            if n.func.upper() == "SELECTEDVALUE":
                return True
            return any(_has_selectedvalue(a) for a in n.args)
        if isinstance(n, BinaryOpNode):
            return _has_selectedvalue(n.left) or _has_selectedvalue(n.right)
        if isinstance(n, UnaryOpNode):
            return _has_selectedvalue(n.operand)
        return False

    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() == "FILTER" and len(cur.args) >= 2:
                first_arg = cur.args[0]
                is_all_table = False
                if isinstance(first_arg, FunctionCallNode) and first_arg.func.upper() in ("ALL", "ALLEXCEPT", "ALLSELECTED"):
                    is_all_table = True
                if is_all_table and any(_has_selectedvalue(a) for a in cur.args[1:]):
                    res = True
                    return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def _get_tables_in_node(node: DaxNode) -> Set[str]:
    tables: Set[str] = set()
    def _walk(cur):
        if cur is None:
            return
        if isinstance(cur, ColumnRefNode) and cur.table:
            tables.add(cur.table.strip("'"))
        for child in getattr(cur, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand"):
            if hasattr(cur, attr) and getattr(cur, attr) is not None:
                _walk(getattr(cur, attr))
        if isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return tables


def _is_selector_over_disconnected_table(node: DaxNode, relationships: Optional[Iterable[Any]]) -> bool:
    if not isinstance(node, FunctionCallNode) or node.func.upper() not in ("IF", "SWITCH"):
        return False
    if not relationships:
        return False
    connected: Set[str] = set()
    for r in relationships:
        ft = getattr(r, "from_dataset", None) or getattr(r, "from_table", None)
        tt = getattr(r, "to_dataset", None) or getattr(r, "to_table", None)
        if ft:
            connected.add(str(ft).casefold())
        if tt:
            connected.add(str(tt).casefold())

    if node.func.upper() == "IF" and len(node.args) >= 2:
        cond_tables = _get_tables_in_node(node.args[0])
        if cond_tables and all(t.casefold() not in connected for t in cond_tables):
            branch_tables: Set[str] = set()
            for b in node.args[1:]:
                branch_tables.update(_get_tables_in_node(b))
            other_tables = [t for t in branch_tables if not any(t.casefold() == ct.casefold() for ct in cond_tables)]
            if other_tables:
                return True
    elif node.func.upper() == "SWITCH" and len(node.args) >= 3:
        cond_tables = _get_tables_in_node(node.args[0])
        if cond_tables and all(t.casefold() not in connected for t in cond_tables):
            branch_tables: Set[str] = set()
            for i in range(2, len(node.args), 2):
                branch_tables.update(_get_tables_in_node(node.args[i]))
            if len(node.args) % 2 == 0 and len(node.args) > 2:
                branch_tables.update(_get_tables_in_node(node.args[-1]))
            other_tables = [t for t in branch_tables if not any(t.casefold() == ct.casefold() for ct in cond_tables)]
            if other_tables:
                return True
    return False


def _get_measure_refs(n: DaxNode) -> Set[str]:
    refs: Set[str] = set()
    def _walk(cur):
        if isinstance(cur, MeasureRefNode):
            refs.add(cur.name)
        for child in getattr(cur, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand"):
            if hasattr(cur, attr) and getattr(cur, attr) is not None:
                _walk(getattr(cur, attr))
        if isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(n)
    return refs


def _is_enrichable_all_measure(node: DaxNode, in_division: bool = False) -> bool:
    try:
        from semabridge.converter.whole_table_all import match_whole_table_all_ast
        if match_whole_table_all_ast(node) is not None:
            return True
    except Exception:
        pass
    if isinstance(node, FunctionCallNode):
        func = node.func.upper()
        if func == "DIVIDE" and len(node.args) >= 2:
            return _is_enrichable_all_measure(node.args[0], in_division=in_division) or _is_enrichable_all_measure(node.args[1], in_division=True)
        if func == "CALCULATE" and len(node.args) >= 2:
            has_all = False
            has_disqualifying_filter = False
            for a in node.args[1:]:
                if isinstance(a, FunctionCallNode) and a.func.upper() in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):
                    if not a.args:
                        has_disqualifying_filter = True
                    elif not in_division:
                        has_col = any(
                            isinstance(arg, (ColumnRefNode, MeasureRefNode))
                            or (isinstance(arg, IdentifierNode) and ("[" in getattr(arg, "name", "") or "." in getattr(arg, "name", "")))
                            for arg in a.args
                        )
                        if not has_col:
                            has_disqualifying_filter = True
                        else:
                            has_all = True
                    else:
                        has_all = True
                elif isinstance(a, (BinaryOpNode, FunctionCallNode)):
                    if not in_division:
                        has_disqualifying_filter = True
                    else:
                        pass
                else:
                    has_disqualifying_filter = True

            if has_all and not has_disqualifying_filter:
                if _has_allselected(node):
                    return False
                if _has_non_decomposable_aggregate(node):
                    return False
                return True
            return False

        return any(_is_enrichable_all_measure(a, in_division=in_division) for a in node.args if _calculate_has_direct_all_or_allexcept_modifier(a))
    if isinstance(node, BinaryOpNode):
        if node.op == "/":
            return _is_enrichable_all_measure(node.left, in_division=in_division) or _is_enrichable_all_measure(node.right, in_division=True)
        left_ok = _is_enrichable_all_measure(node.left, in_division=in_division) if _calculate_has_direct_all_or_allexcept_modifier(node.left) else False
        right_ok = _is_enrichable_all_measure(node.right, in_division=in_division) if _calculate_has_direct_all_or_allexcept_modifier(node.right) else False
        return left_ok or right_ok
    if isinstance(node, UnaryOpNode):
        return _is_enrichable_all_measure(node.operand, in_division=in_division)
    if isinstance(node, VarNode):
        return _is_enrichable_all_measure(node.body, in_division=in_division)
    return False

def _has_non_literal_dateadd(node: DaxNode) -> bool:
    if isinstance(node, FunctionCallNode):
        if node.func.upper() == "DATEADD":
            if len(node.args) >= 2:
                arg1 = node.args[1]
                if isinstance(arg1, UnaryOpNode) and arg1.op == "-":
                    if not isinstance(arg1.operand, LiteralNode):
                        return True
                    try:
                        val = arg1.operand.value
                        if isinstance(val, int) and not isinstance(val, bool):
                            pass
                        elif isinstance(val, str) and re.match(r"^\d+$", val.strip()):
                            pass
                        else:
                            return True
                    except Exception:
                        return True
                elif isinstance(arg1, LiteralNode):
                    try:
                        val = arg1.value
                        if isinstance(val, int) and not isinstance(val, bool):
                            pass
                        elif isinstance(val, str) and re.match(r"^-?\d+$", val.strip()):
                            pass
                        else:
                            return True
                    except Exception:
                        return True
                else:
                    return True
        return any(_has_non_literal_dateadd(a) for a in node.args)
    if isinstance(node, BinaryOpNode):
        return _has_non_literal_dateadd(node.left) or _has_non_literal_dateadd(node.right)
    if isinstance(node, UnaryOpNode):
        return _has_non_literal_dateadd(node.operand)
    if isinstance(node, VarNode):
        return any(_has_non_literal_dateadd(v[1]) for v in node.variables) or _has_non_literal_dateadd(node.body)
    return False

def _has_case_when_or_if(node: DaxNode) -> bool:
    res = False
    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() in ("IF", "SWITCH"):
                res = True
                return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def _has_window_construct(node: DaxNode) -> bool:
    res = False
    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() in (
                "RANKX", "RANK", "DENSE_RANK", "ROWNUMBER", "ISINSCOPE",
                "ALLSELECTED", "WINDOW", "OFFSET", "INDEX"
            ):
                res = True
                return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def classify_dax_ast_not_applicable(
    parsed_ast: DaxNode,
    relationships: Optional[Iterable[Any]] = None,
    non_additive_map: Optional[Dict[str, Any]] = None,
    metrics_context: Optional[Iterable[Any]] = None,
    allow_window_enrichment: bool = False,
) -> Optional[str]:
    """Classify unrepresentable DAX patterns from AST and model metadata before SQL rendering:
    1. text result -> 'not applicable: text result'
    2. constant -> 'not applicable: constant' (numeric literals translated as MAX(<literal>))
    3. ranking -> 'not applicable: needs window metric (ranking)'
    4. window metric -> 'not applicable: needs window metric'
    5. non-additive closing balance + time intelligence -> 'not applicable: depends on non-additive metric'
    5. non-additive dependency -> 'not applicable: depends on non-additive metric [X]'
    6. disconnected selector -> 'not applicable: selector over a disconnected table'
    """
    if parsed_ast is None:
        return None
    dax_str = str(parsed_ast).upper()
    if "__FIRSTDATEVISIBLE <= __LASTDATEWITHDATA" in dax_str or "FIRSTDATEVISIBLE <= LASTDATEWITHDATA" in dax_str:
        return None

    if _has_non_literal_dateadd(parsed_ast):
        return "not applicable: DATEADD shift amount must be a literal integer"

    node = unwrap_var_body(parsed_ast)

    # 1. Text result
    if _is_text_producing_node(node):
        return "not applicable: text result"

    # 2. Compile-time constant (Rule B: numeric literal -> constant metric MAX(<literal>))
    if not _refs_any_data(parsed_ast):
        if dax_numeric_literal_value(parsed_ast) is not None:
            pass
        else:
            return "not applicable: constant"

    # CASE WHEN / IF over a window metric (Snowflake error 010258 / 010273)
    if _has_case_when_or_if(parsed_ast) and _has_window_construct(parsed_ast):
        return "not applicable: CASE WHEN over a window metric is not supported in Snowflake semantic views"

    # Virtual relationships (TREATAS)
    if _has_treatas(parsed_ast):
        return "not applicable: virtual relationship (TREATAS) not supported in semantic views"

    # 3. Ranking functions
    if _has_ranking_function(parsed_ast):
        return "not applicable: needs window metric (ranking)"

    # 4. Window metric & report selection
    if _has_allselected(parsed_ast):
        return "not applicable: depends on report selection (ALLSELECTED)"
    if _has_selectedvalue_in_filter_all(parsed_ast):
        if not _is_leave_one_out(parsed_ast) or _has_non_decomposable_aggregate(parsed_ast):
            return "not applicable: needs window metric"
    if _calculate_has_direct_all_or_allexcept_modifier(parsed_ast):
        if _is_enrichable_all_measure(parsed_ast):
            pass
        elif _is_leave_one_out(parsed_ast):
            if _has_non_decomposable_aggregate(parsed_ast):
                return "not applicable: not decomposable for a window"
        elif _calculate_has_allexcept_modifier(parsed_ast):
            if _has_non_decomposable_aggregate(parsed_ast):
                return "not applicable: not decomposable for a window"
        elif _has_non_decomposable_aggregate(parsed_ast):
            return "not applicable: not decomposable for a window"
        else:
            return "not applicable: not decomposable for a window"

    # 5. Non-additive closing balance + time intelligence
    if _has_closing_balance(parsed_ast) and _has_time_intel_function(parsed_ast):
        return "not applicable: depends on non-additive metric"

    # 5. Non-additive dependencies
    if non_additive_map:
        all_refs = _get_measure_refs(parsed_ast)
        non_add_refs = [
            m for m in all_refs
            if any(m.casefold() == k.casefold() for k in non_additive_map)
        ]
        if non_add_refs:
            first_ref = next((m for m in non_add_refs if non_additive_map.get(m) and non_additive_map[m][0]), non_add_refs[0])
            # CASE WHEN over a semi-additive metric triggers Snowflake compilation error 010902
            if _has_case_when_or_if(parsed_ast):
                return f"not applicable: CASE WHEN over a semi-additive metric is not supported in Snowflake semantic views [{first_ref}]"

            is_pure_filtered = False
            if isinstance(node, MeasureRefNode):
                is_pure_filtered = True
            elif _is_pure_ratio_or_arithmetic_over_metrics(node):
                is_pure_filtered = True
            elif (
                isinstance(node, FunctionCallNode)
                and node.func.upper() == "CALCULATE"
                and len(node.args) >= 1
                and isinstance(node.args[0], MeasureRefNode)
            ):
                filter_refs: Set[str] = set()
                has_time_intel = False
                for f_arg in node.args[1:]:
                    filter_refs.update(_get_measure_refs(f_arg))
                    if _has_time_intel_function(f_arg):
                        has_time_intel = True
                if not filter_refs and not has_time_intel:
                    is_pure_filtered = True

            if not is_pure_filtered:
                return f"not applicable: depends on non-additive metric [{first_ref}]"

    # 6. Disconnected selector
    if relationships is not None:
        if _is_selector_over_disconnected_table(node, relationships):
            return "not applicable: selector over a disconnected table"

    return None


def dax_root_is_string_producing(dax: str) -> bool:

    """

    True if the DAX expression's root/outermost operation produces a string

    value — a bare string literal, or a call to a string-producing function

    — rather than a numeric aggregate.



    Such an expression cannot be represented in a Snowflake semantic-view

    METRICS clause (which requires an aggregate expression); it should be

    classified as a dimension/attribute instead, not attempted as a metric

    translation.

    """

    node = unwrap_var_body(DaxAstParser().parse(dax))

    if isinstance(node, LiteralNode):

        return isinstance(node.value, str) and node.value.strip() != ""

    if isinstance(node, FunctionCallNode):

        if node.func.upper() == "IF" and len(node.args) >= 2:
            then_b = node.args[1]
            else_b = node.args[2] if len(node.args) >= 3 else None
            if _is_empty_branch(else_b):
                return _is_text_producing_node(then_b)
            if _is_empty_branch(then_b):
                return _is_text_producing_node(else_b) if else_b is not None else False
            return _is_text_producing_node(then_b) or _is_text_producing_node(else_b)

        return node.func.upper() in _STRING_PRODUCING_FUNCS

    return False





def dax_has_zero_data_dependencies(dax: str) -> bool:

    """

    True if the DAX expression references no column, measure, or table

    anywhere in its parse tree — a compile-time constant (e.g. a bare

    string/numeric literal, or arithmetic over literals only).



    Such an expression has nothing to aggregate and should be classified as

    by-design excluded, not attempted as a translation or reported as a

    DAX-translation failure.

    """

    # Rule B: A measure whose DAX is only a numeric literal is translated as a constant metric MAX(<literal>)
    if dax_numeric_literal_value(dax) is not None:
        return False

    node = DaxAstParser().parse(dax)

    if node is None:

        return False



    def _refs_data(n: DaxNode) -> bool:

        if isinstance(n, (ColumnRefNode, MeasureRefNode)):

            return True

        if isinstance(n, BinaryOpNode):

            return _refs_data(n.left) or _refs_data(n.right)

        if isinstance(n, UnaryOpNode):

            return _refs_data(n.operand)

        if isinstance(n, FunctionCallNode):

            if n.func.upper() in ("COUNTROWS", "COUNTAX", "SUMX", "AVERAGEX", "MINX", "MAXX"):

                return True

            return any(_refs_data(a) for a in n.args)

        if isinstance(n, VarNode):

            # A VAR is a named sub-expression, not a boundary that hides

            # what it references — a reference inside a variable's own

            # definition (e.g. `VAR _today = [Today]`) is just as much a

            # real data dependency as one in the RETURN body. Check both,

            # for however many variables this block defines.

            return (

                any(_refs_data(value_expr) for _, value_expr in n.variables)

                or _refs_data(n.body)

            )

        return False



    return not _refs_data(node)





_CONTEXT_TRANSITION_REASON = "DAX ALLEXCEPT/ALL requires re-partitioning the aggregate independently of the query"

def _has_allselected(node: DaxNode) -> bool:
    found = False
    def _walk(n: DaxNode):
        nonlocal found
        if found or n is None:
            return
        if isinstance(n, FunctionCallNode) and n.func.upper() == "ALLSELECTED":
            found = True
            return
        for child in getattr(n, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand", "body"):
            if hasattr(n, attr):
                _walk(getattr(n, attr))
    _walk(node)
    return found

def _has_non_decomposable_aggregate(node: DaxNode) -> bool:
    found = False
    def _walk(n: DaxNode):
        nonlocal found
        if found or n is None:
            return
        if isinstance(n, FunctionCallNode) and n.func.upper() in (
            "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK", "MEDIAN", "MEDIANX", "PERCENTILE"
        ):
            found = True
            return
        for child in getattr(n, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand", "body"):
            if hasattr(n, attr):
                _walk(getattr(n, attr))
    _walk(node)
    return found

def _calculate_has_direct_all_or_allexcept_modifier(node: DaxNode) -> bool:

    """True if any CALCULATE(...) anywhere in the tree has ALL(...) or

    ALLEXCEPT(...) as one of its own direct filter-modifier arguments —

    the exact shape DaxSqlRenderer._render_calculate treats as a

    context-transition/window-function case (sets is_window=True).



    Deliberately NOT a text/regex search for "ALL(" or "ALLEXCEPT("

    anywhere in the expression: ALL(...) also appears legitimately nested

    inside FILTER(...)'s own first argument (e.g. CALCULATE(measure,

    FILTER(ALL(table), predicate))) — DAX's idiom for "filter the whole

    table regardless of context," a normal, different, sometimes-

    resolvable filtered-aggregate pattern, not a window-function one. Only

    an ALL/ALLEXCEPT that CALCULATE sees as its own direct argument

    triggers the window-function path; walking the same structure

    _render_calculate itself dispatches on keeps this classifier exactly

    consistent with the renderer's actual behavior.

    """

    if isinstance(node, FunctionCallNode):

        if node.func.upper() == "CALCULATE":

            for filter_arg in node.args[1:]:

                if isinstance(filter_arg, FunctionCallNode) and filter_arg.func.upper() in ("ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS", "ALLNOBLANKROW"):

                    return True

        return any(_calculate_has_direct_all_or_allexcept_modifier(a) for a in node.args)

    if isinstance(node, BinaryOpNode):

        return (

            _calculate_has_direct_all_or_allexcept_modifier(node.left)

            or _calculate_has_direct_all_or_allexcept_modifier(node.right)

        )

    if isinstance(node, UnaryOpNode):

        return _calculate_has_direct_all_or_allexcept_modifier(node.operand)

    if isinstance(node, VarNode):

        return any(

            _calculate_has_direct_all_or_allexcept_modifier(value_expr)

            for _, value_expr in node.variables

        ) or _calculate_has_direct_all_or_allexcept_modifier(node.body)

    return False





def _is_leave_one_out(node: DaxNode) -> bool:
    if node is None:
        return False
    found = False
    def _walk(n: DaxNode):
        nonlocal found
        if n is None or found:
            return
        if isinstance(n, FunctionCallNode) and n.func.upper() == "CALCULATE" and len(n.args) >= 2:
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
                                    found = True
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
    return found


def _calculate_has_allexcept_modifier(node: DaxNode) -> bool:
    if node is None:
        return False
    if isinstance(node, FunctionCallNode):
        if node.func.upper() == "CALCULATE":
            for filter_arg in node.args[1:]:
                if isinstance(filter_arg, FunctionCallNode) and filter_arg.func.upper() == "ALLEXCEPT":
                    return True
        return any(_calculate_has_allexcept_modifier(a) for a in node.args)
    if isinstance(node, BinaryOpNode):
        return _calculate_has_allexcept_modifier(node.left) or _calculate_has_allexcept_modifier(node.right)
    if isinstance(node, UnaryOpNode):
        return _calculate_has_allexcept_modifier(node.operand)
    if isinstance(node, VarNode):
        return any(_calculate_has_allexcept_modifier(ve) for _, ve in node.variables) or _calculate_has_allexcept_modifier(node.body)
    return False


def _is_pure_ratio_or_arithmetic_over_metrics(n: DaxNode) -> bool:
    if n is None:
        return False
    if isinstance(n, MeasureRefNode):
        return True
    if isinstance(n, LiteralNode):
        return True
    if isinstance(n, BinaryOpNode) and n.op in ("+", "-", "*", "/"):
        return _is_pure_ratio_or_arithmetic_over_metrics(n.left) and _is_pure_ratio_or_arithmetic_over_metrics(n.right)
    if isinstance(n, FunctionCallNode) and n.func.upper() in ("DIVIDE", "ROUND", "ROUNDUP", "ROUNDDOWN"):
        return all(_is_pure_ratio_or_arithmetic_over_metrics(a) for a in n.args)
    if isinstance(n, UnaryOpNode) and n.op in ("+", "-"):
        return _is_pure_ratio_or_arithmetic_over_metrics(n.operand)
    return False


def dax_context_transition_failure_reason(dax: str) -> Optional[str]:

    """

    Returns a specific, accurate failure reason if `dax` contains

    CALCULATE(agg, ALL(...)) or CALCULATE(agg, ALLEXCEPT(...)) — a genuine

    DAX context-transition pattern with no SQL equivalent inside

    Snowflake's semantic-view METRICS clause. ALLEXCEPT/ALL mean

    "recompute this aggregate re-partitioned independently of whatever

    dimensions the query itself groups by" — inherently a SQL window

    function, which Snowflake's METRICS clause forbids (see

    DaxSqlRenderer._render_calculate, which fails closed on this shape

    rather than emitting one). This codebase has no derived-metric/

    precomputed-column mechanism to materialize the windowed value some

    other way, so the pattern is genuinely unsupported today, not just

    untranslated.



    Returns None for any other DAX shape (including ALL(...) nested inside

    FILTER(...), a different and sometimes-resolvable idiom — see

    _calculate_has_direct_all_or_allexcept_modifier), so callers fall back

    to their own generic translation-failure message.

    """

    if not dax:
        return None

    clean_dax_upper = re.sub(r"\s+", " ", str(dax).upper())
    if "__FIRSTDATEVISIBLE <= __LASTDATEWITHDATA" in clean_dax_upper or "FIRSTDATEVISIBLE <= LASTDATEWITHDATA" in clean_dax_upper:
        return None

    node = DaxAstParser().parse(dax)

    if node is None:

        return None

    if _calculate_has_direct_all_or_allexcept_modifier(node):

        if _is_enrichable_all_measure(node):

            return None

        if _is_leave_one_out(node):
            if _has_non_decomposable_aggregate(node):
                return "not applicable: leave-one-out not decomposable"
            return None

        if _calculate_has_allexcept_modifier(node):
            if _has_non_decomposable_aggregate(node):
                return "not applicable: not decomposable for a window"
            return None

        if _has_allselected(node):
            return "not applicable: depends on report selection (ALLSELECTED)"
        if _has_non_decomposable_aggregate(node):
            return "not applicable: not decomposable for a window"
        return _CONTEXT_TRANSITION_REASON

    return None





_LAG_PERIOD_FUNCS = ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER")





def _calculate_lag_period_of_measure_reference(node: DaxNode) -> Optional[str]:

    """Returns the referenced measure's name if any CALCULATE(...)

    anywhere in the tree wraps a bare measure reference (not a direct

    aggregate call) with a SAMEPERIODLASTYEAR/PREVIOUSxxx filter modifier

    as its own direct argument — the shape DaxSqlRenderer._render_lag_period's

    measure-reference fallback handles by inlining that measure's own

    resolved SQL (shifting its date anchor, or injecting a new period

    filter if it has none). Mirrors

    _calculate_has_direct_all_or_allexcept_modifier's AST-walk approach —

    a text/regex search would risk matching the function name appearing

    nested somewhere unrelated.

    """

    if isinstance(node, FunctionCallNode):

        if node.func == "CALCULATE" and node.args:

            base = node.args[0]

            if isinstance(base, MeasureRefNode):

                for filter_arg in node.args[1:]:

                    if isinstance(filter_arg, FunctionCallNode) and filter_arg.func in _LAG_PERIOD_FUNCS:

                        return base.name

        for a in node.args:

            found = _calculate_lag_period_of_measure_reference(a)

            if found:

                return found

        return None

    if isinstance(node, BinaryOpNode):

        return (

            _calculate_lag_period_of_measure_reference(node.left)

            or _calculate_lag_period_of_measure_reference(node.right)

        )

    if isinstance(node, UnaryOpNode):

        return _calculate_lag_period_of_measure_reference(node.operand)

    if isinstance(node, VarNode):

        for _, value_expr in node.variables:

            found = _calculate_lag_period_of_measure_reference(value_expr)

            if found:

                return found

        return _calculate_lag_period_of_measure_reference(node.body)

    return None





def dax_lag_period_of_measure_reference_failure_reason(dax: str) -> Optional[str]:

    """

    Returns a specific, accurate failure reason if `dax` contains

    CALCULATE([SomeMeasure], SAMEPERIODLASTYEAR(...)/PREVIOUSYEAR(...)/

    PREVIOUSMONTH(...)/PREVIOUSQUARTER(...)) — a measure reference (not a

    direct aggregate) wrapped in a prior-period filter.



    Translating this requires inlining the referenced measure's own

    resolved SQL and shifting its date anchor (or, if it has none,

    injecting a new period filter) — see

    DaxSqlRenderer._render_lag_period. If this still failed to translate,

    the referenced measure either hasn't been resolved yet (a normal,

    later-pass-clears-it deferral) or its SQL shape was too complex to

    safely rewrite — either way, "DAX translation failed" alone doesn't

    say why, and this used to fail differently and worse: emitting a bare

    reference to the other metric by name (rejected by Snowflake with "a

    metric must directly refer to another aggregate-level expression") or

    a self-contradictory always-empty filter, neither of which is this

    function's concern to restate — it only describes the current,

    corrected failure mode.



    Returns None for any other DAX shape, so callers fall back to their

    own generic translation-failure message.

    """

    if not dax:
        return None

    clean_dax_upper = re.sub(r"\s+", " ", str(dax).upper())
    if "__FIRSTDATEVISIBLE <= __LASTDATEWITHDATA" in clean_dax_upper or "FIRSTDATEVISIBLE <= LASTDATEWITHDATA" in clean_dax_upper:
        return None

    node = DaxAstParser().parse(dax)

    if node is None:

        return None

    measure_name = _calculate_lag_period_of_measure_reference(node)

    if measure_name is None:

        return None

    return (

        f"DAX SAMEPERIODLASTYEAR/PREVIOUSYEAR/PREVIOUSMONTH/PREVIOUSQUARTER wraps "

        f"measure [{measure_name}] rather than a direct aggregate — requires that "

        f"measure's own resolved SQL to inline and shift its date window. Not yet "

        f"resolvable: either [{measure_name}] hasn't been translated yet, or its "

        f"SQL shape is too complex to safely rewrite."

    )





# ---------------------------------------------------------------------------

# CALCULATE-through-a-disconnected-dimension detection

# ---------------------------------------------------------------------------

#

# The DAX idiom this section detects: CALCULATE(measure_or_agg, <filter on

# TableX>) where the metric declaring this expression is itself based on a

# table with no relationship path to TableX. In DAX this is completely

# normal — CALCULATE's filter arguments apply through the model's whole

# evaluation context, not through the calling measure's own relationship

# path — but Snowflake's semantic-view compiler binds each METRICS-clause

# entry to ONE base-table alias and validates every column reference inside

# its expression against THAT table's relationship graph, independent of

# how the SQL is shaped (rejected with "A metric cannot refer to another

# dimension from an unrelated entity", error 010211). No amount of

# expression-level rewriting fixes this — DaxSqlRenderer._render_calculate

# already rewrites CALCULATE([Measure], filter) into a CASE-based filtered

# aggregate (see _inject_case_filter_into_rendered_aggregate), and the

# result still gets rejected, because the problem is which table the

# metric is declared under, not the shape of its SQL text. This is

# therefore a DETECTOR only, producing a precise, well-explained reason —

# not a rewriter (see Docs/decisions or the KPI01/KPI02 investigation this

# was written for).





# Stable category code for this failure class, carried on

# UnreachableDimensionFilter.category and mirrored onto

# SMLMetric.advisory_categories by osi_to_sml.py so downstream consumers

# (project_mapping_engine.py's predicted-failure status computation) can

# key off this structural signal instead of matching the free-text reason

# string. Not specific to any one model's metric names (e.g. KPI01/KPI02) —

# it fires for any metric on any model whose CALCULATE(...) filters an

# unreachable table.

ADVISORY_CATEGORY_UNREACHABLE_DIMENSION = "unreachable_dimension"





@dataclass

class UnreachableDimensionFilter:

    """A CALCULATE(...) filter that references a dimension with no

    relationship path from the calling metric's own base table — the

    shape that will fail Snowflake's semantic-view compiler regardless of

    how the expression is translated."""



    filtered_table: str

    referenced_measure: Optional[str]

    referenced_measure_dataset: Optional[str]

    # None when referenced_measure is unset/unresolvable; otherwise True if

    # THAT measure's own base table DOES have a path to filtered_table

    # (the common, fixable shape: the metric is just declared under the

    # wrong table), False if nothing in the model reaches filtered_table

    # from either table (the KPI01/KPI02 shape: no fix short of adding a

    # relationship that may not legitimately exist in the source data).

    referenced_measure_has_path: Optional[bool]

    reason: str

    category: str = ADVISORY_CATEGORY_UNREACHABLE_DIMENSION





def _find_all_calculate_nodes(node: DaxNode) -> List[FunctionCallNode]:

    """Every CALCULATE(...) FunctionCallNode anywhere in the tree, however

    deeply nested — a metric can contain more than one (e.g. inside

    separate branches of an IF/SWITCH selector, exactly the KPI01/KPI02

    shape: IF(SUM('KPI'[KPI])=N, ..., CALCULATE(...), ...))."""

    found: List[FunctionCallNode] = []



    def _walk(n: DaxNode) -> None:

        if isinstance(n, FunctionCallNode):

            if n.func == "CALCULATE":

                found.append(n)

            for a in n.args:

                _walk(a)

        elif isinstance(n, BinaryOpNode):

            _walk(n.left)

            _walk(n.right)

        elif isinstance(n, UnaryOpNode):

            _walk(n.operand)

        elif isinstance(n, VarNode):

            for _, value_expr in n.variables:

                _walk(value_expr)

            _walk(n.body)



    _walk(node)

    return found





def _collect_column_ref_tables(node: DaxNode) -> Set[str]:

    """Every distinct table name referenced by a ColumnRefNode anywhere in

    this subtree — deliberately walks the WHOLE filter-argument subtree

    (not just a single top-level comparison), so compound filters

    (FILTER(table, predicate), AND/OR-composed comparisons) are covered,

    not just the single direct-comparison shape KPI01/KPI02 happens to

    use."""

    tables: Set[str] = set()



    def _walk(n: DaxNode) -> None:

        if isinstance(n, ColumnRefNode):

            if n.table:

                tables.add(n.table)

        elif isinstance(n, BinaryOpNode):

            _walk(n.left)

            _walk(n.right)

        elif isinstance(n, UnaryOpNode):

            _walk(n.operand)

        elif isinstance(n, FunctionCallNode):

            for a in n.args:

                _walk(a)

        elif isinstance(n, VarNode):

            for _, value_expr in n.variables:

                _walk(value_expr)

            _walk(n.body)



    _walk(node)

    return tables





def dax_calculate_filters_unreachable_dimension(

    dax: str,

    calling_dataset: str,

    relationships: Iterable[Any],

    metric_datasets: Optional[Dict[str, str]] = None,

) -> Optional["UnreachableDimensionFilter"]:

    """Detect a CALCULATE(...) filter that references a table unreachable

    from `calling_dataset` via `relationships` — the shape that will fail

    Snowflake's semantic-view compiler as "a metric cannot refer to

    another dimension from an unrelated entity," no matter how the

    expression itself is translated (see module-level comment above).



    Args:

        dax: the metric's raw DAX expression.

        calling_dataset: the dataset/table this metric is (or would be)

            declared under in the target semantic view.

        relationships: the model's relationship list (anything

            utils.relationship_graph.find_relationship_path accepts —

            e.g. SMLModel.relationships).

        metric_datasets: optional {metric_unique_name: dataset_name} map,

            used only to enrich the result with whether the measure

            CALCULATE's first argument references (when it's a bare

            measure reference) has its OWN path to the filtered table —

            distinguishes "this metric is just declared under the wrong

            table" (fixable by re-anchoring or relating those two tables)

            from "nothing in this model reaches that table at all" (not

            fixable without a relationship that may not legitimately

            exist in the source model). Omit if unavailable — detection

            of the core problem doesn't depend on it.



    Returns the FIRST such filter found (walking CALCULATE nodes in

    document order), or None if no CALCULATE in this expression filters by

    an unreachable table — including the ordinary case where every

    CALCULATE's filters stay within calling_dataset's own reachable graph.

    """

    if not dax or not calling_dataset:

        return None

    node = DaxAstParser().parse(dax)

    if node is None:

        return None



    metric_datasets_ci = {str(k).casefold(): v for k, v in (metric_datasets or {}).items()}



    for calc_node in _find_all_calculate_nodes(node):

        if not calc_node.args:

            continue

        filtered_tables: Set[str] = set()

        for filter_arg in calc_node.args[1:]:

            filtered_tables |= _collect_column_ref_tables(filter_arg)



        for filtered_table in sorted(filtered_tables):

            if has_relationship_path(relationships, calling_dataset, filtered_table):

                continue  # reachable -- not the failure shape this detects



            referenced_measure: Optional[str] = None

            referenced_measure_dataset: Optional[str] = None

            referenced_measure_has_path: Optional[bool] = None

            base = calc_node.args[0]

            if isinstance(base, MeasureRefNode):

                referenced_measure = base.name

                referenced_measure_dataset = metric_datasets_ci.get(str(base.name).casefold())

                if referenced_measure_dataset:

                    referenced_measure_has_path = has_relationship_path(

                        relationships, referenced_measure_dataset, filtered_table

                    )



            reason = (

                f"This metric's CALCULATE(...) filters by table '{filtered_table}', but "

                f"'{calling_dataset}' (this metric's own base table) has no relationship "

                f"path to '{filtered_table}'. Snowflake's semantic-view compiler will "

                f"reject this as \"a metric cannot refer to another dimension from an "

                f"unrelated entity\" (error 010211) regardless of how the DAX is "

                f"translated — the SQL expression's shape isn't the problem, the missing "

                f"relationship path is."

            )

            if referenced_measure:

                if referenced_measure_has_path:

                    reason += (

                        f" '{referenced_measure}' (the measure this CALCULATE wraps, "

                        f"declared on '{referenced_measure_dataset}') DOES have a path to "

                        f"'{filtered_table}' — this metric may be declared under the wrong "

                        f"table; re-anchoring it to '{referenced_measure_dataset}', or "

                        f"adding a relationship between '{calling_dataset}' and "

                        f"'{referenced_measure_dataset}', would resolve this."

                    )

                elif referenced_measure_dataset:

                    reason += (

                        f" '{referenced_measure}' (declared on "

                        f"'{referenced_measure_dataset}') doesn't reach '{filtered_table}' "

                        f"either — no relationship anywhere in this model connects any of "

                        f"these tables."

                    )



            return UnreachableDimensionFilter(

                filtered_table=filtered_table,

                referenced_measure=referenced_measure,

                referenced_measure_dataset=referenced_measure_dataset,

                referenced_measure_has_path=referenced_measure_has_path,

                reason=reason,

            )



    return None





# Distinct from ADVISORY_CATEGORY_UNREACHABLE_DIMENSION: that one fires when

# NO relationship path exists (a real, unfixable-without-a-new-relationship

# 010211 failure). This one fires for the opposite outcome -- a relationship

# path DOES exist -- for a metric whose translation nonetheless failed today,

# because dry-run's translators can't yet exploit that path (see

# dax_calculate_filters_reachable_dimension_pending_enrichment's docstring).

# Mirrored onto SMLMetric.advisory_categories by osi_to_sml.py and mapped to

# STATUS_NEEDS_REVIEW (not STATUS_PREDICTED_FAILURE) by

# project_mapping_engine.py, since a real deploy has a genuine chance of

# resolving this where dry-run could not.

ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT = "pending_live_schema_enrichment"





@dataclass

class ReachableDimensionFilterPendingEnrichment:

    """A CALCULATE(...) filter that references a dimension WITH a

    relationship path from the calling metric's own base table, whose

    translation still failed -- see module docstring above

    ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT."""



    filtered_table: str

    reason: str

    category: str = ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT





def dax_calculate_filters_reachable_dimension_pending_enrichment(

    dax: str,

    calling_dataset: str,

    relationships: Iterable[Any],

) -> Optional["ReachableDimensionFilterPendingEnrichment"]:

    """Detect a CALCULATE(...) filter that references a table OTHER than

    `calling_dataset`, but one THAT IS reachable from it via

    `relationships` -- the shape dry-run's deterministic AST renderer

    declines (DaxSqlRenderer._resolve_table_alias refuses to guess a

    cross-table SQL alias) and Tier-5's LLM fallback also declines (its

    prompt is never given relationship/alias information for any table

    but the metric's own, so it correctly treats reachability as

    "unclear" and emits a NULL-cast placeholder per its own verification

    checklist).



    Unlike that failure, this one has a real fix path at real-deploy time:

    connectors/metrics_clause_builder.py's precomputed-cross-dataset-

    column rewrite (_rewrite_cross_dataset_dax_refs_to_precomputed) runs

    against the live-schema-derived enriched view -- built only when a

    real Snowflake connection is available -- and can resolve exactly

    this reference by substituting a same-table precomputed column before

    handing the DAX back to the same deterministic renderer for a second,

    easier attempt. Dry-run has no live connection, so it can never

    exercise that rewrite; a metric hitting this shape may well succeed

    at real deploy even though dry-run could not translate it.



    Only called (see osi_to_sml.py's

    _flag_reachable_dimension_filters_pending_enrichment) for a metric

    whose translation already failed -- if some tier already produced

    valid SQL, this note would be misleading noise, not a signal.



    Returns the FIRST such filter found (walking CALCULATE nodes in

    document order), or None if no CALCULATE in this expression filters

    by a reachable-but-different table — including the ordinary

    same-table case and the unreachable case

    (dax_calculate_filters_unreachable_dimension's shape, not this one).

    """

    if not dax or not calling_dataset:

        return None

    node = DaxAstParser().parse(dax)

    if node is None:

        return None



    calling_dataset_cf = str(calling_dataset).strip().strip("'").casefold()



    for calc_node in _find_all_calculate_nodes(node):

        if not calc_node.args:

            continue

        filtered_tables: Set[str] = set()

        for filter_arg in calc_node.args[1:]:

            filtered_tables |= _collect_column_ref_tables(filter_arg)



        for filtered_table in sorted(filtered_tables):

            if str(filtered_table or "").strip().strip("'").casefold() == calling_dataset_cf:

                continue  # same table -- not a cross-table reference at all

            if not has_relationship_path(relationships, calling_dataset, filtered_table):

                continue  # unreachable -- dax_calculate_filters_unreachable_dimension's shape, not this one



            reason = (

                f"This metric's CALCULATE(...) filters by table '{filtered_table}', "

                f"which IS reachable from '{calling_dataset}' via a declared "

                f"relationship, but dry-run's translators cannot resolve a "

                f"cross-table reference without a live connection's schema "

                f"enrichment. This metric may succeed at real deploy time even "

                f"though it could not be translated here — review before "

                f"assuming it will fail."

            )

            return ReachableDimensionFilterPendingEnrichment(

                filtered_table=filtered_table,

                reason=reason,

            )



    return None





# ---------------------------------------------------------------------------

# Disconnected-selector-table decomposition

# ---------------------------------------------------------------------------

# A distinct, THIRD shape from the two detectors above: a metric declared on

# a small selector/parameter table (e.g. a Power BI "field parameter"/

# "what-if" table with no real business key) whose DAX is an IF-chain that

# switches between OTHER tables' real aggregates based on the selector's own

# value -- e.g. IF(SUM('KPI'[KPI])=1, [MeasureA], IF(SUM('KPI'[KPI])=2, " ",

# CALCULATE([MeasureB], 'Date'[Year]=1))). DAX can do this with no

# relationship at all, because each branch evaluates independently in its

# own filter context and the results are just combined afterward -- but a

# single Snowflake METRICS-clause expression cannot: it must be one SQL

# aggregate validated against ONE base table's relationship graph, and

# there is no relationship to declare here (the selector table is

# deliberately disconnected, not merely mis-anchored).

#

# Unlike ADVISORY_CATEGORY_UNREACHABLE_DIMENSION (a dead end -- no fix

# short of a relationship that may not legitimately exist) this shape is

# actually resolvable: every non-trivial branch, with its selector

# condition stripped away, is a self-contained expression that doesn't

# reference the selector table at all -- e.g. `[MeasureB]` filtered by

# 'Date'[Year]=1 only needs SalesFact/Date, never KPI. Decomposing each

# branch into its OWN standalone metric lets those real values deploy

# normally; the original metric still can't be one metric (recorded as an

# advisory, not silently dropped), and a human recreates the selector

# switch itself one layer up, in the reporting tool, using the new

# per-branch metrics.

ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED = "disconnected_selector_decomposed"





@dataclass

class DecomposedSelectorBranch:

    """One branch extracted from a disconnected-selector IF-chain, ready to

    become its own standalone metric."""



    condition_dax: str  # human-readable, e.g. "SUM('KPI'[KPI])=1" or "otherwise" for a terminal else

    branch_dax: str      # best-effort re-serialized DAX text for the branch's own value expression

    branch_dataset: str  # the single, unambiguous dataset this branch's expression is anchored on





@dataclass

class DisconnectedSelectorDecomposition:

    selector_table: str

    branches: List[DecomposedSelectorBranch]

    skipped_trivial_count: int  # e.g. a bare " " literal branch -- nothing to decompose there





def _walk_if_chain(node: DaxNode) -> Tuple[List[Tuple[DaxNode, DaxNode]], Optional[DaxNode]]:

    """Follow a right-linear chain of IF(cond, true_val, next_if_or_else)

    calls. Returns (list of (cond, true_val) pairs in order, terminal else

    node or None if the chain ends without one / stops at a non-IF node)."""

    pairs: List[Tuple[DaxNode, DaxNode]] = []

    current: Optional[DaxNode] = node

    while isinstance(current, FunctionCallNode) and current.func == "IF" and len(current.args) >= 2:

        pairs.append((current.args[0], current.args[1]))

        current = current.args[2] if len(current.args) >= 3 else None

    return pairs, current





def _node_contains_measure_ref(node: DaxNode) -> bool:

    if isinstance(node, MeasureRefNode):

        return True

    if isinstance(node, BinaryOpNode):

        return _node_contains_measure_ref(node.left) or _node_contains_measure_ref(node.right)

    if isinstance(node, UnaryOpNode):

        return _node_contains_measure_ref(node.operand)

    if isinstance(node, FunctionCallNode):

        return any(_node_contains_measure_ref(a) for a in node.args)

    if isinstance(node, VarNode):

        return any(_node_contains_measure_ref(v) for _, v in node.variables) or _node_contains_measure_ref(node.body)

    return False





def _node_is_pure_literal(node: DaxNode) -> bool:

    """True if `node` has no column/measure/function reference anywhere --

    a compile-time constant like " " or 42, nothing worth its own metric."""

    if isinstance(node, LiteralNode):

        return True

    if isinstance(node, BinaryOpNode):

        return _node_is_pure_literal(node.left) and _node_is_pure_literal(node.right)

    if isinstance(node, UnaryOpNode):

        return _node_is_pure_literal(node.operand)

    if isinstance(node, VarNode):

        return all(_node_is_pure_literal(v) for _, v in node.variables) and _node_is_pure_literal(node.body)

    # BLANK() / TRUE() / FALSE() take no arguments and reference nothing:
    # the usual "else BLANK()" tail of a selector IF-chain is a constant,
    # not a branch that needs (or can get) its own metric.
    if isinstance(node, FunctionCallNode) and not node.args and str(node.func).upper() in {"BLANK", "TRUE", "FALSE"}:

        return True

    return False





def _collect_measure_ref_names(node: DaxNode) -> Set[str]:

    """Every distinct measure name referenced by a MeasureRefNode anywhere

    in this subtree -- same walk shape as _collect_column_ref_tables."""

    names: Set[str] = set()



    def _walk(n: DaxNode) -> None:

        if isinstance(n, MeasureRefNode):

            names.add(n.name)

        elif isinstance(n, BinaryOpNode):

            _walk(n.left)

            _walk(n.right)

        elif isinstance(n, UnaryOpNode):

            _walk(n.operand)

        elif isinstance(n, FunctionCallNode):

            for a in n.args:

                _walk(a)

        elif isinstance(n, VarNode):

            for _, value_expr in n.variables:

                _walk(value_expr)

            _walk(n.body)



    _walk(node)

    return names





def _resolve_single_branch_dataset(

    node: DaxNode, calling_dataset: str, metric_datasets_ci: Dict[str, str]

) -> Optional[str]:

    """The one, unambiguous dataset the new standalone metric should be

    anchored on. Prefers the dataset of a referenced MEASURE (e.g.

    CALCULATE([Total Category Volume], 'Date'[Running Year]=1) anchors on

    'Total Category Volume''s own dataset, SalesFact) over any bare column

    reference in the same branch (here, 'Date') -- a branch commonly

    references its own anchor's related tables too (a filter on the date

    dimension, say), and that's fine: whether THOSE are actually reachable

    from the chosen anchor is verified naturally when the new metric goes

    through the normal translation pipeline afterward, the same as any

    other metric -- this function only decides which table OWNS the new

    metric, not whether every reference in it is valid.



    None if measure references resolve to more than one distinct dataset

    (ambiguous), or if there's no measure reference at all and bare column

    references don't collapse to exactly one table either -- the caller

    must decline decomposing that branch rather than guess.

    """

    measure_datasets = set()

    for measure_name in _collect_measure_ref_names(node):

        resolved = metric_datasets_ci.get(str(measure_name).casefold())

        if resolved:

            measure_datasets.add(resolved)

    measure_datasets.discard(calling_dataset)

    if len(measure_datasets) == 1:

        return next(iter(measure_datasets))

    if measure_datasets:

        return None



    tables = set(_collect_column_ref_tables(node))

    tables.discard(calling_dataset)

    if len(tables) != 1:

        return None

    return next(iter(tables))





def _dax_text(node: DaxNode) -> str:

    """Best-effort DAX pretty-printer -- the inverse of DaxAstParser, used

    only to give an auto-decomposed branch a readable, round-trippable DAX

    expression (fed back through the normal translation pipeline exactly

    like any hand-authored metric). Not a byte-exact reproduction of the

    original source text (whitespace/quote-style may differ), only

    semantically equivalent DAX."""

    if isinstance(node, LiteralNode):

        if isinstance(node.value, bool):

            return "TRUE" if node.value else "FALSE"

        if isinstance(node.value, str):

            escaped = node.value.replace('"', '""')

            return f'"{escaped}"'

        return str(node.value)

    if isinstance(node, ColumnRefNode):

        return f"'{node.table}'[{node.column}]"

    if isinstance(node, MeasureRefNode):

        return f"[{node.name}]"

    if isinstance(node, IdentifierNode):

        return node.name

    if isinstance(node, FunctionCallNode):

        return f"{node.func}({', '.join(_dax_text(a) for a in node.args)})"

    if isinstance(node, BinaryOpNode):

        return f"{_dax_text(node.left)}{node.op}{_dax_text(node.right)}"

    if isinstance(node, UnaryOpNode):

        return f"{node.op}{_dax_text(node.operand)}"

    return "BLANK()"





def try_decompose_disconnected_selector_metric(

    dax: str,

    calling_dataset: str,

    relationships: Iterable[Any],

    metric_datasets: Optional[Dict[str, str]] = None,

) -> Optional[DisconnectedSelectorDecomposition]:

    """Detect the disconnected-selector-table IF-chain shape (see module

    comment above ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED) and,

    if it matches, extract each non-trivial branch as a standalone,

    self-contained DAX expression ready to become its own metric.



    IMPORTANT: this codebase's own AST renderer does NOT validate

    cross-table reachability for an INLINED MEASURE reference (only for a

    bare ColumnRefNode, via _resolve_table_alias) -- it just splices in

    the referenced measure's already-resolved SQL verbatim. So a metric

    matching this exact shape typically translates "successfully"

    (sql_expression populated, sync_enabled=True) by this codebase's own

    check, even though the emitted SQL mixes columns from a table

    unrelated to the metric's own base table -- something Snowflake's

    real semantic-view compiler WILL reject at actual deploy time (error

    010211). That's why this function takes `relationships` and requires

    calling_dataset to have NO path to each non-trivial branch's own

    dataset: without that check, this would also "decompose" an ordinary,

    already-valid multi-table metric (one Snowflake would happily accept

    because a real relationship exists) that merely happens to match the

    IF-chain shape syntactically -- unnecessary and wasteful.



    Deliberately conservative -- bails (returns None) entirely rather than

    partially decomposing, on ANY sign this isn't cleanly the shape

    understood here:

      - the metric's WHOLE expression must be a top-level IF(...) call

        (not IF nested inside some other operation);

      - every condition in the chain must reference ONLY calling_dataset

        (and never a measure reference, which could hide another table)

        -- and at least one condition must actually reference it, or this

        isn't a "selector on this table" shape at all;

      - every non-trivial branch value must NOT reference calling_dataset

        anywhere, must resolve to exactly one other, unambiguous dataset

        (via _resolve_single_branch_dataset), AND that dataset must be

        genuinely UNREACHABLE from calling_dataset (has_relationship_path

        returns False) -- any branch that's ambiguous, still depends on

        calling_dataset, or IS actually reachable means the whole metric

        declines, not just that branch, since guessing here risks a

        silently wrong (or unnecessary) decomposition.



    A trivial branch (a bare literal like " ", per _node_is_pure_literal)

    is simply skipped (counted in skipped_trivial_count), never given its

    own metric -- there's nothing to translate.



    Returns None if the chain has fewer than 2 conditioned branches, or if

    every branch turned out trivial/skippable (nothing usable to

    decompose) -- the caller's existing advisory-only handling applies

    unchanged in either case.

    """

    if not dax or not calling_dataset:

        return None

    node = DaxAstParser().parse(dax)

    if node is None:

        return None

    if not (isinstance(node, FunctionCallNode) and node.func == "IF"):

        return None



    pairs, terminal = _walk_if_chain(node)

    if len(pairs) < 2:

        return None



    metric_datasets_ci = {str(k).casefold(): v for k, v in (metric_datasets or {}).items()}



    saw_calling_dataset_condition = False

    for cond, _true_val in pairs:

        if _node_contains_measure_ref(cond):

            return None

        cond_tables = _collect_column_ref_tables(cond)

        if cond_tables - {calling_dataset}:

            return None

        if calling_dataset in cond_tables:

            saw_calling_dataset_condition = True

    if not saw_calling_dataset_condition:

        return None



    values_to_check: List[Tuple[Optional[DaxNode], DaxNode]] = list(pairs)

    if terminal is not None:

        values_to_check.append((None, terminal))



    branches: List[DecomposedSelectorBranch] = []

    skipped_trivial = 0

    for cond, value_node in values_to_check:

        if _node_is_pure_literal(value_node):

            skipped_trivial += 1

            continue

        if calling_dataset in _collect_column_ref_tables(value_node):

            return None

        branch_dataset = _resolve_single_branch_dataset(value_node, calling_dataset, metric_datasets_ci)

        if not branch_dataset:

            return None

        if has_relationship_path(relationships, calling_dataset, branch_dataset):

            # Genuinely reachable -- this branch's table isn't actually

            # disconnected from calling_dataset, so the original metric

            # may well be valid Snowflake SQL as written. Decline the

            # whole decomposition rather than second-guess a metric that

            # might already deploy correctly.

            return None

        branches.append(DecomposedSelectorBranch(

            condition_dax=_dax_text(cond) if cond is not None else "otherwise",

            branch_dax=_dax_text(value_node),

            branch_dataset=branch_dataset,

        ))



    if not branches:

        return None



    return DisconnectedSelectorDecomposition(

        selector_table=calling_dataset,

        branches=branches,

        skipped_trivial_count=skipped_trivial,

    )



def dax_time_intelligence_failure_reason(dax: str) -> str:
    return 'time intelligence not supported yet'


# Expose through the dax_ast_parser facade (both import orders work).
_parser.ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED = ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED
_parser.ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT = ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT
_parser.ADVISORY_CATEGORY_UNREACHABLE_DIMENSION = ADVISORY_CATEGORY_UNREACHABLE_DIMENSION
_parser.BY_DESIGN_EXCLUDED_PREFIX = BY_DESIGN_EXCLUDED_PREFIX
_parser.DecomposedSelectorBranch = DecomposedSelectorBranch
_parser.DisconnectedSelectorDecomposition = DisconnectedSelectorDecomposition
_parser.ReachableDimensionFilterPendingEnrichment = ReachableDimensionFilterPendingEnrichment
_parser.UnreachableDimensionFilter = UnreachableDimensionFilter
_parser._CONTEXT_TRANSITION_REASON = _CONTEXT_TRANSITION_REASON
_parser._GROUP_GRAIN_TABLE_FUNCS = _GROUP_GRAIN_TABLE_FUNCS
_parser._ITERATOR_FUNCS = _ITERATOR_FUNCS
_parser._LAG_PERIOD_FUNCS = _LAG_PERIOD_FUNCS
_parser._SCOPE_GUARD_FUNCS = _SCOPE_GUARD_FUNCS
_parser._STRING_PRODUCING_FUNCS = _STRING_PRODUCING_FUNCS
_parser._ast_contains_func = _ast_contains_func
_parser._calculate_has_allexcept_modifier = _calculate_has_allexcept_modifier
_parser._calculate_has_direct_all_or_allexcept_modifier = _calculate_has_direct_all_or_allexcept_modifier
_parser._calculate_lag_period_of_measure_reference = _calculate_lag_period_of_measure_reference
_parser._collect_column_ref_tables = _collect_column_ref_tables
_parser._collect_measure_ref_names = _collect_measure_ref_names
_parser._dax_text = _dax_text
_parser._extract_closing_balance_dim = _extract_closing_balance_dim
_parser._find_all_calculate_nodes = _find_all_calculate_nodes
_parser._get_measure_refs = _get_measure_refs
_parser._get_tables_in_node = _get_tables_in_node
_parser._has_allselected = _has_allselected
_parser._has_case_when_or_if = _has_case_when_or_if
_parser._has_closing_balance = _has_closing_balance
_parser._has_non_decomposable_aggregate = _has_non_decomposable_aggregate
_parser._has_non_literal_dateadd = _has_non_literal_dateadd
_parser._has_ranking_function = _has_ranking_function
_parser._has_selectedvalue_in_filter_all = _has_selectedvalue_in_filter_all
_parser._has_time_intel_function = _has_time_intel_function
_parser._has_treatas = _has_treatas
_parser._has_window_construct = _has_window_construct
_parser._is_empty_branch = _is_empty_branch
_parser._is_enrichable_all_measure = _is_enrichable_all_measure
_parser._is_leave_one_out = _is_leave_one_out
_parser._is_pure_ratio_or_arithmetic_over_metrics = _is_pure_ratio_or_arithmetic_over_metrics
_parser._is_selector_over_disconnected_table = _is_selector_over_disconnected_table
_parser._is_text_producing_node = _is_text_producing_node
_parser._iter_children = _iter_children
_parser._node_contains_measure_ref = _node_contains_measure_ref
_parser._node_is_pure_literal = _node_is_pure_literal
_parser._refs_any_data = _refs_any_data
_parser._resolve_single_branch_dataset = _resolve_single_branch_dataset
_parser._walk_if_chain = _walk_if_chain
_parser.classify_dax_ast_not_applicable = classify_dax_ast_not_applicable
_parser.dax_calculate_filters_reachable_dimension_pending_enrichment = dax_calculate_filters_reachable_dimension_pending_enrichment
_parser.dax_calculate_filters_unreachable_dimension = dax_calculate_filters_unreachable_dimension
_parser.dax_context_transition_failure_reason = dax_context_transition_failure_reason
_parser.dax_has_overriding_calculate_filter = dax_has_overriding_calculate_filter
_parser.dax_has_zero_data_dependencies = dax_has_zero_data_dependencies
_parser.dax_lag_period_of_measure_reference_failure_reason = dax_lag_period_of_measure_reference_failure_reason
_parser.dax_numeric_literal_value = dax_numeric_literal_value
_parser.dax_root_is_string_producing = dax_root_is_string_producing
_parser.dax_semantic_fidelity_failure_reason = dax_semantic_fidelity_failure_reason
_parser.dax_time_intelligence_failure_reason = dax_time_intelligence_failure_reason
_parser.is_by_design_excluded = is_by_design_excluded
_parser.try_decompose_disconnected_selector_metric = try_decompose_disconnected_selector_metric
