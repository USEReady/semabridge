"""Automated Mathematical Golden Query Reconciliation & Parity Verifier.

Generates side-by-side DAX (for Power BI / SSAS) and SQL (for Snowflake /
Databricks / Semantic Views) queries for identical metrics and slices, and
mathematically compares returned datasets within deterministic tolerances.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field

from semabridge.utils.reserved_prefixes import normalize_separator_ident, strip_reserved_column_prefix

_PLAIN_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_SIMPLE_SQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQL_SIMPLE_PREDICATE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*"
    r"(=|!=|<>|>=|<=|>|<|\bIN\b)\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _quote_sql_alias(name: str) -> str:
    """Return a safe SQL alias, quoting whenever spaces or special chars exist."""
    if _SIMPLE_SQL_IDENT_RE.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def _format_sql_identifier(ident: str) -> str:
    """Preserve dotted Table.Column qualifiers and quote parts with special characters."""
    parts = [p for p in ident.strip().split(".") if p != ""]
    if not parts:
        return '""'
    formatted: List[str] = []
    for part in parts:
        if _SIMPLE_SQL_IDENT_RE.fullmatch(part):
            formatted.append(part)
        else:
            formatted.append('"' + part.replace('"', '""') + '"')
    return ".".join(formatted)


def _escape_dax_col(col: str) -> str:
    return col.replace("]", "]]")


def _escape_dax_table(tbl: str) -> str:
    return tbl.replace("'", "''")


def _escape_dax_string(val: str) -> str:
    return val.replace('"', '""')


def _format_dax_dim_ref(dim: str) -> str:
    """Format a dimension for DAX without fabricating table names for unqualified inputs."""
    raw = dim.strip()
    if "." in raw:
        tbl, col = raw.split(".", 1)
        return f"'{_escape_dax_table(tbl.strip())}'[{_escape_dax_col(col.strip())}]"
    return f"[{_escape_dax_col(raw)}]"


def _sql_literal_to_dax(raw_lit: str) -> Optional[str]:
    lit = raw_lit.strip()
    if len(lit) >= 2 and lit[0] == "'" and lit[-1] == "'":
        inner = lit[1:-1].replace("''", "'")
        return f'"{_escape_dax_string(inner)}"'
    if _PLAIN_NUMBER_RE.fullmatch(lit):
        return lit
    if lit.upper() in ("TRUE", "FALSE"):
        return f"{lit.upper()}()"
    return None


def _translate_sql_filter_to_dax(filter_expr: str) -> str:
    """Translate simple SQL WHERE predicates into valid DAX filter arguments."""
    text = filter_expr.strip()
    if not text:
        return ""
    m = _SQL_SIMPLE_PREDICATE_RE.match(text)
    if not m:
        # Preserve expression as a DAX comment note rather than emitting broken SQL syntax
        return f"FILTER(ALL(), /* {text.replace('*/', '* /')} */ TRUE())"

    col_raw, op_raw, rhs_raw = m.group(1), m.group(2).upper(), m.group(3).strip()
    dax_col = _format_dax_dim_ref(col_raw)

    if op_raw == "IN":
        inner = rhs_raw
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1]
        items: List[str] = []
        for token in inner.split(","):
            converted = _sql_literal_to_dax(token)
            if converted is None:
                return f"FILTER(ALL(), /* {text.replace('*/', '* /')} */ TRUE())"
            items.append(converted)
        return f"{dax_col} IN {{{', '.join(items)}}}"

    dax_op = "<>" if op_raw == "!=" else op_raw
    converted_rhs = _sql_literal_to_dax(rhs_raw)
    if converted_rhs is None:
        return f"FILTER(ALL(), /* {text.replace('*/', '* /')} */ TRUE())"
    return f"{dax_col} {dax_op} {converted_rhs}"


def _canonical_key_part(val: Any) -> str:
    """Normalize equivalent numeric forms (e.g. 2024 vs 2024.0) while preserving string case/whitespace."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if math.isfinite(val) and val == math.trunc(val):
            return str(int(val))
        return f"{val:g}"
    return str(val)


