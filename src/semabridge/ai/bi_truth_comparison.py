"""Pure comparison engine for BI Truth Suite regression verification.

Takes plain values in and returns a verdict out:
  expected, actual, tolerance, floor, expected_row_count, actual_row_count.
Does not read a case row, does not import the ORM, does not touch a session.
Pure arithmetic and data structure comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import os
from typing import Any, Dict, List, Optional, Tuple


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


def get_default_tolerance() -> float:
    """Retrieve default relative variance tolerance from env or settings (default 0.001 = 0.1%)."""
    val = os.getenv("SEMABRIDGE_BI_TRUTH_TOLERANCE", "").strip()
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    try:
        from semabridge.core.settings import get_settings
        cfg = getattr(get_settings(), "bi_truth", None)
        if cfg and hasattr(cfg, "tolerance"):
            return float(cfg.tolerance)
    except Exception:
        pass
    return 1e-3


def get_default_absolute_floor() -> float:
    """Retrieve absolute tolerance floor in measure's own units for values near zero (default 0.0001)."""
    val = os.getenv("SEMABRIDGE_BI_TRUTH_ABSOLUTE_FLOOR", "").strip()
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    try:
        from semabridge.core.settings import get_settings
        cfg = getattr(get_settings(), "bi_truth", None)
        if cfg and hasattr(cfg, "absolute_floor"):
            return float(cfg.absolute_floor)
    except Exception:
        pass
    return 1e-4


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
        expected=expected,
        actual=actual,
        tolerance_pct=tolerance_pct,
        floor=floor,
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


def _extract_scalar(val: Any) -> Tuple[Any, Optional[int]]:
    """Extract single scalar value and row count from query output or benchmark."""
    if isinstance(val, list):
        rc = len(val)
        if rc == 0:
            return None, 0
        if rc == 1 and isinstance(val[0], dict):
            vals = list(val[0].values())
            if len(vals) == 1:
                return vals[0], rc
            chosen = next((v for v in vals if isinstance(v, (int, float))), vals[-1])
            return chosen, rc
        return val, rc
    return val, 1


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).strip().casefold(): v for k, v in row.items()}


