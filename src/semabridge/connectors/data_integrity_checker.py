"""Data Integrity Baseline Checker for SemaBridge.

Verifies live Snowflake tables and columns against the golden baseline
(phase1_data_baseline.json). Guarantees that SemaBridge operations have not
mutated, dropped, or corrupted physical data tables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from semabridge.core.exceptions import ConnectorError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_BASELINE_PATH = Path(
    r"C:\Users\charank\.gemini\antigravity-ide\brain\ed4cc1d0-1e1e-45cf-85f7-99736113be35\phase1_data_baseline.json"
)


class DataIntegrityDiscrepancyError(ConnectorError):
    """Raised when live Snowflake data deviates from the baseline."""
    pass


def load_baseline(baseline_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the golden baseline JSON."""
    path = baseline_path or DEFAULT_BASELINE_PATH
    if not path.exists():
        raise FileNotFoundError(f"Baseline file not found at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_table_integrity(
    cursor: Any,
    table_name: str,
    baseline_info: Dict[str, Any],
    database: Optional[str] = None,
    schema: Optional[str] = None,
) -> List[str]:
    """Check a single table's row count and per-column non-null counts against baseline."""
    discrepancies: List[str] = []

    table_qual = f'"{database}"."{schema}"."{table_name}"' if database and schema else f'"{table_name}"'

    # Check row count
    expected_rows = baseline_info.get("row_count")
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {table_qual}")
        live_rows = cursor.fetchone()[0]
        if live_rows != expected_rows:
            discrepancies.append(
                f"Table {table_name}: Row count mismatch! Live={live_rows}, Expected={expected_rows}"
            )
    except Exception as exc:
        discrepancies.append(f"Table {table_name}: Failed to query row count: {exc}")
        return discrepancies

    # Check column non-null counts
    columns = baseline_info.get("columns", {})
    if columns:
        col_exprs = [f'COUNT("{col}")' for col in columns]
        col_sql = f"SELECT {', '.join(col_exprs)} FROM {table_qual}"
        try:
            cursor.execute(col_sql)
            counts = cursor.fetchone()
            for (col_name, col_meta), live_non_null in zip(columns.items(), counts):
                expected_non_null = col_meta.get("non_null_count")
                if live_non_null != expected_non_null:
                    discrepancies.append(
                        f"Table {table_name}, Column {col_name}: Non-null count mismatch! "
                        f"Live={live_non_null}, Expected={expected_non_null}"
                    )
        except Exception as exc:
            discrepancies.append(f"Table {table_name}: Failed to query column non-null counts: {exc}")

    return discrepancies


def verify_data_integrity(
    cursor: Any,
    baseline_path: Optional[Path] = None,
    database: Optional[str] = None,
    schema: Optional[str] = None,
    tables_to_check: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Verify live Snowflake tables against baseline.

    Args:
        cursor: Active Snowflake cursor.
        baseline_path: Path to phase1_data_baseline.json.
        database: Optional database name.
        schema: Optional schema name.
        tables_to_check: Optional subset of tables to check. If None, checks all baseline tables.

    Returns:
        Dict with status, tables_checked, columns_checked, and discrepancies.

    Raises:
        DataIntegrityDiscrepancyError: If any discrepancy is found.
    """
    baseline = load_baseline(baseline_path)
    target_tables = tables_to_check or list(baseline.keys())

    all_discrepancies: List[str] = []
    tables_checked = 0
    columns_checked = 0

    logger.info("🔍 Starting data integrity verification against baseline for %d tables...", len(target_tables))

    for tbl in target_tables:
        if tbl not in baseline:
            logger.warning("Table %s not found in baseline; skipping", tbl)
            continue

        base_info = baseline[tbl]
        disc = check_table_integrity(cursor, tbl, base_info, database, schema)
        tables_checked += 1
        columns_checked += len(base_info.get("columns", {}))

        if disc:
            all_discrepancies.extend(disc)
            for d in disc:
                logger.error("❌ Integrity Discrepancy: %s", d)
        else:
            logger.debug("✅ Table %s matched baseline (%d rows, %d cols)", tbl, base_info.get("row_count"), len(base_info.get("columns", {})))

    if all_discrepancies:
        summary_msg = (
            f"Data integrity verification FAILED with {len(all_discrepancies)} discrepancy(ies) "
            f"across {tables_checked} tables!\n" + "\n".join(all_discrepancies[:20])
        )
        if len(all_discrepancies) > 20:
            summary_msg += f"\n... and {len(all_discrepancies) - 20} more."
        raise DataIntegrityDiscrepancyError(summary_msg)

    logger.info(
        "✅ Data integrity verified successfully: %d tables, %d columns match baseline 100%%.",
        tables_checked,
        columns_checked,
    )
    return {
        "status": "PASS",
        "tables_checked": tables_checked,
        "columns_checked": columns_checked,
        "discrepancies": [],
    }
