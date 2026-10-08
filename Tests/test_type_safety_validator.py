"""Unit tests for the general date-vs-numeric type-mismatch detector.

See connectors/type_safety_validator.py's module docstring for the real
incident this closes: a Tier-5 translation combined a date-arithmetic
expression with an INTEGER surrogate-key column, and nothing caught it
before the resulting DDL crashed a live Snowflake deploy. These tests use
only synthetic placeholder names/columns -- no real project or metric names,
and no live LLM or Snowflake connection is needed to run them.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.type_safety_validator import (
    build_dataset_col_types,
    detect_date_numeric_type_mismatch,
    detect_nested_aggregate,
)

DATASET_ALIASES = {"SalesFact": "SALESFACT"}
COL_TYPES = {"SalesFact": {"MONTHINDEX": "INTEGER", "MAX_DATE": "DATE", "UNITS": "INTEGER"}}


def test_column_before_date_function_is_flagged():
    sql = 'SALESFACT.MONTHINDEX > DATE_ADDDAYSTODATE(NEGATE(12), SALESFACT.MAX_DATE)'
    reason = detect_date_numeric_type_mismatch(sql, DATASET_ALIASES, COL_TYPES)
    assert reason is not None
    assert "Type mismatch" in reason
    assert "MONTHINDEX" in reason


def test_column_after_date_function_is_flagged():
    sql = 'DATEADD(MONTH, -12, SALESFACT.MAX_DATE) <= SALESFACT.MONTHINDEX'
    reason = detect_date_numeric_type_mismatch(sql, DATASET_ALIASES, COL_TYPES)
    assert reason is not None
    assert "MONTHINDEX" in reason


def test_quoted_column_reference_is_also_flagged():
    sql = 'SALESFACT."MONTHINDEX" > TO_DATE(SALESFACT."MAX_DATE")'
    reason = detect_date_numeric_type_mismatch(sql, DATASET_ALIASES, COL_TYPES)
    assert reason is not None


def test_date_compared_to_date_is_not_flagged():
    """No false positive: comparing a date-arithmetic expression against a
    genuinely DATE-typed column is exactly what these functions are for."""
    sql = 'SALESFACT.MAX_DATE >= DATE_TRUNC(YEAR, SALESFACT.MAX_DATE)'
    assert detect_date_numeric_type_mismatch(sql, DATASET_ALIASES, COL_TYPES) is None


def test_extracting_a_number_from_a_date_is_not_flagged():
    """YEAR()/MONTH()/DATEDIFF() extract a NUMBER out of a date -- comparing
    that result to an integer column is the *correct* pattern, not a bug,
    and must never be flagged."""
    sql = 'SALESFACT.MONTHINDEX = DATEDIFF(MONTH, SALESFACT.MAX_DATE, CURRENT_DATE())'
    # DATEDIFF itself isn't in DATE_PRODUCING_FUNCTIONS (it returns a number),
    # so only the CURRENT_DATE() argument inside it could match -- and it has
    # no adjacent qualified column comparison of its own.
    assert detect_date_numeric_type_mismatch(sql, DATASET_ALIASES, COL_TYPES) is None


def test_no_type_info_available_never_flags_anything():
    """If dataset_col_types is empty (e.g. a caller that hasn't been wired
    up yet), the check must stay silent rather than guess -- matching how
    every other schema-driven check in this codebase treats missing
    schema info as 'cannot confirm', not 'assume broken'."""
    sql = 'SALESFACT.MONTHINDEX > DATE_ADDDAYSTODATE(NEGATE(12), SALESFACT.MAX_DATE)'
    assert detect_date_numeric_type_mismatch(sql, DATASET_ALIASES, {}) is None


def test_unrelated_scalar_metric_sql_is_not_flagged():
    sql = 'SUM(SALESFACT."UNITS"::FLOAT)'
    assert detect_date_numeric_type_mismatch(sql, DATASET_ALIASES, COL_TYPES) is None


def test_build_dataset_col_types_from_sml_style_columns():
    datasets = [
        SimpleNamespace(
            unique_name="SalesFact",
            columns=[
                SimpleNamespace(unique_name="MonthIndex", data_type=SimpleNamespace(value="integer")),
                SimpleNamespace(unique_name="Max Date", data_type=SimpleNamespace(value="date")),
            ],
        )
    ]
    types = build_dataset_col_types(datasets)
    assert types["SalesFact"]["MONTHINDEX"] == "INTEGER"
    assert types["SalesFact"]["MAX_DATE"] == "DATE"


def test_build_dataset_col_types_handles_missing_datasets_gracefully():
    assert build_dataset_col_types(None) == {}
    assert build_dataset_col_types([]) == {}


# ---------------------------------------------------------------------------
# detect_nested_aggregate
#
# See connectors/type_safety_validator.py's module docstring for the real
# incident this closes: a Tier-5 translation for a rolling-window metric
# produced SUM(CASE WHEN MAX(...) - ... < 12 THEN ... END) -- an aggregate
# nested inside another aggregate's row-level argument -- and nothing caught
# it before the resulting DDL crashed a live Snowflake deploy with 010218.
# These tests use only synthetic placeholder table/column names, never a
# real metric or model name, and cover any aggregate pair generally.
# ---------------------------------------------------------------------------

def test_aggregate_nested_inside_case_inside_another_aggregate_is_flagged():
    """The exact real-incident shape: SUM(CASE WHEN MAX(...) ... END)."""
    sql = (
        'SUM(CASE WHEN MAX(SOME_TABLE.SOME_COL) - SOME_TABLE.SOME_COL < 12 '
        'THEN SOME_TABLE.OTHER_COL ELSE 0 END::FLOAT)'
    )
    reason = detect_nested_aggregate(sql)
    assert reason is not None
    assert "Nested aggregate" in reason
    assert "SUM" in reason and "MAX" in reason


def test_any_aggregate_pair_is_flagged_not_just_sum_max():
    """General over any pair -- AVG nested inside COUNT, not a SUM/MAX-only
    special case."""
    sql = 'COUNT(CASE WHEN AVG(SOME_TABLE.SCORE) > 3 THEN 1 ELSE 0 END)'
    reason = detect_nested_aggregate(sql)
    assert reason is not None
    assert "COUNT" in reason and "AVG" in reason


def test_directly_nested_aggregate_with_no_case_wrapping_is_flagged():
    sql = 'SUM(SUM(SOME_TABLE.AMOUNT))'
    reason = detect_nested_aggregate(sql)
    assert reason is not None


def test_single_aggregate_over_case_is_not_flagged():
    """The normal, legitimate flat shape every metric should use: one
    aggregate wrapping a row-level CASE, no aggregate inside it."""
    sql = 'SUM(CASE WHEN SOME_TABLE.FLAG = 1 THEN SOME_TABLE.UNITS ELSE 0 END)'
    assert detect_nested_aggregate(sql) is None


def test_sibling_aggregates_in_a_ratio_are_not_flagged():
    """Two aggregates that are SIBLINGS under a non-aggregate wrapper (the
    normal ratio-metric shape, e.g. COALESCE(SUM(x) / NULLIF(SUM(y), 0), 0))
    are not nested in each other and must not be flagged."""
    sql = (
        'COALESCE(SUM(CASE WHEN SOME_TABLE.FLAG = 1 THEN SOME_TABLE.UNITS ELSE 0 END) '
        '/ NULLIF(SUM(CASE WHEN SOME_TABLE.FLAG = 1 THEN SOME_TABLE.UNITS ELSE 0 END), 0), 0)'
    )
    assert detect_nested_aggregate(sql) is None


def test_no_aggregate_at_all_is_not_flagged():
    assert detect_nested_aggregate('SOME_TABLE.UNITS::FLOAT') is None


def test_empty_sql_is_not_flagged():
    assert detect_nested_aggregate("") is None
    assert detect_nested_aggregate(None) is None
