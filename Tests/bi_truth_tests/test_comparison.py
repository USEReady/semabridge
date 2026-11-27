"""1. The comparison engine.

Pure arithmetic: no database, no warehouse, no fixtures. If any of these needs
setup, the module is reaching somewhere it should not.
"""
from __future__ import annotations

import pytest

from bi_truth_tests import adapter

C = adapter.names(
    adapter.comparison,
    "compare_values", "not_comparable", "summarise", "Verdict",
)
V = C.Verdict


# ------------------------------------------------------------------ tolerance

def test_1_1_a_value_inside_tolerance_passes():
    r = C.compare_values(expected=1000.0, actual=1000.05, tolerance_pct=0.01)
    assert r.verdict is V.PASS


def test_1_2_a_value_outside_tolerance_fails():
    r = C.compare_values(expected=1000.0, actual=1010.0, tolerance_pct=0.01)
    assert r.verdict is V.FAIL


def test_1_3_the_variance_is_reported_as_a_percentage():
    r = C.compare_values(expected=1000.0, actual=990.0, tolerance_pct=0.01)
    assert r.variance_pct == pytest.approx(1.0)


def test_1_4_exactly_at_tolerance_passes():
    r = C.compare_values(expected=100.0, actual=101.0, tolerance_pct=1.0)
    assert r.verdict is V.PASS


def test_1_5_tolerance_of_zero_demands_exact_equality():
    assert C.compare_values(expected=42.0, actual=42.0, tolerance_pct=0.0).verdict is V.PASS
    assert C.compare_values(expected=42.0, actual=42.000001, tolerance_pct=0.0).verdict is V.FAIL


# ------------------------------------------------------------------ nulls

def test_1_6_null_against_null_passes():
    """A window with genuinely no data is not a failure."""
    r = C.compare_values(expected=None, actual=None)
    assert r.verdict is V.PASS


def test_1_7_null_against_zero_fails_and_says_which_side():
    r = C.compare_values(expected=None, actual=0)
    assert r.verdict is V.FAIL
    assert "expected" in r.reason.lower()

    r2 = C.compare_values(expected=0, actual=None)
    assert r2.verdict is V.FAIL
    assert "actual" in r2.reason.lower()


def test_1_8_null_against_a_number_fails():
    assert C.compare_values(expected=None, actual=100).verdict is V.FAIL
    assert C.compare_values(expected=100, actual=None).verdict is V.FAIL


# ------------------------------------------------------------------ near zero

def test_1_9_a_near_zero_expected_value_uses_the_absolute_rule():
    r = C.compare_values(expected=0.0, actual=0.005, floor=0.01, tolerance_pct=0.01)
    assert r.verdict is V.PASS
    assert r.used_absolute_rule


def test_1_10_a_near_zero_difference_beyond_the_floor_fails():
    r = C.compare_values(expected=0.0, actual=5.0, floor=0.01)
    assert r.verdict is V.FAIL
    assert r.used_absolute_rule


def test_1_11_the_floor_is_in_the_measures_own_units():
    """A ratio and a currency total cannot share one floor.

    The same absolute difference of 0.005 is a tenth of a cent on a currency total
    and half a percentage point on a ratio stored as a fraction.
    """
    ratio = C.compare_values(expected=0.001, actual=0.006, floor=0.0001)
    currency = C.compare_values(expected=0.001, actual=0.006, floor=0.01)
    assert ratio.verdict is V.FAIL
    assert currency.verdict is V.PASS


def test_1_12_a_large_value_never_uses_the_absolute_rule():
    r = C.compare_values(expected=1_000_000.0, actual=1_000_000.5, floor=0.01)
    assert not r.used_absolute_rule
    assert r.variance_pct is not None


# ------------------------------------------------------------------ row counts

def test_1_13_a_matching_value_with_differing_rows_is_not_a_pass():
    r = C.compare_values(
        expected=100.0, actual=100.0,
        expected_row_count=1000, actual_row_count=900,
    )
    assert r.verdict is V.ROWS_DIFFER
    assert r.verdict is not V.PASS


def test_1_14_matching_rows_leave_the_verdict_alone():
    r = C.compare_values(
        expected=100.0, actual=100.0,
        expected_row_count=1000, actual_row_count=1000,
    )
    assert r.verdict is V.PASS


def test_1_15_an_unknown_row_count_does_not_change_the_verdict():
    r = C.compare_values(expected=100.0, actual=100.0, expected_row_count=None)
    assert r.verdict is V.PASS


def test_1_16_a_failing_value_stays_failing_whatever_the_rows():
    r = C.compare_values(
        expected=100.0, actual=200.0,
        expected_row_count=10, actual_row_count=10,
    )
    assert r.verdict is V.FAIL


# ------------------------------------------------------------------ other values

def test_1_17_text_values_compare_case_insensitively():
    assert C.compare_values(expected="Open", actual="open").verdict is V.PASS
    assert C.compare_values(expected="Open", actual="Closed").verdict is V.FAIL


def test_1_18_a_boolean_is_not_treated_as_a_number():
    """True would otherwise equal 1.0 and pass against it."""
    r = C.compare_values(expected=1.0, actual=True)
    assert r.verdict is V.FAIL


# ------------------------------------------------------------------ not comparable

def test_1_19_a_case_that_could_not_run_is_not_comparable():
    r = C.not_comparable("the query could not be generated")
    assert r.verdict is V.NOT_COMPARABLE
    assert r.verdict is not V.PASS
    assert r.reason


