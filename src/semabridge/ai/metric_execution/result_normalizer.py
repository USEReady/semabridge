"""Decimal-safe metric result normalization with explicit NULL policy support."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
from typing import Any, Optional


FOUR_PLACES = Decimal("0.0001")


def normalize_metric_value(
    value: Any,
    null_policy: str = "STRICT_NULL",
    round_places: int = 4,
) -> Optional[Decimal]:
    """Normalize a raw metric value to a Decimal rounded to 4 decimal places.
    
    Acceptance Criteria:
    - Normalize with ROUND(val, 4) (Decimal-safe in Python).
    - Use COALESCE(val, 0) ONLY when the metric's NULL policy explicitly says NULL = 0 (e.g. "NULL_EQUALS_ZERO").
    - Never convert NULLs globally.
    """
    if value is None:
        if str(null_policy or "").strip().upper() in ("NULL_EQUALS_ZERO", "NULL_AS_ZERO", "COALESCE_ZERO"):
            return Decimal("0.0000")
        return None

    # Handle nan / inf
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None

    # Convert to Decimal safely
    try:
        if isinstance(value, Decimal):
            dec = value
        elif isinstance(value, (int, float)):
            dec = Decimal(str(value))
        elif isinstance(value, str):
            clean_str = value.replace(",", "").strip()
            if not clean_str or clean_str.lower() in ("null", "none", "nan"):
                if str(null_policy or "").strip().upper() in ("NULL_EQUALS_ZERO", "NULL_AS_ZERO", "COALESCE_ZERO"):
                    return Decimal("0.0000")
                return None
            dec = Decimal(clean_str)
        elif isinstance(value, bool):
            # Booleans are not numeric metrics
            return None
        else:
            return None
    except (InvalidOperation, ValueError, TypeError):
        return None

    quantum = Decimal(10) ** -round_places
    return dec.quantize(quantum, rounding=ROUND_HALF_UP)