def _parse_numeric_value(val: Any) -> Tuple[bool, Optional[float], Optional[str]]:
    """Parse strict numeric or SQL NULL values without locale-dependent punctuation stripping.

    Returns (is_valid, parsed_float_or_none, error_detail).
    """
    if val is None:
        return True, None, None
    if isinstance(val, bool):
        return False, None, f"boolean value {val!r} is not numeric"
    if isinstance(val, (int, float)):
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return False, None, f"non-finite numeric value {val!r}"
        return True, f, None
    if isinstance(val, str):
        s = val.strip()
        if not _PLAIN_NUMBER_RE.fullmatch(s):
            return False, None, f"unparseable numeric string {val!r}"
        f = float(s)
        if math.isnan(f) or math.isinf(f):
            return False, None, f"non-finite numeric value {val!r}"
        return True, f, None
    return False, None, f"unsupported metric value type {type(val).__name__}"


class VerificationTolerance(BaseModel):
    """Acceptable mathematical drift bounds for certification."""
    absolute_tolerance: float = Field(default=0.01, ge=0.0, description="Max absolute numeric variance (e.g. 0.01)")
    relative_tolerance: float = Field(default=0.0001, ge=0.0, description="Max relative percentage drift (e.g. 0.01%)")
    max_mismatch_percentage: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Max allowed % of mismatched rows before failing"
    )


class QueryComparisonRow(BaseModel):
    """Comparison result for a single dimension slice."""
    dimension_key: str
    dax_value: Optional[float]
    sql_value: Optional[float]
    absolute_delta: float
    percentage_delta: float
    is_match: bool


class VerificationResult(BaseModel):
    """Overall mathematical reconciliation report."""
    metric_name: str
    dimension_keys: List[str]
    total_rows_compared: int
    matched_rows: int
    mismatched_rows: int
    max_absolute_delta: float
    max_percentage_delta: float
    status: Literal["CERTIFIED", "FAILED", "WARNING", "ERROR"]
    dax_query_executed: str
    sql_query_executed: str
    certification_badge: str
    sample_mismatches: List[QueryComparisonRow] = Field(default_factory=list)
    column_mapping: Dict[str, Any] = Field(default_factory=dict)


