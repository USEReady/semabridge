"""Fabric table row counts via COUNTROWS (for row-count parity only).

Replaces nothing by default: used only when ``behavior.features.fabric_real_row_counts``
is on. The counts are kept for parity checks and are NEVER fed into fact /
dimension classification (facts come from relationships).

Why COUNTROWS: the legacy query reads ``[Cardinality]`` from ``INFO.TABLES()``,
a column that call does not return, and swallows the error, so it yields {}.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from semabridge.sources.base import SourceError

logger = logging.getLogger(__name__)

_AUTO_DATE_PREFIXES = ("LocalDateTable_", "DateTableTemplate_")


@dataclass
class RowCountResult:
    counts: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)  # one reason per failed batch


def countable_tables(tmsl: Dict[str, Any]) -> List[str]:
    """Model tables that hold rows (no calculation groups, no auto-date templates)."""
    out: List[str] = []
    for t in (tmsl.get("model") or {}).get("tables") or []:
        name = str((t or {}).get("name") or "").strip()
        if not name or "calculationGroup" in t or name.startswith(_AUTO_DATE_PREFIXES):
            continue
        out.append(name)
    return out


def _dax_string(value: str) -> str:
    return value.replace('"', '""')


def _dax_table(value: str) -> str:
    return value.replace("'", "''")


def build_countrows_query(tables: List[str]) -> str:
    rows = ",\n".join(
        f'    ROW("t", "{_dax_string(n)}", "n", COUNTROWS(\'{_dax_table(n)}\'))' for n in tables
    )
    return "EVALUATE\nUNION(\n" + rows + "\n)" if len(tables) > 1 else "EVALUATE\n" + rows.strip()


def fabric_row_counts(extractor: Any, dataset_id: str, tmsl: Dict[str, Any], *, batch_size: int = 40) -> RowCountResult:
    """COUNTROWS per table in batches through the strict DAX call.

    A failed batch is logged as a WARNING with its reason and recorded in
    ``errors``; its tables are left out of ``counts`` (never reported as 0).
    """
    result = RowCountResult()
    tables = countable_tables(tmsl)
    for i in range(0, len(tables), max(1, batch_size)):
        chunk = tables[i:i + batch_size]
        try:
            rows = extractor.execute_dax_query_strict(dataset_id, build_countrows_query(chunk))
        except SourceError as exc:
            reason = f"row counts for {len(chunk)} table(s) ({', '.join(chunk[:5])}{'…' if len(chunk) > 5 else ''}) failed: {exc}"
            logger.warning("Fabric %s", reason)
            result.errors.append(reason)
            continue
        for r in rows:
            name = r.get("[t]", r.get("t"))
            count = r.get("[n]", r.get("n"))
            if name is not None and count is not None:
                result.counts[str(name)] = int(count)
    return result
