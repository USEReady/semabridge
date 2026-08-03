"""Shared naming-convention type hints for common CRM-style audit/metric
columns (e.g. a Salesforce-sourced "SystemModstamp" column, which is a
recognized platform convention, not any one customer's fixture).

Used by converter/tmsl_to_osi.py, connectors/schema_manager.py, and
connectors/snowflake_emitter_parts/schema_evolution.py so a fix to one
copy can't silently diverge from the others. Whole-word matching only —
a raw substring check on "AMOUNT"/"DATE" false-positives on names like
"Paramount"/"Validated".

Only a refinement layer for ambiguous/string-typed columns — never
overrides an already-known/declared type.
"""
from __future__ import annotations

from typing import Optional

from semabridge.connectors.fact_table_naming import tokenize_dataset_name

_FIELD_HISTORY_SUFFIX = "FIELDHISTORY"


def infer_business_field_type(table_name: str, col_name: str) -> Optional[str]:
    """Return one of "BOOLEAN"/"TIMESTAMP"/"FLOAT"/"DATE", or None."""
    table_upper = (table_name or "").upper()
    col_upper = (col_name or "").strip().upper()
    tokens = set(tokenize_dataset_name(col_name))

    if col_upper == "DELETED":
        return "BOOLEAN"
    if "MODSTAMP" in tokens:
        return "TIMESTAMP"
    if tokens & {"VOLUME", "AMOUNT"}:
        return "FLOAT"
    if "DATE" in tokens and not table_upper.endswith(_FIELD_HISTORY_SUFFIX):
        return "DATE"

    return None
