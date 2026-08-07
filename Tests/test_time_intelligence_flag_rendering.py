"""Regression tests for Part 2, Step 3 of the MAX_DATE-capability-limit fix:
DaxSqlRenderer's TOTALYTD/TOTALMTD/TOTALQTD (_render_period_to_date) and
SAMEPERIODLASTYEAR/PREVIOUSxxx (_render_lag_period) rendering, with and
without an anchor_flag_map (see converter/time_intelligence_shapes.py).

Two properties must both hold:
  (a) with a flag map, the shape being rendered references its precomputed
      flag column, never MAX_DATE;
  (b) with no flag map (the schema-blind dry-run/preview path), output is
      byte-identical to the pre-fix inline MAX_DATE rendering — zero
      regression for that path.
"""
from __future__ import annotations

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer


def _render(dax: str, measure_sql_map=None, anchor_flag_map=None, table_alias="SALESFACT", date_alias="COL_DATE"):
    ast = DaxAstParser().parse(dax)
    renderer = DaxSqlRenderer(
        table_alias=table_alias,
        date_alias=date_alias,
        measure_sql_map=measure_sql_map or {},
        anchor_flag_map=anchor_flag_map,
    )
    return renderer.render(ast)


# ---------------------------------------------------------------------------
# TOTALYTD (direct aggregate) — _render_period_to_date
# ---------------------------------------------------------------------------

def test_totalytd_direct_aggregate_dry_run_is_byte_identical_to_pre_fix_behavior():
    sql = _render("TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])")
    assert sql == (
        'SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', SALESFACT."MAX_DATE") '
        'AND COL_DATE."COL_DATE" <= SALESFACT."MAX_DATE" THEN SALESFACT."UNITS"::FLOAT END)'
    )


def test_totalytd_direct_aggregate_uses_flag_column_when_provided():
    sql = _render(
        "TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
        anchor_flag_map={("YTD",): "IS_YTD"},
    )
    assert sql == 'SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS"::FLOAT END)'
    assert "MAX_DATE" not in sql


# ---------------------------------------------------------------------------
# TOTALYTD (measure-reference fallback) — _render_period_to_date
# ---------------------------------------------------------------------------

def test_totalytd_measure_ref_dry_run_is_byte_identical_to_pre_fix_behavior():
    sql = _render(
        "TOTALYTD([TOTAL_UNITS], 'Date'[Date])",
        measure_sql_map={"TOTAL_UNITS": 'SUM(SALESFACT."UNITS"::FLOAT)'},
    )
    assert sql == (
        'SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', SALESFACT."MAX_DATE") '
        'AND COL_DATE."COL_DATE" <= SALESFACT."MAX_DATE" THEN SALESFACT."UNITS"::FLOAT ELSE NULL END)'
    )


def test_totalytd_measure_ref_uses_flag_column_when_provided():
    sql = _render(
        "TOTALYTD([TOTAL_UNITS], 'Date'[Date])",
        measure_sql_map={"TOTAL_UNITS": 'SUM(SALESFACT."UNITS"::FLOAT)'},
        anchor_flag_map={("YTD",): "IS_YTD"},
    )
    assert sql == 'SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS"::FLOAT ELSE NULL END)'


# ---------------------------------------------------------------------------
# SAMEPERIODLASTYEAR of a plain measure (direct aggregate) — _render_lag_period
# ---------------------------------------------------------------------------

def test_sply_year_direct_aggregate_dry_run_is_byte_identical_to_pre_fix_behavior():
    sql = _render("CALCULATE(SUM('SalesFact'[Units]), SAMEPERIODLASTYEAR('Date'[Date]))")
    assert sql == (
        'SUM(CASE WHEN YEAR(COL_DATE."COL_DATE") = YEAR(SALESFACT."MAX_DATE") - 1 '
        'AND COL_DATE."COL_DATE" BETWEEN DATEADD(YEAR, -1, DATE_TRUNC(\'YEAR\', SALESFACT."MAX_DATE")) '
        'AND DATEADD(YEAR, -1, SALESFACT."MAX_DATE") THEN SALESFACT."UNITS"::FLOAT END)'
    )


def test_sply_year_direct_aggregate_uses_flag_column_when_provided():
    sql = _render(
        "CALCULATE(SUM('SalesFact'[Units]), SAMEPERIODLASTYEAR('Date'[Date]))",
        anchor_flag_map={("SPLY_YEAR",): "IS_SPLY_YEAR"},
    )
    assert sql == 'SUM(CASE WHEN SALESFACT."IS_SPLY_YEAR" THEN SALESFACT."UNITS"::FLOAT END)'


def test_sply_month_direct_aggregate_dry_run_is_byte_identical_to_pre_fix_behavior():
    sql = _render("CALCULATE(SUM('SalesFact'[Units]), PREVIOUSMONTH('Date'[Date]))")
    assert sql == (
        'SUM(CASE WHEN YEAR(COL_DATE."COL_DATE") = YEAR(DATEADD(MONTH, -1, SALESFACT."MAX_DATE")) '
        'AND MONTH(COL_DATE."COL_DATE") = MONTH(DATEADD(MONTH, -1, SALESFACT."MAX_DATE")) '
        'THEN SALESFACT."UNITS"::FLOAT END)'
    )


