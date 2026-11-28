"""Shared DAX building, column normalization, and result extraction."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_BRACKETED_COL_RE = re.compile(r"^(?:.*\[)([^\[\]]+)\]$")


def normalize_measure_name(measure_name: str) -> str:
    """Ensure measure is cleanly bracketed for DAX, e.g. 'Total Sales' -> '[Total Sales]'."""
    s = str(measure_name or "").strip()
    if not s:
        return ""
    if s.startswith("[") and s.endswith("]"):
        return s
    # If formatted as 'Table'[Measure], return as is
    if re.match(r"^'?[^'\[\]]+'?\[[^\[\]]+\]$", s):
        return s
    return f"[{s}]"


def clean_measure_name(measure_name: str) -> str:
    """Extract clean inner measure identifier, e.g. '[Total Sales]' -> 'Total Sales'."""
    s = str(measure_name or "").strip()
    m = _BRACKETED_COL_RE.match(s)
    if m:
        return m.group(1).strip()
    return s.strip("[]")


def build_dax_query(
    measure_name: str,
    slice_filters: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a DAX query to evaluate a single metric or sliced metric.
    
    Generates:
        EVALUATE ROW("Value", [MeasureName])
    or with filter slices:
        EVALUATE CALCULATETABLE(
            ROW("Value", [MeasureName]),
            'Table'[Column] = "Value"
        )
    """
    clean_name = normalize_measure_name(measure_name)
    if not clean_name:
        raise ValueError("measure_name cannot be empty")

    if not slice_filters:
        return f'EVALUATE ROW("Value", {clean_name})'

    filter_conditions: List[str] = []
    for col, val in slice_filters.items():
        col_expr = col.strip()
        if not (col_expr.startswith("[") or "'" in col_expr):
            col_expr = f"[{col_expr}]"
        
        if val is None:
            filter_conditions.append(f"ISBLANK({col_expr})")
        elif isinstance(val, (int, float)):
            filter_conditions.append(f"{col_expr} = {val}")
        elif isinstance(val, bool):
            filter_conditions.append(f"{col_expr} = {str(val).upper()}")
        else:
            escaped_val = str(val).replace('"', '""')
            filter_conditions.append(f'{col_expr} = "{escaped_val}"')

    filters_clause = ", ".join(filter_conditions)
    return (
        f'EVALUATE CALCULATETABLE(\n'
        f'    ROW("Value", {clean_name}),\n'
        f'    {filters_clause}\n'
        f')'
    )


def normalize_dax_columns(raw_columns: List[str]) -> List[str]:
    """Normalize Analysis Services / Power BI column headers such as '[Value]' or 'Table[Col]'."""
    stripped: List[str] = []
    for col in raw_columns:
        s = str(col or "").strip()
        m = _BRACKETED_COL_RE.match(s)
        stripped.append(m.group(1).strip() if m else s)

    counts: Dict[str, int] = {}
    for name in stripped:
        key = name.casefold()
        counts[key] = counts.get(key, 0) + 1

    final_cols: List[str] = []
    for raw, norm in zip(raw_columns, stripped):
        if counts.get(norm.casefold(), 0) > 1:
            final_cols.append(str(raw).strip())
        else:
            final_cols.append(norm)
    return final_cols


def extract_single_value(rows: List[Dict[str, Any]], expected_metric: Optional[str] = None) -> Any:
    """Extract single scalar metric value from DAX result rows."""
    if not rows:
        return None
    first_row = rows[0]
    if not first_row:
        return None

    # Preferred keys in order: "Value", clean metric name, case-insensitive match, or first value
    candidates = ["Value", "value", "[Value]"]
    if expected_metric:
        clean = clean_measure_name(expected_metric)
        candidates.extend([clean, f"[{clean}]", expected_metric])

    for k in candidates:
        if k in first_row:
            return first_row[k]

    # Case-insensitive search
    first_row_lower = {str(k).lower(): v for k, v in first_row.items()}
    for k in candidates:
        if k.lower() in first_row_lower:
            return first_row_lower[k.lower()]

    # Return first available value in row
    return next(iter(first_row.values()))
