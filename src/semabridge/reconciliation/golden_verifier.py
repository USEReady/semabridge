"""Automated Mathematical Data Reconciliation Engine ("Golden Query Verifier").

Verifies numerical parity between source BI queries (Power BI / Fabric DAX)
and target data-layer queries (Snowflake / Databricks / DuckDB SQL).
Asserts that translated measures match down to the cent across dimension slices.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("semabridge.reconciliation.golden_verifier")


class VerificationTolerance(BaseModel):
    """Tolerance thresholds for numerical reconciliation."""
    absolute_tolerance: float = 0.01      # Absolute delta threshold (e.g. $0.01)
    relative_tolerance: float = 0.0001    # Relative percentage delta (0.01%)
    max_mismatch_percentage: float = 0.0  # Max allowed row mismatch % before failure (0% for strict parity)


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
    status: str  # 'CERTIFIED', 'FAILED', 'WARNING'
    dax_query_executed: str
    sql_query_executed: str
    certification_badge: str
    sample_mismatches: List[QueryComparisonRow] = Field(default_factory=list)


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
        dims = dimensions or []
        dim_str = ", ".join(f"'{d.split('.')[0]}'[{(d.split('.')[1] if '.' in d else d)}]" for d in dims)
        filter_str = ""
        if filters:
            filter_clauses = ", ".join(filters)
            filter_str = f", {filter_clauses}"

        if dims:
            return f"""EVALUATE
SUMMARIZECOLUMNS(
    {dim_str}{filter_str},
    "{metric_name}", [{metric_name}]
)
ORDER BY {dim_str}"""
        else:
            return f"""EVALUATE
ROW(
    "{metric_name}", [{metric_name}]
)"""

    @staticmethod
    def generate_sql_query(
        metric_name: str,
        metric_sql_expression: str,
        target_table_or_view: str,
        dimensions: Optional[List[str]] = None,
        filters: Optional[List[str]] = None,
    ) -> str:
        """Construct corresponding SQL query against the target semantic view."""
        dims = dimensions or []
        dim_cols = [d.split(".")[-1] for d in dims]

        select_parts = []
        for d in dim_cols:
            select_parts.append(f"    {d}")
        select_parts.append(f"    {metric_sql_expression} AS {metric_name}")

        where_clause = f"\nWHERE {' AND '.join(filters)}" if filters else ""
        group_by = f"\nGROUP BY {', '.join(str(i) for i in range(1, len(dim_cols) + 1))}" if dim_cols else ""
        order_by = f"\nORDER BY {', '.join(str(i) for i in range(1, len(dim_cols) + 1))}" if dim_cols else ""

        select_clause = ",\n".join(select_parts)
        return f"""SELECT
{select_clause}
FROM {target_table_or_view}{where_clause}{group_by}{order_by};"""

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

        def make_key(row: Dict[str, Any]) -> str:
            if not dimension_keys:
                return "__GLOBAL__"
            parts = []
            for k in dimension_keys:
                clean_k = k.split(".")[-1]
                val = row.get(clean_k) if clean_k in row else row.get(k)
                parts.append(str(val) if val is not None else "NULL")
            return " | ".join(parts)

        def extract_num(row: Dict[str, Any], key: str) -> Optional[float]:
            val = row.get(key)
            if val is None:
                # try case-insensitive
                for k, v in row.items():
                    if k.casefold() == key.casefold():
                        val = v
                        break
            if val is None:
                return None
            try:
                f = float(val)
                return 0.0 if math.isnan(f) else f
            except (ValueError, TypeError):
                return None

        # Build lookup tables
        dax_map = {make_key(r): extract_num(r, metric_name) for r in dax_rows}
        sql_map = {make_key(r): extract_num(r, metric_name) for r in sql_rows}

        all_keys = sorted(list(set(dax_map.keys()) | set(sql_map.keys())))
        total_rows = len(all_keys)

        matched_count = 0
        mismatched_count = 0
        max_abs_delta = 0.0
        max_pct_delta = 0.0
        sample_mismatches: List[QueryComparisonRow] = []

        for k in all_keys:
            d_val = dax_map.get(k)
            s_val = sql_map.get(k)

            if d_val is None and s_val is None:
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

                is_match = (abs_delta <= tol.absolute_tolerance) or ((pct_delta / 100.0) <= tol.relative_tolerance)

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
            status = "CERTIFIED"
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
        )
