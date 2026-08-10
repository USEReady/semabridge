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
