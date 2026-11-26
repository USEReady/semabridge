"""Exact, keyed comparison of probe results with hand-computed expectations.

* Rows are matched by their group key (order never matters); missing, extra and
  duplicate rows are each reported.
* NULL is never treated as zero (and zero never as NULL).
* Integers and DECIMAL compare exactly. A DECIMAL result of a division may carry
  Databricks' fixed scale: it matches when the exact expectation rounds
  (half-up) to exactly that value at that scale -- reported as rounding.
* Only floating-point results (FLOAT / DOUBLE) use a tolerance (relative and
  absolute 1e-9), and only because binary floating point cannot be exact.
* Strings compare exactly.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

FLOAT_TOLERANCE = 1e-9


def _num(v: Any) -> Optional[Decimal]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, Decimal)):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    try:
        return Decimal(str(v)) if isinstance(v, str) else None
    except InvalidOperation:
        return None


def values_match(expected: Any, actual: Any) -> Tuple[bool, Optional[str]]:
    """(match, note). The note says how a non-exact match was accepted."""
    if expected is None or actual is None:
        return expected is None and actual is None, None
    if isinstance(expected, str) or isinstance(actual, str) and _num(expected) is None:
        return str(expected) == str(actual), None
    e = _num(expected)
    if e is None:
        return expected == actual, None
    if isinstance(actual, float):
        ok = math.isclose(float(e), actual, rel_tol=FLOAT_TOLERANCE, abs_tol=FLOAT_TOLERANCE)
        return ok, None if not ok or float(e) == actual else "floating-point tolerance"
    a = _num(actual)
    if a is None:
        return False, None
    if a == e:
        return True, None
    if isinstance(actual, Decimal) and a.as_tuple().exponent < 0:
        q = e.quantize(Decimal(1).scaleb(a.as_tuple().exponent), rounding=ROUND_HALF_UP)
        if q == a:
            return True, f"DECIMAL rounded to {-a.as_tuple().exponent} places"
    return False, None


def compare(expected: Dict[Tuple[Any, ...], Dict[str, Any]], rows: Sequence[Dict[str, Any]],
            dims: Sequence[str], measures: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    """``measures``: (expected key, result column). Returns {ok, mismatches, missing,
    extra, duplicates, notes}."""
    seen: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    duplicates: List[str] = []
    for row in rows:
        key = tuple(row.get(d) for d in dims)
        if key in seen:
            duplicates.append(repr(key))
        seen[key] = row
    mismatches: List[Dict[str, Any]] = []
    notes: List[str] = []
    missing = [repr(k) for k in expected if k not in seen]
    extra = [repr(k) for k in seen if k not in expected]
    for key, want in expected.items():
        row = seen.get(key)
        if row is None:
            continue
        for name, column in measures:
            if name not in want:
                continue
            ok, note = values_match(want[name], row.get(column))
            if not ok:
                mismatches.append({"group": repr(key), "measure": name, "expected": _show(want[name]),
                                   "actual": _show(row.get(column))})
            elif note:
                notes.append(f"{key} {name}: {note}")
    return {"ok": not (mismatches or missing or extra or duplicates), "mismatches": mismatches,
            "missing": missing, "extra": extra, "duplicates": duplicates, "notes": notes}


def _show(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    return v
