"""Pattern matcher for DAX whole-table ALL/REMOVEFILTERS aggregates.

Generic rule:
  CALCULATE(<agg>(T[C]), ALL(T)) or CALCULATE(<agg>(T[C]), REMOVEFILTERS(T))
where T is the measure's own table (or primary fact table), and ALL(T) is the entire table
(NOT a column like ALL(T[C]) and NOT a related dimension like ALL(Dim)).

Allowed aggregates:
  SUM, COUNT, COUNTA, COUNTROWS, AVG, AVERAGE, MIN, MAX, DISTINCTCOUNT.

Enriched view:
  CROSS JOIN (SELECT <agg>(C) AS _ALL_<AGG>_<C> FROM T) anchor_all_<agg>_<c>
Metric:
  MAX(T."_ALL_<AGG>_<C>")
Helper column in DIMENSIONS:
  PRIVATE T."_ALL_<AGG>_<C>" AS T."_ALL_<AGG>_<C>"
"""

import re
from dataclasses import dataclass
from typing import Optional, Dict
from semabridge.converter.dax_ast_parser import (
    DaxAstParser,
    DaxNode,
    FunctionCallNode,
    ColumnRefNode,
    MeasureRefNode,
    IdentifierNode,
    unwrap_var_body,
)
from semabridge.utils.naming import sanitize_column


_SUPPORTED_AGGS = {
    "SUM": "SUM",
    "AVG": "AVG",
    "AVERAGE": "AVG",
    "AVERAGEX": "AVG",
    "MIN": "MIN",
    "MAX": "MAX",
    "COUNT": "COUNT",
    "COUNTA": "COUNT",
    "COUNTROWS": "COUNT(*)",
    "DISTINCTCOUNT": "COUNT(DISTINCT",
}


@dataclass(frozen=True)
class WholeTableAllPattern:
    """Represents a whole-table ALL precomputation pattern."""
    agg: str           # e.g. "MAX", "SUM"
    table: str         # e.g. "COVID"
    col: Optional[str] # e.g. "Date" or None for COUNTROWS
    helper_name: str   # e.g. "_ALL_MAX_DATE"
    sql_agg: str       # e.g. "MAX", "COUNT(*)", "COUNT(DISTINCT"


def match_whole_table_all_pattern(
    dax: Optional[str],
    fact_table: Optional[str] = None,
    metrics_dict: Optional[Dict[str, str]] = None,
) -> Optional[WholeTableAllPattern]:
    """Parse DAX to detect CALCULATE(<agg>(T[C]), ALL(T)) whole-table constant pattern."""
    if not dax or not isinstance(dax, str):
        return None

    # Fast filter: must contain CALCULATE and ALL or REMOVEFILTERS
    dax_u = dax.upper()
    if "CALCULATE" not in dax_u:
        return None
    if "ALL(" not in dax_u and "REMOVEFILTERS" not in dax_u and "ALLNOBLANKROW" not in dax_u:
        return None

    try:
        ast = DaxAstParser().parse(dax)
    except Exception:
        return None

    return match_whole_table_all_ast(ast, fact_table=fact_table, metrics_dict=metrics_dict)


def match_whole_table_all_ast(
    node: Optional[DaxNode],
    fact_table: Optional[str] = None,
    metrics_dict: Optional[Dict[str, str]] = None,
) -> Optional[WholeTableAllPattern]:
    """Inspect AST node to detect whole-table ALL pattern."""
    if node is None:
        return None

    node = unwrap_var_body(node)
    if not isinstance(node, FunctionCallNode) or node.func.upper() != "CALCULATE":
        return None

    if len(node.args) < 2:
        return None

    base_expr = node.args[0]
    filter_args = node.args[1:]

    # Check filter args: must ONLY be whole-table ALL/REMOVEFILTERS
    # Do NOT match if any filter is a column reference ALL(T[C]) or dimension ALL(Dim)
    target_table = fact_table

    for farg in filter_args:
        if not isinstance(farg, FunctionCallNode):
            return None
        fname = farg.func.upper()
        if fname not in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):
            return None

        # If ALL() has no arguments, it removes all filters on all tables
        if not farg.args:
            continue

        # If ALL has arguments, verify it is a TABLE identifier, NOT a column
        for arg in farg.args:
            if isinstance(arg, ColumnRefNode):
                # Column-level ALL(T[C]) -> MUST NOT use whole-table constant!
                return None
            if isinstance(arg, IdentifierNode):
                tbl_name = arg.name.strip("'\"")
                if "[" in tbl_name or "." in tbl_name:
                    # Column reference in identifier form
                    return None
                if target_table is None:
                    target_table = tbl_name
                elif target_table.casefold() != tbl_name.casefold():
                    # Mismatched table
                    return None
            else:
                return None

    # Now inspect the base expression: <agg>(T[C]) or MeasureRef
    agg = None
    col = None
    expr_table = None

    if isinstance(base_expr, MeasureRefNode):
        if metrics_dict:
            ref_dax = metrics_dict.get(base_expr.name.casefold(), "")
            if ref_dax:
                try:
                    ref_ast = DaxAstParser().parse(ref_dax)
                    ref_ast = unwrap_var_body(ref_ast)
                    if isinstance(ref_ast, FunctionCallNode):
                        base_expr = ref_ast
                except Exception:
                    pass

    if isinstance(base_expr, FunctionCallNode):
        fn = base_expr.func.upper()
        if fn in _SUPPORTED_AGGS:
            agg = "AVG" if fn in ("AVERAGE", "AVERAGEX") else fn
            if fn == "COUNTROWS":
                col = None
                if base_expr.args and isinstance(base_expr.args[0], IdentifierNode):
                    expr_table = base_expr.args[0].name.strip("'\"")
            elif base_expr.args:
                carg = base_expr.args[0]
                if isinstance(carg, ColumnRefNode):
                    col = carg.column
                    expr_table = carg.table
                elif isinstance(carg, IdentifierNode):
                    raw = carg.name.strip("'\"")
                    if "[" in raw:
                        # Table[Col]
                        m = re.match(r"(?:'([^']+)'|([A-Za-z0-9_ ]+))\s*\[([^\]]+)\]", raw)
                        if m:
                            expr_table = m.group(1) or m.group(2)
                            col = m.group(3)
                        else:
                            col = raw.strip("[]")
                    else:
                        col = raw
                elif hasattr(carg, "column"):
                    col = carg.column
                    expr_table = getattr(carg, "table", None)
                elif hasattr(carg, "name"):
                    col = carg.name

    if not agg:
        return None

    resolved_table = expr_table or target_table or fact_table
    if not resolved_table:
        return None

    # If both expr_table and target_table are present, verify they match
    if expr_table and target_table and expr_table.casefold() != target_table.casefold():
        return None

    clean_agg = agg.upper()
    if col:
        clean_col = re.sub(r'[^A-Za-z0-9_]', '_', col).upper()
        helper_name = f"_ALL_{clean_agg}_{clean_col}"
    else:
        helper_name = f"_ALL_{clean_agg}"

    sql_agg = _SUPPORTED_AGGS.get(clean_agg, clean_agg)

    return WholeTableAllPattern(
        agg=clean_agg,
        table=resolved_table,
        col=col,
        helper_name=helper_name,
        sql_agg=sql_agg,
    )