def test_sply_month_direct_aggregate_uses_flag_column_when_provided():
    sql = _render(
        "CALCULATE(SUM('SalesFact'[Units]), PREVIOUSMONTH('Date'[Date]))",
        anchor_flag_map={("SPLY_MONTH",): "IS_SPLY_MONTH"},
    )
    assert sql == 'SUM(CASE WHEN SALESFACT."IS_SPLY_MONTH" THEN SALESFACT."UNITS"::FLOAT END)'


# ---------------------------------------------------------------------------
# SAMEPERIODLASTYEAR wrapping a plain measure reference (no existing anchor
# to shift) — _render_lag_period's "inject a new period condition" branch.
# ---------------------------------------------------------------------------

def test_sply_year_wrapping_plain_measure_ref_dry_run_is_byte_identical_to_pre_fix_behavior():
    sql = _render(
        "CALCULATE([TOTAL_UNITS], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={"TOTAL_UNITS": 'SUM(SALESFACT."UNITS"::FLOAT)'},
    )
    assert sql == (
        'SUM(CASE WHEN YEAR(COL_DATE."COL_DATE") = YEAR(SALESFACT."MAX_DATE") - 1 '
        'AND COL_DATE."COL_DATE" BETWEEN DATEADD(YEAR, -1, DATE_TRUNC(\'YEAR\', SALESFACT."MAX_DATE")) '
        'AND DATEADD(YEAR, -1, SALESFACT."MAX_DATE") THEN SALESFACT."UNITS"::FLOAT ELSE NULL END)'
    )


def test_sply_year_wrapping_plain_measure_ref_uses_flag_column_when_provided():
    sql = _render(
        "CALCULATE([TOTAL_UNITS], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={"TOTAL_UNITS": 'SUM(SALESFACT."UNITS"::FLOAT)'},
        anchor_flag_map={("SPLY_YEAR",): "IS_SPLY_YEAR"},
    )
    assert sql == 'SUM(CASE WHEN SALESFACT."IS_SPLY_YEAR" THEN SALESFACT."UNITS"::FLOAT ELSE NULL END)'


# ---------------------------------------------------------------------------
# Nested composition: SAMEPERIODLASTYEAR wrapping a YTD-shaped measure
# reference — the "detect existing anchor and shift" branch (dry-run) vs.
# "detect existing flag reference and swap for the combined shape's flag"
# (flag-map mode). This is the exact real shape of proj-test-1's
# TOTAL_UNITS_YTD_SPLY.
# ---------------------------------------------------------------------------

def test_nested_sply_of_ytd_measure_ref_dry_run_is_byte_identical_to_pre_fix_behavior():
    sql = _render(
        "CALCULATE([TOTAL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={
            "TOTAL_UNITS_YTD": (
                'SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', SALESFACT."MAX_DATE") '
                'AND COL_DATE."COL_DATE" <= SALESFACT."MAX_DATE" THEN SALESFACT."UNITS"::FLOAT END)'
            )
        },
    )
    assert sql == (
        '(SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', DATEADD(YEAR, -1, SALESFACT."MAX_DATE")) '
        'AND COL_DATE."COL_DATE" <= DATEADD(YEAR, -1, SALESFACT."MAX_DATE") THEN SALESFACT."UNITS"::FLOAT END))'
    )


def test_nested_sply_of_ytd_measure_ref_swaps_to_combined_flag_when_provided():
    """The inlined [TOTAL_UNITS_YTD] measure_sql_map entry here is itself
    already flag-rendered (as it would be in a real pipeline run, since
    every metric is rendered through the same flag-aware renderer) --
    confirming the substitution finds the inner IS_YTD reference and swaps
    it for the combined IS_YTD_SPLY_YEAR flag, never touching MAX_DATE."""
    sql = _render(
        "CALCULATE([TOTAL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={
            "TOTAL_UNITS_YTD": 'SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS"::FLOAT END)'
        },
        anchor_flag_map={("YTD",): "IS_YTD", ("YTD", "SPLY_YEAR"): "IS_YTD_SPLY_YEAR"},
    )
    assert sql == '(SUM(CASE WHEN SALESFACT."IS_YTD_SPLY_YEAR" THEN SALESFACT."UNITS"::FLOAT END))'
    assert "MAX_DATE" not in sql
    assert '"IS_YTD"' not in sql  # fully swapped, not left alongside


def test_nested_composition_fails_closed_when_combined_flag_missing():
    """If the discovery pass and enrichment somehow disagree (the combined
    shape wasn't provisioned), rendering must not silently emit a wrong,
    self-contradictory condition (ANDing an unrelated new date filter around
    an already-flag-shaped, opaque inner expression reproduces the exact
    always-empty-CASE bug this whole mechanism exists to avoid) -- it must
    fail closed (None) instead."""
    sql = _render(
        "CALCULATE([TOTAL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={
            "TOTAL_UNITS_YTD": 'SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS"::FLOAT END)'
        },
        anchor_flag_map={("YTD",): "IS_YTD"},  # no ("YTD","SPLY_YEAR") entry
    )
    assert sql is None
