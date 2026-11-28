"""Tests for comparison engine variance, tolerance, and floor rules."""
import pytest
from semabridge.ai.bi_truth_comparison import (
    Verdict,
    compare_metric_execution,
)


def test_pass_within_tolerance():
    # 100.00 vs 100.005 -> 0.005% variance <= 0.01% tolerance
    comp = compare_metric_execution(100.0, 100.005, tolerance_pct=0.01)
    assert comp.verdict == Verdict.PASS
    assert comp.passed is True
    assert comp.variance_pct is not None
    assert comp.variance_pct < 0.01


def test_fail_exceeding_tolerance():
    # 100.0 vs 100.05 -> 0.05% variance > 0.01% tolerance
    comp = compare_metric_execution(100.0, 100.05, tolerance_pct=0.01)
    assert comp.verdict == Verdict.FAIL
    assert comp.passed is False


def test_zero_source_absolute_floor():
    # Source is 0.0, target is 0.005, floor is 0.01 -> PASS via absolute floor
    comp = compare_metric_execution(0.0, 0.005, tolerance_pct=0.01, floor=0.01)
    assert comp.verdict == Verdict.PASS
    assert comp.used_absolute_rule is True

    # Source is 0.0, target is 0.05, floor is 0.01 -> FAIL
    comp_fail = compare_metric_execution(0.0, 0.05, tolerance_pct=0.01, floor=0.01)
    assert comp_fail.verdict == Verdict.FAIL


def test_null_handling():
    # Both NULL -> PASS
    comp_both_null = compare_metric_execution(None, None, null_policy="STRICT_NULL")
    assert comp_both_null.verdict == Verdict.PASS

    # One NULL with STRICT_NULL -> FAIL
    comp_one_null = compare_metric_execution(100.0, None, null_policy="STRICT_NULL")
    assert comp_one_null.verdict == Verdict.FAIL

    # One NULL with NULL_EQUALS_ZERO -> coalesces to 0
    comp_coalesce = compare_metric_execution(0.0, None, null_policy="NULL_EQUALS_ZERO", floor=0.01)
    assert comp_coalesce.verdict == Verdict.PASS


def test_not_comparable_status():
    comp = compare_metric_execution(None, None, not_comparable=True)
    assert comp.verdict == Verdict.NOT_COMPARABLE

    comp_no_baseline = compare_metric_execution(None, 50.0, source_error="NO_BASELINE_FIXTURE")
    assert comp_no_baseline.verdict == Verdict.NOT_COMPARABLE