def test_1_20_not_comparable_is_excluded_from_the_compared_count():
    results = [
        C.compare_values(expected=1.0, actual=1.0),
        C.compare_values(expected=1.0, actual=5.0),
        C.not_comparable("no expected value for this window"),
        C.not_comparable("the window could not be built"),
    ]
    counts = C.summarise(results)
    assert counts["compared"] == 2
    assert counts[V.NOT_COMPARABLE.value] == 2
    assert counts["total"] == 4


def test_1_21_every_result_carries_the_context_it_ran_in():
    ctx = {"window": "w1", "view": "V", "grain": "month"}
    r = C.compare_values(expected=1.0, actual=1.0, context=ctx)
    assert r.context == ctx


def test_1_22_every_verdict_explains_itself():
    for r in (
        C.compare_values(expected=1.0, actual=1.0),
        C.compare_values(expected=1.0, actual=9.0),
        C.compare_values(expected=None, actual=1.0),
        C.compare_values(expected=1.0, actual=1.0,
                         expected_row_count=1, actual_row_count=2),
    ):
        assert r.reason, f"{r.verdict} gave no reason"


# ------------------------------------------------------------------ executor types & platform isolation

def test_1_23_databricks_decimal_and_string_numbers_compare_as_numbers():
    from decimal import Decimal
    from semabridge.ai.executors.databricks_executor import _normalize_databricks_cell

    dec_val = _normalize_databricks_cell(Decimal("1000000.00"))
    str_val = _normalize_databricks_cell("1000000.50")
    assert isinstance(dec_val, float)
    assert isinstance(str_val, float)
    assert C.compare_values(expected=1000000.0, actual=dec_val).verdict is V.PASS
    assert C.compare_values(expected=1000000.5, actual=str_val).verdict is V.PASS


def test_1_24_databricks_null_against_null_and_differing_rows():
    from semabridge.ai.executors.databricks_executor import _normalize_databricks_cell

    assert _normalize_databricks_cell(None) is None
    assert C.compare_values(expected=None, actual=_normalize_databricks_cell(None)).verdict is V.PASS
    r_rows = C.compare_values(
        expected=500000.0,
        actual=_normalize_databricks_cell("500000.0"),
        expected_row_count=1,
        actual_row_count=2,
    )
    assert r_rows.verdict is V.ROWS_DIFFER


def test_1_25_uppercased_databricks_column_names_compare_regardless_of_stored_casing():
    from semabridge.ai.bi_truth_comparison import compare_truth_results

    actual_rows = [{"ACCO_ACCOUNT_NAME": "Acme Corp", "MEASURE(AVG_OF_ANNUAL_REVENUE)": 500000.0}]
    expected_rows = [{"acco_account_name": "Acme Corp", "measure(avg_of_annual_revenue)": 500000.0}]
    ok, err, details = compare_truth_results(actual_rows, expected_rows, expected_row_count=1)
    assert ok is True
    assert err is None
    assert details["verdict"] == "PASS"


def test_1_26_bi_truth_comparison_has_no_platform_or_adapter_imports():
    from pathlib import Path
    import semabridge.ai.bi_truth_comparison as mod

    src = Path(mod.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("snowflake", "databricks", "adapters", "executors"):
        assert forbidden not in src, f"bi_truth_comparison.py must not reference '{forbidden}'"


# ------------------------------------------------------------------ multi-row keyed comparison

def test_1_27_multi_row_same_rows_reordered_pass():
    from semabridge.ai.bi_truth_comparison import compare_truth_results

    expected = [
        {"DIM_A": "Alpha", "MEASURE_X": 100.0},
        {"DIM_A": "Beta", "MEASURE_X": 200.0},
        {"DIM_A": "Gamma", "MEASURE_X": 300.0},
    ]
    actual = [
        {"DIM_A": "Gamma", "MEASURE_X": 300.0},
        {"DIM_A": "Alpha", "MEASURE_X": 100.0},
        {"DIM_A": "Beta", "MEASURE_X": 200.0},
    ]
    ok, err, details = compare_truth_results(actual, expected)
    assert ok is True
    assert err is None
    assert details["verdict"] == "PASS"


def test_1_28_multi_row_missing_row_fails_naming_it():
    from semabridge.ai.bi_truth_comparison import compare_truth_results

    expected = [
        {"DIM_A": "Alpha", "MEASURE_X": 100.0},
        {"DIM_A": "Beta", "MEASURE_X": 200.0},
    ]
    actual = [
        {"DIM_A": "Alpha", "MEASURE_X": 100.0},
    ]
    ok, err, details = compare_truth_results(actual, expected)
    assert ok is False
    assert details["verdict"] == "FAIL"
    assert "Beta" in (err or "")


def test_1_29_multi_row_extra_row_fails_naming_it():
    from semabridge.ai.bi_truth_comparison import compare_truth_results

    expected = [
        {"DIM_A": "Alpha", "MEASURE_X": 100.0},
    ]
    actual = [
        {"DIM_A": "Alpha", "MEASURE_X": 100.0},
        {"DIM_A": "Delta", "MEASURE_X": 400.0},
    ]
    ok, err, details = compare_truth_results(actual, expected)
    assert ok is False
    assert details["verdict"] == "FAIL"
    assert "Delta" in (err or "")


def test_1_30_multi_row_value_differing_fails_naming_that_row():
    from semabridge.ai.bi_truth_comparison import compare_truth_results

    expected = [
        {"DIM_A": "Alpha", "MEASURE_X": 100.0},
        {"DIM_A": "Beta", "MEASURE_X": 200.0},
    ]
    actual = [
        {"DIM_A": "Beta", "MEASURE_X": 250.0},
        {"DIM_A": "Alpha", "MEASURE_X": 100.0},
    ]
    ok, err, details = compare_truth_results(actual, expected)
    assert ok is False
    assert details["verdict"] == "FAIL"
    assert "Beta" in (err or "")
