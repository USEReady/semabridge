"""Classification of zero-table models and corresponding exceptions.

A model with 0 physical tables cannot deploy a semantic view. It must be
classified into one of the 5 reason codes:
  1. LIVE_CONNECTION: Thin report connecting live to a published dataset on Power BI Service
  2. ONLY_AUTO_DATE_TABLES: Model has tables, but all are auto date/template tables
  3. ALL_TABLES_FAILED: User tables existed, but all failed during extraction/binding
  4. MEASURES_ONLY: Model has 0 physical tables, but has one or more measures defined
  5. EMPTY_MODEL: Model has 0 tables and 0 measures
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from semabridge.core.exceptions import SemaBridgeError


def is_auto_date_table(name: str) -> bool:
    """Return True if the table name matches Power BI auto-generated date tables."""
    return str(name or "").startswith(("LocalDateTable_", "DateTableTemplate_"))


class ZeroTablesModelError(SemaBridgeError):
    """Raised when a model has 0 physical tables and cannot generate a semantic view."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        pbix_path: Optional[str] = None,
        model_reference: Optional[str] = None,
        measures: Optional[List[Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        full_details = dict(details or {})
        full_details["reason_code"] = reason_code
        if model_reference:
            full_details["model_reference"] = model_reference
        if pbix_path:
            full_details["pbix_path"] = pbix_path
        super().__init__(message, full_details)
        self.reason_code = reason_code
        self.pbix_path = pbix_path
        self.model_reference = model_reference
        self.measures = list(measures or [])


def classify_tmsl_model(
    tmsl: Optional[Dict[str, Any]],
    pbix_path: Optional[str] = None,
    live_reference: Optional[str] = None,
) -> Optional[Tuple[str, Optional[str], List[Any]]]:
    """Inspect TMSL payload and return (reason_code, model_reference, measures)
    if the model has 0 physical tables, or None if it contains physical user tables.
    """
    if live_reference is not None:
        return ("LIVE_CONNECTION", live_reference or None, [])

    if not isinstance(tmsl, dict):
        return ("EMPTY_MODEL", None, [])

    model_obj = tmsl.get("model")
    if not isinstance(model_obj, dict):
        return ("EMPTY_MODEL", None, [])

    raw_tables = model_obj.get("tables", []) or []
    user_tables = [
        t for t in raw_tables
        if isinstance(t, dict) and not is_auto_date_table(str(t.get("name", "")))
    ]

    measures: List[Any] = []
    for t in raw_tables:
        if isinstance(t, dict):
            measures.extend(t.get("measures", []) or [])
    if isinstance(model_obj.get("measures"), list):
        measures.extend(model_obj.get("measures") or [])

    if not user_tables:
        if measures:
            return ("MEASURES_ONLY", None, measures)
        if raw_tables:
            return ("ONLY_AUTO_DATE_TABLES", None, measures)
        return ("EMPTY_MODEL", None, measures)

    return None
