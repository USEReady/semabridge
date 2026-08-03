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