def _split_row_columns(
    exp_rows: List[Dict[str, Any]],
    act_rows: List[Dict[str, Any]],
    metric_name: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Return (dim_cols, measure_cols) using case-insensitive column names."""
    all_cols: List[str] = []
    for r in exp_rows + act_rows:
        for k in r.keys():
            if k not in all_cols:
                all_cols.append(k)

    m_cf = metric_name.strip().casefold() if metric_name else ""
    measure_cols: List[str] = []
    if m_cf:
        measure_cols = [
            c for c in all_cols
            if c == m_cf or c == f"measure({m_cf})"
        ]

    if not measure_cols:
        # Columns that are non-numeric in any row are dimensions; remaining numeric columns are measures.
        dim_cols = [
            c for c in all_cols
            if any(
                r.get(c) is not None and _as_number(r.get(c)) is None
                for r in exp_rows + act_rows
            )
        ]
        measure_cols = [c for c in all_cols if c not in dim_cols]
        if not dim_cols and len(all_cols) > 1:
            dim_cols = all_cols[:-1]
            measure_cols = [all_cols[-1]]
    else:
        dim_cols = [c for c in all_cols if c not in measure_cols]

    return dim_cols, measure_cols


def _row_key_and_label(row: Dict[str, Any], dim_cols: List[str]) -> Tuple[Tuple[Tuple[str, str], ...], str]:
    key_parts: List[Tuple[str, str]] = []
    label_parts: List[str] = []
    for c in dim_cols:
        v = row.get(c)
        norm_v = "" if v is None else str(v).strip().casefold()
        key_parts.append((c, norm_v))
        label_parts.append(f"{c}={v!r}")
    label = ", ".join(label_parts) if label_parts else repr(row)
    return tuple(key_parts), label


def _compare_multi_row(
    exp_rows: List[Dict[str, Any]],
    act_rows: List[Dict[str, Any]],
    tol_pct: float,
    floor: float,
    effective_expected_rc: Optional[int],
    metric_name: Optional[str] = None,
) -> Comparison:
    """Compare multi-row result sets as a set keyed on dimension values, independent of row order."""
    norm_exp = [_normalize_row(r) for r in exp_rows]
    norm_act = [_normalize_row(r) for r in act_rows]
    dim_cols, measure_cols = _split_row_columns(norm_exp, norm_act, metric_name=metric_name)

    exp_map: Dict[Tuple[Tuple[str, str], ...], Tuple[str, Dict[str, Any]]] = {}
    act_map: Dict[Tuple[Tuple[str, str], ...], Tuple[str, Dict[str, Any]]] = {}
    exp_counts: Dict[Tuple[Tuple[str, str], ...], int] = {}
    act_counts: Dict[Tuple[Tuple[str, str], ...], int] = {}

    for r in norm_exp:
        k, lbl = _row_key_and_label(r, dim_cols)
        idx = exp_counts.get(k, 0)
        exp_counts[k] = idx + 1
        full_k = k + (("__dup__", str(idx)),) if idx > 0 else k
        exp_map[full_k] = (lbl, r)

    for r in norm_act:
        k, lbl = _row_key_and_label(r, dim_cols)
        idx = act_counts.get(k, 0)
        act_counts[k] = idx + 1
        full_k = k + (("__dup__", str(idx)),) if idx > 0 else k
        act_map[full_k] = (lbl, r)

    base = dict(
        expected=exp_rows,
        actual=act_rows,
        tolerance_pct=tol_pct,
        floor=floor,
        expected_row_count=effective_expected_rc,
        actual_row_count=len(act_rows),
    )

    missing_keys = [k for k in exp_map if k not in act_map]
    extra_keys = [k for k in act_map if k not in exp_map]

    if missing_keys:
        missing_lbls = "; ".join(exp_map[k][0] for k in missing_keys[:5])
        return Comparison(
            Verdict.FAIL,
            reason=f"missing row(s) in actual result: {missing_lbls}",
            **base,
        )

    if extra_keys:
        extra_lbls = "; ".join(act_map[k][0] for k in extra_keys[:5])
        return Comparison(
            Verdict.FAIL,
            reason=f"unexpected extra row(s) in actual result: {extra_lbls}",
            **base,
        )

    for k, (lbl, exp_r) in exp_map.items():
        _, act_r = act_map[k]
        for mc in measure_cols:
            row_comp = compare_values(
                expected=exp_r.get(mc),
                actual=act_r.get(mc),
                tolerance_pct=tol_pct,
                floor=floor,
            )
            if not row_comp.passed:
                return Comparison(
                    Verdict.FAIL,
                    variance_pct=row_comp.variance_pct,
                    absolute_difference=row_comp.absolute_difference,
                    used_absolute_rule=row_comp.used_absolute_rule,
                    reason=f"row ({lbl}) measure '{mc}': {row_comp.reason}",
                    **base,
                )

    return _row_check(Comparison(
        Verdict.PASS,
        reason=f"all {len(exp_map)} row(s) match within {tol_pct}%",
        **base,
    ))


def compare_truth_results(
    actual: Any,
    expected: Any,
    tolerance: Optional[float] = None,
    absolute_floor: Optional[float] = None,
    expected_row_count: Optional[int] = None,
    metric_name: Optional[str] = None,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """Backward-compatible adapter for existing bi_truth_service callers."""
    tol = tolerance if tolerance is not None else get_default_tolerance()
    floor = absolute_floor if absolute_floor is not None else get_default_absolute_floor()
    tol_pct = tol * 100.0 if tol < 1.0 else tol

    if (
        isinstance(actual, list)
        and isinstance(expected, list)
        and len(actual) > 0
        and len(expected) > 0
        and all(isinstance(r, dict) for r in actual)
        and all(isinstance(r, dict) for r in expected)
        and (len(actual) > 1 or len(expected) > 1)
    ):
        effective_expected_rc = expected_row_count if expected_row_count else len(expected)
        comp = _compare_multi_row(
            exp_rows=expected,
            act_rows=actual,
            tol_pct=tol_pct,
            floor=floor,
            effective_expected_rc=effective_expected_rc,
            metric_name=metric_name,
        )
        act_rc = len(actual)
    else:
        act_scalar, act_rc = _extract_scalar(actual)
        exp_scalar, exp_rc = _extract_scalar(expected)
        effective_expected_rc = expected_row_count if expected_row_count is not None else exp_rc
        comp = compare_values(
            expected=exp_scalar,
            actual=act_scalar,
            tolerance_pct=tol_pct,
            floor=floor,
            expected_row_count=effective_expected_rc,
            actual_row_count=act_rc,
        )

    details = {
        "tolerance": tol,
        "absolute_floor": floor,
        "rule_applied": "absolute_floor" if comp.used_absolute_rule else ("null_match" if comp.expected is None and comp.actual is None else "tolerance"),
        "expected_row_count": effective_expected_rc,
        "actual_row_count": act_rc,
        "delta": comp.absolute_difference,
        "variance_pct": comp.variance_pct,
        "verdict": comp.verdict.value,
        "reason": comp.reason,
    }

    if comp.verdict is Verdict.PASS:
        return True, None, details
    elif comp.verdict is Verdict.ROWS_DIFFER:
        return False, comp.reason, details
    else:
        return False, comp.reason, details
