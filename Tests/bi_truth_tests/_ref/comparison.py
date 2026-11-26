"""Reference comparison engine.

Takes plain values in and returns a verdict out. No database, no warehouse, no ORM:
the tolerance and the floor arrive as arguments because the caller reads them from
the case.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ROWS_DIFFER = "ROWS_DIFFER"      # the value agreed, the populations did not
    NOT_COMPARABLE = "NOT_COMPARABLE"  # nothing could be compared


PASS = Verdict.PASS
FAIL = Verdict.FAIL
ROWS_DIFFER = Verdict.ROWS_DIFFER
NOT_COMPARABLE = Verdict.NOT_COMPARABLE

DEFAULT_TOLERANCE_PCT = 0.01   # percent, not a fraction
DEFAULT_FLOOR = 0.01           # in the measure's own units


@dataclass
class Comparison:
    verdict: Verdict
    expected: Any = None
    actual: Any = None
    variance_pct: Optional[float] = None
    absolute_difference: Optional[float] = None
    used_absolute_rule: bool = False
    tolerance_pct: Optional[float] = None
    floor: Optional[float] = None
    expected_row_count: Optional[int] = None
    actual_row_count: Optional[int] = None
    reason: str = ""
    context: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS


def _as_number(value: Any) -> Optional[float]:
    """A float when the value is numeric, otherwise None. Booleans are not numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare_values(
    expected: Any,
    actual: Any,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    floor: float = DEFAULT_FLOOR,
    expected_row_count: Optional[int] = None,
    actual_row_count: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Comparison:
    """Compare one expected value against one actual value.

    Null against null passes: a window with genuinely no data is not a failure.
    Null against a number fails, and says which side was null.
    Below the floor the absolute difference decides, because a percentage of a
    near-zero expected value is meaningless. The floor is in the measure's own
    units, so a ratio and a currency total do not share one number.
    A matching value over a differing row count is reported, never passed.
    """
    ctx = dict(context or {})
    base = dict(
        expected=expected, actual=actual,
        tolerance_pct=tolerance_pct, floor=floor,
        expected_row_count=expected_row_count,
        actual_row_count=actual_row_count,
        context=ctx,
    )

    # 1. Nulls, before anything numeric.
    if expected is None and actual is None:
        return Comparison(Verdict.PASS, reason="both sides have no value", **base)
    if expected is None:
        return Comparison(Verdict.FAIL, reason="the expected value is null and the actual value is not", **base)
    if actual is None:
        return Comparison(Verdict.FAIL, reason="the actual value is null and the expected value is not", **base)

    e = _as_number(expected)
    a = _as_number(actual)

    # 2. Non-numeric values compare as text, case-insensitively.
    if e is None or a is None:
        if str(expected).strip().casefold() == str(actual).strip().casefold():
            return _row_check(Comparison(Verdict.PASS, reason="values match", **base))
        return Comparison(
            Verdict.FAIL,
            reason=f"values differ: expected {expected!r}, got {actual!r}",
            **base,
        )

    difference = abs(e - a)
    base["absolute_difference"] = difference

    # 3. Near zero: the percentage is meaningless, so the floor decides.
    if abs(e) < floor:
        base["used_absolute_rule"] = True
        if difference <= floor:
            return _row_check(Comparison(
                Verdict.PASS,
                reason=f"difference {difference} is within the floor of {floor}",
                **base,
            ))
        return Comparison(
            Verdict.FAIL,
            reason=f"difference {difference} exceeds the floor of {floor}",
            **base,
        )

    # 4. Everything else: relative variance.
    variance = (difference / abs(e)) * 100.0
    base["variance_pct"] = variance
    if variance <= tolerance_pct:
        return _row_check(Comparison(
            Verdict.PASS,
            reason=f"variance {variance:.6f}% is within {tolerance_pct}%",
            **base,
        ))
    return Comparison(
        Verdict.FAIL,
        reason=f"variance {variance:.6f}% exceeds {tolerance_pct}%",
        **base,
    )


def _row_check(result: Comparison) -> Comparison:
    """A matching value over a differing population is not a pass."""
    expected_rows = result.expected_row_count
    actual_rows = result.actual_row_count
    if expected_rows is None or actual_rows is None:
        return result
    if int(expected_rows) == int(actual_rows):
        return result
    result.verdict = Verdict.ROWS_DIFFER
    result.reason = (
        f"the value matched but the row counts differ: expected {expected_rows}, "
        f"got {actual_rows}"
    )
    return result


def not_comparable(reason: str, context: Optional[Dict[str, Any]] = None) -> Comparison:
    """For a case or window that could not be evaluated at all."""
    return Comparison(Verdict.NOT_COMPARABLE, reason=reason, context=dict(context or {}))


def summarise(results) -> Dict[str, int]:
    """Counts by verdict, plus how many were actually compared."""
    counts = {v.value: 0 for v in Verdict}
    for r in results:
        counts[r.verdict.value] += 1
    counts["compared"] = (
        counts[Verdict.PASS.value]
        + counts[Verdict.FAIL.value]
        + counts[Verdict.ROWS_DIFFER.value]
    )
    counts["total"] = sum(1 for _ in results) if not hasattr(results, "__len__") else len(results)
    return counts
