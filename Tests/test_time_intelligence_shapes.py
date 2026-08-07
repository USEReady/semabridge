"""Regression tests for the time-intelligence shape-discovery pass (Part 2,
Step 1 of the MAX_DATE-capability-limit fix). See
semabridge.converter.time_intelligence_shapes for the design rationale.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.converter.time_intelligence_shapes import (
    build_shape_boolean_sql,
    discover_time_intelligence_shapes,
    flag_column_name,
)


def _metric(name: str, expr: str) -> SimpleNamespace:
    return SimpleNamespace(unique_name=name, expression=expr)


def test_model_with_only_direct_shapes_returns_no_nested_combination():
    """A model using TOTALYTD and a plain SAMEPERIODLASTYEAR (of a measure
    with no date window of its own) — no metric composes the two — must
    report exactly the two direct shapes, and nothing nested."""
    metrics = [
        _metric("TOTAL_UNITS", "SUM([Units])"),
        _metric("TOTAL_UNITS_YTD", "TOTALYTD([TOTAL_UNITS], 'Date'[Date])"),
        _metric("TOTAL_UNITS_SPLY", "CALCULATE([TOTAL_UNITS],SAMEPERIODLASTYEAR('Date'[Date]))"),
    ]
    shapes = discover_time_intelligence_shapes(metrics)
    assert shapes == {("YTD",), ("SPLY_YEAR",)}


def test_model_with_nested_composition_reports_it_as_its_own_shape():
    """SAMEPERIODLASTYEAR wrapping a YTD-shaped measure — including
    transitively, through an intermediate CALCULATE(..., FILTER(ALL(...)))
    layer that changes rows but not the date window (the exact real shape
    of TOTAL_VANARSDEL_UNITS_YTD_SPLY in proj-test-1) — must report the
    nested ("YTD", "SPLY_YEAR") shape explicitly, not just its two
    components separately, and not derive it from mere co-occurrence."""
    metrics = [
        _metric("TOTAL_UNITS", "SUM([Units])"),
        _metric("TOTAL_UNITS_YTD", "TOTALYTD([TOTAL_UNITS], 'Date'[Date])"),
        _metric(
            "TOTAL_VANARSDEL_UNITS_YTD",
            'CALCULATE([TOTAL_UNITS_YTD], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]="Yes"))',
        ),
        _metric(
            "TOTAL_VANARSDEL_UNITS_YTD_SPLY",
            "CALCULATE([TOTAL_VANARSDEL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
        ),
    ]
    shapes = discover_time_intelligence_shapes(metrics)
    assert ("YTD", "SPLY_YEAR") in shapes
    assert ("YTD",) in shapes  # the base shape is still needed too (TOTAL_UNITS_YTD itself)


def test_model_with_no_time_intelligence_metrics_returns_empty_set():
    """No time-intelligence shapes anywhere in the model must return an
    empty set -- never a default/guessed set of flags to provision."""
    metrics = [
        _metric("TOTAL_UNITS", "SUM([Units])"),
        _metric("SALES_DOL", "SUM([Revenue])"),
        _metric("PCT_UNITS_MARKET_SHARE", "DIVIDE([TOTAL_UNITS], [SALES_DOL], 0)"),
    ]
    assert discover_time_intelligence_shapes(metrics) == set()


def test_arithmetic_combining_two_shapes_does_not_need_its_own_flag():
    """A metric like TOTAL_UNITS_YTD_VAR ([TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY])
    inlines two already-shaped measures via simple subtraction -- it needs
    no flag column of its own; only its two dependencies' shapes matter."""
    metrics = [
        _metric("TOTAL_UNITS", "SUM([Units])"),
        _metric("TOTAL_UNITS_YTD", "TOTALYTD([TOTAL_UNITS], 'Date'[Date])"),
        _metric("TOTAL_UNITS_YTD_SPLY", "CALCULATE([TOTAL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))"),
        _metric("TOTAL_UNITS_YTD_VAR", "[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]"),
    ]
    shapes = discover_time_intelligence_shapes(metrics)
    assert shapes == {("YTD",), ("YTD", "SPLY_YEAR")}


def test_flag_column_name_is_deterministic_and_shape_derived():
    assert flag_column_name(("YTD",)) == "IS_YTD"
    assert flag_column_name(("YTD", "SPLY_YEAR")) == "IS_YTD_SPLY_YEAR"
    assert flag_column_name(("SPLY_MONTH",)) == "IS_SPLY_MONTH"


def test_build_shape_boolean_sql_matches_known_correct_ytd_and_nested_forms():
    """Cross-checked directly against proj-test-1's real, previously-working
    cached sql_expression text for TOTAL_UNITS_YTD and TOTAL_UNITS_YTD_SPLY
    (captured before this fix, from the live SML snapshot)."""
    date_col = 'COL_DATE."COL_DATE"'
    anchor = 'SALESFACT."MAX_DATE"'

    ytd = build_shape_boolean_sql(("YTD",), date_col, anchor)
    assert ytd == (
        'COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', SALESFACT."MAX_DATE") '
        'AND COL_DATE."COL_DATE" <= SALESFACT."MAX_DATE"'
    )

    nested = build_shape_boolean_sql(("YTD", "SPLY_YEAR"), date_col, anchor)
    assert nested == (
        'COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', DATEADD(YEAR, -1, SALESFACT."MAX_DATE")) '
        'AND COL_DATE."COL_DATE" <= DATEADD(YEAR, -1, SALESFACT."MAX_DATE")'
    )


def test_build_shape_boolean_sql_month_and_quarter_lag_use_whole_period_equality():
    """Month/quarter lag alone (no period-to-date component) bounds to the
    whole prior month/quarter via equality, not a partial-period BETWEEN --
    matching _render_lag_period's existing month/quarter formulas exactly,
    which are NOT the same shape as the year case."""
    date_col = 'D."DATE"'
    anchor = 'F."MAX_DATE"'

    month_sql = build_shape_boolean_sql(("SPLY_MONTH",), date_col, anchor)
    assert "MONTH(D.\"DATE\") = MONTH(DATEADD(MONTH, -1, F.\"MAX_DATE\"))" in month_sql
    assert "BETWEEN" not in month_sql

    quarter_sql = build_shape_boolean_sql(("SPLY_QUARTER",), date_col, anchor)
    assert "QUARTER(D.\"DATE\") = QUARTER(DATEADD(QUARTER, -1, F.\"MAX_DATE\"))" in quarter_sql
