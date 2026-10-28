"""Shared whole-word keyword detection for dataset classification —
calendar-dimension existence checks (converter/osi_to_sml.py,
converter/tmsl_to_sml.py) and the dimension-classification override
(converter/tmsl_to_sml.py). Reuses the same tokenizer as
connectors/fact_table_naming.py (not duplicated) so every name-based
classification heuristic in this codebase splits on the same word
boundaries — e.g. a dataset named "Validated_Orders" or
"Consolidated_Fact" must not match "DATE"/"FACT" merely because those
letters appear mid-word.
"""
from __future__ import annotations

import re

from semabridge.connectors.fact_table_naming import tokenize_dataset_name

CALENDAR_KEYWORDS = frozenset({"DATE", "DATES", "CALENDAR"})


def is_calendar_like_name(name: str) -> bool:
    """True if `name` contains one of CALENDAR_KEYWORDS as a whole word."""
    return bool(set(tokenize_dataset_name(name)) & CALENDAR_KEYWORDS)


def is_date_dataset(dataset: Any, model: Optional[Any] = None) -> bool:
    """True if dataset is a date table using PBIX metadata first, with logged name fallback.

    Checks:
    1. Dataset metadata: marked as date table (is_date_table, is_calendar,
       dataCategory == "Time" or "Date", annotations).
    2. Relationships: target (to_dataset) of date-typed relationships in model.
    3. Logged fallback to keyword matching only when no metadata exists.
    """
    if not dataset:
        return False

    # 1. Direct metadata on dataset
    if getattr(dataset, "is_date_table", False) or getattr(dataset, "is_calendar", False):
        return True
    dc = str(getattr(dataset, "data_category", "") or getattr(dataset, "dataCategory", "") or "").lower()
    if dc in ("time", "date"):
        return True
    annotations = getattr(dataset, "annotations", {}) or {}
    if isinstance(annotations, dict) and annotations.get("__PBI_LocalDateTable"):
        return True

    name = getattr(dataset, "unique_name", None) or getattr(dataset, "name", "")
    for col in getattr(dataset, "columns", []) or []:
        col_dc = str(getattr(col, "data_category", "") or getattr(col, "dataCategory", "") or "").lower()
        if col_dc in ("paddeddatetabledates", "date", "time"):
            return True

    # 2. Target of date-typed relationships
    if model:
        relationships = getattr(model, "relationships", []) or []
        for rel in relationships:
            to_ds = getattr(rel, "to_dataset", None)
            if to_ds and str(to_ds).upper() == str(name).upper():
                to_cols = [str(c).upper() for c in (getattr(rel, "to_columns", []) or [])]
                for col in getattr(dataset, "columns", []) or []:
                    c_name = getattr(col, "unique_name", "") or getattr(col, "name", "")
                    if c_name.upper() in to_cols:
                        dt = getattr(col, "data_type", None)
                        dt_str = getattr(dt, "value", str(dt)).lower()
                        if dt_str in ("date", "datetime"):
                            return True
                if any("DATE" in c or "TIME" in c or "DAY" in c for c in to_cols):
                    return True

    # 3. Logged fallback only when no metadata matches
    if is_calendar_like_name(name):
        import logging
        logging.getLogger(__name__).info(
            "No date table metadata found for '%s'; falling back to name-based detection", name
        )
        return True

    return False


def find_model_date_dataset(model: Any) -> Optional[Any]:
    """Find the date dataset in a model using metadata first, then logged name fallback."""
    if not model:
        return None
    datasets = getattr(model, "datasets", []) or []
    # 1. Metadata check on datasets
    for ds in datasets:
        if getattr(ds, "is_date_table", False) or getattr(ds, "is_calendar", False):
            return ds
        dc = str(getattr(ds, "data_category", "") or getattr(ds, "dataCategory", "") or "").lower()
        if dc in ("time", "date"):
            return ds
        annotations = getattr(ds, "annotations", {}) or {}
        if isinstance(annotations, dict) and annotations.get("__PBI_LocalDateTable"):
            return ds

    # 2. Target of date-typed relationships
    relationships = getattr(model, "relationships", []) or []
    for rel in relationships:
        to_ds = getattr(rel, "to_dataset", None)
        if not to_ds:
            continue
        to_cols = [str(c).upper() for c in (getattr(rel, "to_columns", []) or [])]
        target_ds = next((d for d in datasets if getattr(d, "unique_name", None) == to_ds), None)
        if target_ds:
            for col in getattr(target_ds, "columns", []) or []:
                c_name = getattr(col, "unique_name", "") or getattr(col, "name", "")
                if c_name.upper() in to_cols:
                    dt = getattr(col, "data_type", None)
                    dt_str = getattr(dt, "value", str(dt)).lower()
                    if dt_str in ("date", "datetime"):
                        return target_ds
            if any("DATE" in c or "TIME" in c or "DAY" in c for c in to_cols):
                return target_ds

    # 3. Logged fallback to name keywords
    for ds in datasets:
        name = getattr(ds, "unique_name", None) or getattr(ds, "name", "")
        if is_calendar_like_name(name):
            import logging
            logging.getLogger(__name__).info(
                "No date table metadata found for '%s'; falling back to name-based detection", name
            )
            return ds

    return None



DIMENSION_OVERRIDE_KEYWORDS = frozenset({"BU", "DIM", "USER", "CALENDAR"})

# "BusinessUnit"/"Business_Unit"/"Business Unit" — a genuine compound term,
# not a single word, so the whole-word tokenizer above (which splits
# camelCase at word boundaries) would otherwise miss it entirely. Matched
# as its own two-word phrase instead of adding "BUSINESS" as a standalone
# keyword, which would false-positive on any real fact table whose name
# merely starts with "Business" (e.g. "Business_Metrics_Fact").
_BUSINESS_UNIT_RE = re.compile(r'\bBUSINESS[_\s]?UNIT\b', re.IGNORECASE)


def is_dimension_like_name(name: str) -> bool:
    """True if `name` contains one of DIMENSION_OVERRIDE_KEYWORDS as a
    whole word, or the "BusinessUnit"-style compound phrase — used to
    force a table SmlInferenceEngine's row/relationship-count score
    classified ambiguously to be treated as a dimension regardless of
    that score."""
    if _BUSINESS_UNIT_RE.search(name or ""):
        return True
    return bool(set(tokenize_dataset_name(name)) & DIMENSION_OVERRIDE_KEYWORDS)
