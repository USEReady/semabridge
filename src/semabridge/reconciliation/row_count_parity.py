"""Post-deploy row-count parity verifier.

Compares source table row counts against live Snowflake `COUNT(*)` queries
to verify data fidelity and completeness after deployment.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semabridge.reconciliation.row_count_parity")


def verify_row_count_parity(
    source_counts: Dict[str, int],
    cursor: Any,
    database: str,
    schema: str,
    table_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Compare source table row counts against live Snowflake COUNT(*).

    A table passes only when source count equals Snowflake count.
    """
    mapping = table_mapping or {}
    results: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []

    for src_table, expected_count in source_counts.items():
        target_table = mapping.get(src_table, src_table)
        clean_target = str(target_table).strip('"')
        qual_name = f'"{database}"."{schema}"."{clean_target}"'
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {qual_name}")
            row = cursor.fetchone()
            actual_count = int(row[0]) if row and row[0] is not None else 0
            matched = actual_count == int(expected_count)
            entry = {
                "source_table": src_table,
                "target_table": target_table,
                "source_count": int(expected_count),
                "snowflake_count": actual_count,
                "delta": actual_count - int(expected_count),
                "matched": matched,
            }
            results.append(entry)
            if not matched:
                mismatches.append(entry)
        except Exception as exc:
            logger.warning("Error fetching COUNT(*) for %s: %s", qual_name, exc)
            entry = {
                "source_table": src_table,
                "target_table": target_table,
                "source_count": int(expected_count),
                "snowflake_count": None,
                "delta": None,
                "matched": False,
                "error": str(exc),
            }
            results.append(entry)
            mismatches.append(entry)

    all_matched = len(mismatches) == 0 and len(results) > 0
    return {
        "status": "PASSED" if all_matched else "FAILED",
        "total_tables": len(results),
        "matched_tables": len(results) - len(mismatches),
        "mismatched_tables": len(mismatches),
        "details": results,
        "mismatches": mismatches,
    }
