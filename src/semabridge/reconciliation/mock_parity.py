"""Mock parity & comparative analysis generator for environments without live Fabric / msmdsrv connections.

Server-side configuration only (controlled via SEMABRIDGE_MOCK_PARITY env var, never UI toggles).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


def is_mock_parity_enabled() -> bool:
    """Return True if mock parity / comparative analysis is enabled via server environment."""
    val = os.getenv("SEMABRIDGE_MOCK_PARITY", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def is_mock_drift_enabled() -> bool:
    """Return True if mock drift detection is explicitly enabled via SEMABRIDGE_MOCK_DRIFT."""
    val = os.getenv("SEMABRIDGE_MOCK_DRIFT", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def generate_mock_comparative_data(
    metric_name: str,
    dimensions: Optional[List[str]] = None,
    target_table_or_view: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Generate high-fidelity, matching SQL and DAX result sets showing good match values."""
    dims = [d.strip() for d in (dimensions or []) if d and d.strip()]
    dim_key = dims[0] if dims else None

    # Slice values tailored to common dimensions
    if dim_key:
        dim_upper = dim_key.upper()
        if any(k in dim_upper for k in ("MANUF", "PRODUCT", "VENDOR", "BRAND")):
            slice_vals = ["Abbas", "Aliqui", "Barba", "Curio", "Fama", "Leo", "Natura", "Pirum", "Select", "VanArsdel"]
            base_numbers = [342, 280, 515, 190, 430, 260, 610, 390, 185, 720]
        elif any(k in dim_upper for k in ("YEAR", "DATE", "TIME", "PERIOD")):
            slice_vals = ["2021", "2022", "2023", "2024"]
            base_numbers = [1250000.0, 1420000.0, 1680000.0, 1950000.0]
        elif any(k in dim_upper for k in ("REGION", "GEO", "TERRITORY", "COUNTRY")):
            slice_vals = ["Central", "East", "North", "South", "West"]
            base_numbers = [450000.0, 780000.0, 320000.0, 560000.0, 890000.0]
        elif any(k in dim_upper for k in ("SEGMENT", "CATEGORY", "TYPE")):
            slice_vals = ["Youth", "Modern", "Regular", "Urban", "Enterprise"]
            base_numbers = [210000.0, 430000.0, 650000.0, 380000.0, 820000.0]
        else:
            slice_vals = ["Segment Alpha", "Segment Beta", "Segment Gamma", "Segment Delta", "Segment Epsilon"]
            base_numbers = [15000.0, 28000.0, 34000.0, 49000.0, 62000.0]
    else:
        slice_vals = []
        base_numbers = [245000.0]

    # Adjust values for metric type (Count, Percentage, Amount)
    m_upper = (metric_name or "").upper()
    if any(k in m_upper for k in ("COUNT", "CNT", "NUM", "ITEMS", "QUANTITY")):
        adapted = [int(v % 1000) or 100 for v in base_numbers]
    elif any(k in m_upper for k in ("PCT", "PERCENT", "RATE", "SHARE", "RATIO", "MARGIN")):
        adapted = [round(30.0 + (float(v) % 40.0), 2) for v in base_numbers]
    else:
        adapted = [float(v) for v in base_numbers]

    sql_rows: List[Dict[str, Any]] = []
    dax_rows: List[Dict[str, Any]] = []

    if not slice_vals:
        val = adapted[0]
        sql_rows.append({metric_name: val})
        dax_rows.append({metric_name: val})
    else:
        for idx, s_name in enumerate(slice_vals):
            val = adapted[idx % len(adapted)]
            # Include both leaf name and full dimension name so mapper matches either
            row_sql: Dict[str, Any] = {metric_name: val, dim_key: s_name}
            row_dax: Dict[str, Any] = {metric_name: val, dim_key: s_name}
            if "." in dim_key:
                leaf = dim_key.split(".", 1)[1]
                row_sql[leaf] = s_name
                row_dax[leaf] = s_name
            sql_rows.append(row_sql)
            dax_rows.append(row_dax)

    return sql_rows, dax_rows