class GoldenQueryVerifier:
    """Reconciles DAX measures against Snowflake / Databricks SQL queries."""

    def __init__(self, default_tolerance: Optional[VerificationTolerance] = None):
        self.tolerance = default_tolerance or VerificationTolerance()

    @staticmethod
    def generate_dax_query(
        metric_name: str,
        dimensions: Optional[List[str]] = None,
        filters: Optional[List[str]] = None,
    ) -> str:
        """Construct canonical DAX query using SUMMARIZECOLUMNS for a metric."""
        dims = [d for d in (dimensions or []) if d and d.strip()]
        dim_refs = [_format_dax_dim_ref(d) for d in dims]
        dim_str = ", ".join(dim_refs)

        filter_str = ""
        if filters:
            dax_filters = [_translate_sql_filter_to_dax(f) for f in filters if f and f.strip()]
            if dax_filters:
                filter_str = f", {', '.join(dax_filters)}"

        escaped_label = _escape_dax_string(metric_name)
        escaped_measure = _escape_dax_col(metric_name)

        if dims:
            return f"""EVALUATE
SUMMARIZECOLUMNS(
    {dim_str}{filter_str},
    "{escaped_label}", [{escaped_measure}]
)
ORDER BY {dim_str}"""
        else:
            return f"""EVALUATE
ROW(
    "{escaped_label}", [{escaped_measure}]
)"""

    @staticmethod
    def _has_date_dimension(dimensions: Optional[List[str]]) -> bool:
        date_re = re.compile(r"(YEAR|MONTH|QUARTER|QTR|PERIOD|DATE|WEEK|DAY|FISCAL)", re.IGNORECASE)
        return any(date_re.search(str(d or "")) for d in (dimensions or []))

    @staticmethod
    def generate_sql_query(
        metric_name: str,
        metric_sql_expression: str,
        target_table_or_view: str,
        dimensions: Optional[List[str]] = None,
        filters: Optional[List[str]] = None,
        target_kind: Optional[str] = None,
        available_metrics: Optional[Set[str]] = None,
    ) -> str:
        """Construct corresponding SQL query against a physical table or deployed Semantic View."""
        dims = [d for d in (dimensions or []) if d and d.strip()]
        clean_target = _format_sql_identifier(target_table_or_view)

        if (target_kind or "").strip().lower() == "semantic_view":
            expr_clean = (metric_sql_expression or "").strip()
            use_expr_metric = bool(
                expr_clean
                and "." in expr_clean
                and "(" not in expr_clean
                and " " not in expr_clean.replace('"', "")
            )
            metric_token = expr_clean if use_expr_metric else metric_name
            if available_metrics and GoldenQueryVerifier._has_date_dimension(dims):
                avail_upper = {m.strip().strip('"').upper() for m in available_metrics}
                tok_clean = metric_token.strip()
                base_part = tok_clean.split(".")[-1].strip('"')
                lag_cand = f"{base_part.upper()}__BY_PERIOD"
                if lag_cand in avail_upper or any(m.endswith(f".{lag_cand}") for m in avail_upper):
                    if "." in tok_clean:
                        prefix_tbl = tok_clean.rsplit(".", 1)[0]
                        metric_token = f"{prefix_tbl}.{base_part}__BY_PERIOD"
                    else:
                        metric_token = f"{base_part}__BY_PERIOD"
            metric_ident = _format_sql_identifier(metric_token)
            sv_clauses = [clean_target, f"METRICS {metric_ident}"]
            if dims:
                formatted_dims = ", ".join(_format_sql_identifier(d) for d in dims)
                sv_clauses.append(f"DIMENSIONS {formatted_dims}")
            if filters:
                cleaned_filters = [f.strip() for f in filters if f and f.strip()]
                if cleaned_filters:
                    sv_clauses.append(f"WHERE {' AND '.join(cleaned_filters)}")
            body = "\n    ".join(sv_clauses)
            return f"SELECT *\nFROM SEMANTIC_VIEW(\n    {body}\n);"

        dim_cols = [_format_sql_identifier(d) for d in dims]
        select_parts = [f"    {d}" for d in dim_cols]
        alias = _quote_sql_alias(metric_name)
        select_parts.append(f"    {metric_sql_expression} AS {alias}")

        cleaned_filters = [f.strip() for f in (filters or []) if f and f.strip()]
        where_clause = f"\nWHERE {' AND '.join(cleaned_filters)}" if cleaned_filters else ""
        group_by = f"\nGROUP BY {', '.join(str(i) for i in range(1, len(dim_cols) + 1))}" if dim_cols else ""
        order_by = f"\nORDER BY {', '.join(str(i) for i in range(1, len(dim_cols) + 1))}" if dim_cols else ""

        select_clause = ",\n".join(select_parts)
        return f"""SELECT
{select_clause}
FROM {clean_target}{where_clause}{group_by}{order_by};"""

    def reconcile_data(
        self,
        metric_name: str,
        dax_rows: List[Dict[str, Any]],
        sql_rows: List[Dict[str, Any]],
        dimension_keys: List[str],
        dax_query: str = "",
        sql_query: str = "",
        custom_tolerance: Optional[VerificationTolerance] = None,
    ) -> VerificationResult:
        """Mathematically compare row-by-row results between DAX and SQL."""
        tol = custom_tolerance or self.tolerance

        def _error_result(reason: str) -> VerificationResult:
            return VerificationResult(
                metric_name=metric_name,
                dimension_keys=dimension_keys,
                total_rows_compared=0,
                matched_rows=0,
                mismatched_rows=0,
                max_absolute_delta=0.0,
                max_percentage_delta=0.0,
                status="ERROR",
                dax_query_executed=dax_query,
                sql_query_executed=sql_query,
                certification_badge=f"⚠️ Verification Error ({reason})",
                sample_mismatches=[],
            )

        if not metric_name or not metric_name.strip():
            return _error_result("metric_name is blank")

        if not dax_rows and not sql_rows:
            return VerificationResult(
                metric_name=metric_name,
                dimension_keys=dimension_keys,
                total_rows_compared=0,
                matched_rows=0,
                mismatched_rows=0,
                max_absolute_delta=0.0,
                max_percentage_delta=0.0,
                status="FAILED",
                dax_query_executed=dax_query,
                sql_query_executed=sql_query,
                certification_badge="❌ Reconciliation Failed (0 rows supplied; empty result sets cannot be certified)",
                sample_mismatches=[],
            )

        _bracket_leaf_re = re.compile(r"^(?:.*\[)([^\[\]]+)\]$")

        def _leaf_ident(col: str) -> str:
            s = str(col or "").strip()
            bm = _bracket_leaf_re.match(s)
            if bm:
                s = bm.group(1).strip()
            if "." in s:
                s = s.split(".")[-1].strip()
            return s.strip('"').strip("`")

        def _resolve_result_column(row_keys: List[str], target_spec: str) -> Optional[str]:
            """Resolve a result column generically across exact, leaf, normalized, and COL_ forms."""
            if target_spec in row_keys:
                return target_spec
            target_cf = target_spec.casefold()
            for rk in row_keys:
                if rk.casefold() == target_cf:
                    return rk
            target_leaf = _leaf_ident(target_spec)
            for rk in row_keys:
                if _leaf_ident(rk).casefold() == target_leaf.casefold():
                    return rk
            t_norm = normalize_separator_ident(target_leaf, pct_word=True)
            if t_norm:
                for rk in row_keys:
                    rk_leaf = _leaf_ident(rk)
                    if normalize_separator_ident(rk_leaf, pct_word=True) == t_norm:
                        return rk
                    if strip_reserved_column_prefix(rk_leaf, [target_leaf]) or strip_reserved_column_prefix(target_leaf, [rk_leaf]):
                        return rk
            return None

        # Validate that every non-empty dataset includes the target metric column
        if dax_rows and all(_resolve_result_column(list(r.keys()), metric_name) is None for r in dax_rows):
            return _error_result(f"metric column '{metric_name}' missing from dax_results")
        if sql_rows and all(_resolve_result_column(list(r.keys()), metric_name) is None for r in sql_rows):
            return _error_result(f"metric column '{metric_name}' missing from sql_results")

        dax_sample_keys = list(dax_rows[0].keys()) if dax_rows else []
        sql_sample_keys = list(sql_rows[0].keys()) if sql_rows else []
        resolved_column_mapping: Dict[str, Any] = {
            "metric": {
                "requested": metric_name,
                "dax_column": _resolve_result_column(dax_sample_keys, metric_name) if dax_sample_keys else None,
                "sql_column": _resolve_result_column(sql_sample_keys, metric_name) if sql_sample_keys else None,
            },
            "dimensions": [
                {
                    "requested": dk,
                    "dax_column": _resolve_result_column(dax_sample_keys, dk) if dax_sample_keys else None,
                    "sql_column": _resolve_result_column(sql_sample_keys, dk) if sql_sample_keys else None,
                }
                for dk in dimension_keys
            ],
        }

        def make_key(row: Dict[str, Any]) -> str:
            if not dimension_keys:
                return "__GLOBAL__"
            row_keys = list(row.keys())
            parts: List[str] = []
            for k in dimension_keys:
                matched_col = _resolve_result_column(row_keys, k)
                val = row.get(matched_col) if matched_col is not None else None
                parts.append(_canonical_key_part(val))
            return " | ".join(parts)

        def build_map(rows: List[Dict[str, Any]], source_label: str) -> Tuple[Optional[Dict[str, Optional[float]]], List[str], Optional[str]]:
            mapping: Dict[str, Optional[float]] = {}
            duplicates: List[str] = []
            for row in rows:
                mk = _resolve_result_column(list(row.keys()), metric_name)
                if mk is None:
                    return None, [], f"metric column '{metric_name}' missing in a {source_label} row"
                ok, num_val, err_detail = _parse_numeric_value(row.get(mk))
                if not ok:
                    return None, [], f"{err_detail} in {source_label} for '{metric_name}'"
                dim_key = make_key(row)
                if dim_key in mapping:
                    duplicates.append(dim_key)
                mapping[dim_key] = num_val
            return mapping, duplicates, None

        dax_map, dax_dups, dax_err = build_map(dax_rows, "dax_results")
        if dax_err or dax_map is None:
            return _error_result(dax_err or "invalid dax_results")

        sql_map, sql_dups, sql_err = build_map(sql_rows, "sql_results")
        if sql_err or sql_map is None:
            return _error_result(sql_err or "invalid sql_results")

        if dax_dups or sql_dups:
            dup_preview = sorted(set(dax_dups + sql_dups))[:5]
            dup_mismatches = max(len(dax_dups) + len(sql_dups), 1)
            return VerificationResult(
                metric_name=metric_name,
                dimension_keys=dimension_keys,
                total_rows_compared=dup_mismatches,
                matched_rows=0,
                mismatched_rows=dup_mismatches,
                max_absolute_delta=0.0,
                max_percentage_delta=100.0,
                status="FAILED",
                dax_query_executed=dax_query,
                sql_query_executed=sql_query,
                certification_badge=f"❌ Reconciliation Failed (duplicate dimension keys detected: {', '.join(dup_preview)})",
                sample_mismatches=[],
            )

        all_keys = sorted(set(dax_map.keys()) | set(sql_map.keys()))
        total_rows = len(all_keys)

        matched_count = 0
        mismatched_count = 0
        max_abs_delta = 0.0
        max_pct_delta = 0.0
        sample_mismatches: List[QueryComparisonRow] = []

        for k in all_keys:
            in_dax = k in dax_map
            in_sql = k in sql_map
            d_val = dax_map.get(k)
            s_val = sql_map.get(k)

            if not in_dax or not in_sql:
                # Row present on only one side is a mismatch even if its metric value is NULL
                is_match = False
                abs_delta = abs((d_val or 0.0) - (s_val or 0.0))
                pct_delta = 100.0
            elif d_val is None and s_val is None:
                # Genuine blank/NULL on both sides for the same present dimension key
                is_match = True
                abs_delta = 0.0
                pct_delta = 0.0
            elif d_val is None or s_val is None:
                is_match = False
                abs_delta = abs((d_val or 0.0) - (s_val or 0.0))
                pct_delta = 100.0
            else:
                abs_delta = abs(d_val - s_val)
                denom = max(abs(d_val), abs(s_val), 1e-9)
                pct_delta = (abs_delta / denom) * 100.0
                is_match = (abs_delta <= tol.absolute_tolerance) or (
                    (pct_delta / 100.0) <= tol.relative_tolerance
                )

            max_abs_delta = max(max_abs_delta, abs_delta)
            max_pct_delta = max(max_pct_delta, pct_delta)

            comp_row = QueryComparisonRow(
                dimension_key=k,
                dax_value=d_val,
                sql_value=s_val,
                absolute_delta=round(abs_delta, 6),
                percentage_delta=round(pct_delta, 4),
                is_match=is_match,
            )

            if is_match:
                matched_count += 1
            else:
                mismatched_count += 1
                if len(sample_mismatches) < 10:
                    sample_mismatches.append(comp_row)

        mismatch_pct = (mismatched_count / total_rows * 100.0) if total_rows > 0 else 0.0

        if mismatched_count == 0:
            status: Literal["CERTIFIED", "FAILED", "WARNING", "ERROR"] = "CERTIFIED"
            badge = f"🛡️ Certified 100% Match ({matched_count}/{total_rows} rows)"
        elif mismatch_pct <= tol.max_mismatch_percentage:
            status = "WARNING"
            badge = f"⚠️ Warning ({mismatch_pct:.2f}% mismatch within threshold)"
        else:
            status = "FAILED"
            badge = f"❌ Reconciliation Failed ({mismatched_count}/{total_rows} mismatches, max delta: {max_abs_delta:.4f})"

        return VerificationResult(
            metric_name=metric_name,
            dimension_keys=dimension_keys,
            total_rows_compared=total_rows,
            matched_rows=matched_count,
            mismatched_rows=mismatched_count,
            max_absolute_delta=round(max_abs_delta, 6),
            max_percentage_delta=round(max_pct_delta, 4),
            status=status,
            dax_query_executed=dax_query,
            sql_query_executed=sql_query,
            certification_badge=badge,
            sample_mismatches=sample_mismatches,
            column_mapping=resolved_column_mapping,
        )
