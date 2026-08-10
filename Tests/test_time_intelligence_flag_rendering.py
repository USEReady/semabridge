"""Regression tests for Part 2, Step 3 of the MAX_DATE-capability-limit fix:
DaxSqlRenderer's TOTALYTD/TOTALMTD/TOTALQTD (_render_period_to_date) and
SAMEPERIODLASTYEAR/PREVIOUSxxx (_render_lag_period) rendering, with and
without an anchor_flag_map (see converter/time_intelligence_shapes.py).

Three properties must all hold:
  (a) with a flag map, the shape being rendered references its precomputed
      flag column, never MAX_DATE or CURRENT_DATE();
  (b) with no flag map (the schema-blind dry-run/preview path, AND any real
      deploy where enrichment couldn't establish this fact table's anchor),
      output anchors to CURRENT_DATE() -- a native Snowflake function --
      never a raw reference to the enriched-view-only MAX_DATE column.
      MAX_DATE only exists once _create_enriched_view has actually run for
      this fact table; referencing it unconditionally here was exactly as
      enrichment-dependent as the flag-column path, just via an uglier
      column name that fails instead of gracefully degrading. Previously
      (before this fix) this file pinned the OPPOSITE behavior -- output
      byte-identical to the raw MAX_DATE reference -- as intentional,
      reasoning that "no flag map" only ever meant dry-run/preview. That
      reasoning had a gap: Stage 1/2/3 of the anchor_flag_map wiring fix
      (predict_anchor_flag_map / rerender_anchor_dependent_metrics) not
      existing yet means every REAL deploy also hits this branch today,
      unconditionally -- so the old pinned behavior was breaking real
      metrics (see TOTAL_UNITS_YTD's class of bug), not just previewing
      them. CURRENT_DATE() matches the fallback already established and
      tested in dax_rule_translator.py's
      translate_time_intelligence_with_anchors and
      connectors/translator.py's parallel implementation (commit e4c8322),
      which this file's copy had never been synced to;
  (c) the two independent implementations (this AST renderer, and
      dax_rule_translator.py's regex-based rule engine) anchor identically
      for the same DAX shape -- so a caller can't get a different answer
      depending on which translation tier happened to handle a given
      metric.
"""
from __future__ import annotations

import re

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
from semabridge.converter.dax_rule_translator import translate_time_intelligence_with_anchors


def _render(dax: str, measure_sql_map=None, anchor_flag_map=None, table_alias="SALESFACT", date_alias="COL_DATE"):
    ast = DaxAstParser().parse(dax)
    renderer = DaxSqlRenderer(
        table_alias=table_alias,
        date_alias=date_alias,
        measure_sql_map=measure_sql_map or {},
        anchor_flag_map=anchor_flag_map,
    )
    return renderer.render(ast)


def _case_when_condition(sql: str) -> str:
    """Extract the boolean condition between 'CASE WHEN' and 'THEN' -- the
    part that actually encodes the anchor, isolated from each
    implementation's own (unrelated, pre-existing) wrapper differences
    like ELSE-clause presence or numeric casts."""
    match = re.search(r"CASE WHEN (.+?) THEN", sql)
    assert match, f"no CASE WHEN found in: {sql}"
    return match.group(1)


# ---------------------------------------------------------------------------
# TOTALYTD (direct aggregate) — _render_period_to_date
# ---------------------------------------------------------------------------

def test_totalytd_direct_aggregate_with_no_flag_map_uses_current_date_not_max_date():
    sql = _render("TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])")
    assert sql == (
        'SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', CURRENT_DATE()) '
        'AND COL_DATE."COL_DATE" <= CURRENT_DATE() THEN SALESFACT."UNITS"::FLOAT END)'
    )
    assert "MAX_DATE" not in sql


def test_totalytd_direct_aggregate_uses_flag_column_when_provided():
    sql = _render(
        "TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
        anchor_flag_map={("YTD",): "IS_YTD"},
    )
    assert sql == 'SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS"::FLOAT END)'
    assert "MAX_DATE" not in sql
    assert "CURRENT_DATE" not in sql


def test_totalytd_direct_aggregate_anchor_matches_dax_rule_translator_exactly():
    """The two independent implementations must anchor identically (after
    normalizing the one pre-existing, unrelated difference between them:
    date-column qualification style -- COL_DATE."COL_DATE" here vs. a bare
    "COL_DATE" in the regex-based rule engine, which has no table-alias
    context for the date dimension)."""
    dax = "TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])"
    ast_condition = _case_when_condition(_render(dax))
    rule_condition = _case_when_condition(translate_time_intelligence_with_anchors(dax, "SALESFACT"))
    assert ast_condition.replace('COL_DATE."COL_DATE"', '"COL_DATE"') == rule_condition


# ---------------------------------------------------------------------------
# TOTALYTD (measure-reference fallback) — _render_period_to_date
# ---------------------------------------------------------------------------

def test_totalytd_measure_ref_with_no_flag_map_uses_current_date_not_max_date():
    sql = _render(
        "TOTALYTD([TOTAL_UNITS], 'Date'[Date])",
        measure_sql_map={"TOTAL_UNITS": 'SUM(SALESFACT."UNITS"::FLOAT)'},
    )
    assert sql == (
        'SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', CURRENT_DATE()) '
        'AND COL_DATE."COL_DATE" <= CURRENT_DATE() THEN SALESFACT."UNITS"::FLOAT ELSE NULL END)'
    )
    assert "MAX_DATE" not in sql


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

def test_sply_year_direct_aggregate_with_no_flag_map_uses_current_date_not_max_date():
    sql = _render("CALCULATE(SUM('SalesFact'[Units]), SAMEPERIODLASTYEAR('Date'[Date]))")
    assert sql == (
        'SUM(CASE WHEN YEAR(COL_DATE."COL_DATE") = YEAR(CURRENT_DATE()) - 1 '
        'AND COL_DATE."COL_DATE" BETWEEN DATEADD(YEAR, -1, DATE_TRUNC(\'YEAR\', CURRENT_DATE())) '
        'AND DATEADD(YEAR, -1, CURRENT_DATE()) THEN SALESFACT."UNITS"::FLOAT END)'
    )
    assert "MAX_DATE" not in sql


def test_sply_year_direct_aggregate_uses_flag_column_when_provided():
    sql = _render(
        "CALCULATE(SUM('SalesFact'[Units]), SAMEPERIODLASTYEAR('Date'[Date]))",
        anchor_flag_map={("SPLY_YEAR",): "IS_SPLY_YEAR"},
    )
    assert sql == 'SUM(CASE WHEN SALESFACT."IS_SPLY_YEAR" THEN SALESFACT."UNITS"::FLOAT END)'


def test_sply_month_direct_aggregate_with_no_flag_map_uses_current_date_not_max_date():
    sql = _render("CALCULATE(SUM('SalesFact'[Units]), PREVIOUSMONTH('Date'[Date]))")
    assert sql == (
        'SUM(CASE WHEN YEAR(COL_DATE."COL_DATE") = YEAR(DATEADD(MONTH, -1, CURRENT_DATE())) '
        'AND MONTH(COL_DATE."COL_DATE") = MONTH(DATEADD(MONTH, -1, CURRENT_DATE())) '
        'THEN SALESFACT."UNITS"::FLOAT END)'
    )
    assert "MAX_DATE" not in sql


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

def test_sply_year_wrapping_plain_measure_ref_with_no_flag_map_uses_current_date_not_max_date():
    sql = _render(
        "CALCULATE([TOTAL_UNITS], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={"TOTAL_UNITS": 'SUM(SALESFACT."UNITS"::FLOAT)'},
    )
    assert sql == (
        'SUM(CASE WHEN YEAR(COL_DATE."COL_DATE") = YEAR(CURRENT_DATE()) - 1 '
        'AND COL_DATE."COL_DATE" BETWEEN DATEADD(YEAR, -1, DATE_TRUNC(\'YEAR\', CURRENT_DATE())) '
        'AND DATEADD(YEAR, -1, CURRENT_DATE()) THEN SALESFACT."UNITS"::FLOAT ELSE NULL END)'
    )
    assert "MAX_DATE" not in sql


def test_sply_year_wrapping_plain_measure_ref_uses_flag_column_when_provided():
    sql = _render(
        "CALCULATE([TOTAL_UNITS], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={"TOTAL_UNITS": 'SUM(SALESFACT."UNITS"::FLOAT)'},
        anchor_flag_map={("SPLY_YEAR",): "IS_SPLY_YEAR"},
    )
    assert sql == 'SUM(CASE WHEN SALESFACT."IS_SPLY_YEAR" THEN SALESFACT."UNITS"::FLOAT ELSE NULL END)'


# ---------------------------------------------------------------------------
# Nested composition: SAMEPERIODLASTYEAR wrapping a YTD-shaped measure
# reference — the "detect existing anchor and shift" branch (no flag map)
# vs. "detect existing flag reference and swap for the combined shape's
# flag" (flag-map mode). This is the exact real shape of proj-test-1's
# TOTAL_UNITS_YTD_SPLY.
# ---------------------------------------------------------------------------

def test_nested_sply_of_ytd_measure_ref_with_no_flag_map_shifts_current_date_anchor():
    """The inlined [TOTAL_UNITS_YTD] measure_sql_map entry here is itself
    already rendered by this same, current fallback (CURRENT_DATE()-
    anchored) -- as it would be in a real pipeline run, since every metric
    in a single conversion pass is translated by the same code. Confirms
    the detect-and-shift mechanism still finds and shifts the inner
    anchor when it's CURRENT_DATE()-based rather than the old raw
    MAX_DATE reference."""
    sql = _render(
        "CALCULATE([TOTAL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={
            "TOTAL_UNITS_YTD": (
                'SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', CURRENT_DATE()) '
                'AND COL_DATE."COL_DATE" <= CURRENT_DATE() THEN SALESFACT."UNITS"::FLOAT END)'
            )
        },
    )
    assert sql == (
        '(SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', DATEADD(YEAR, -1, CURRENT_DATE())) '
        'AND COL_DATE."COL_DATE" <= DATEADD(YEAR, -1, CURRENT_DATE()) THEN SALESFACT."UNITS"::FLOAT END))'
    )
    assert "MAX_DATE" not in sql


def test_nested_sply_of_ytd_measure_ref_shifts_legacy_max_date_anchor_too():
    """Defense-in-depth: if the inner [TOTAL_UNITS_YTD] measure_sql_map
    entry is a STALE sql_expression persisted from before this fix (still
    raw-MAX_DATE-anchored -- e.g. a mixed-generation scenario, not
    currently reachable via the normal fresh-sync or rollback pipelines,
    but cheap to guard against), the detect-and-shift mechanism must still
    find and shift that legacy anchor, rather than treating the inner
    expression as anchor-less and ANDing a second, unshifted condition
    around it -- the exact self-contradictory, always-empty-CASE bug this
    whole mechanism exists to avoid, just reachable via a generation
    mismatch instead of a missing flag."""
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
    it for the combined IS_YTD_SPLY_YEAR flag, never touching MAX_DATE or
    CURRENT_DATE()."""
    sql = _render(
        "CALCULATE([TOTAL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
        measure_sql_map={
            "TOTAL_UNITS_YTD": 'SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS"::FLOAT END)'
        },
        anchor_flag_map={("YTD",): "IS_YTD", ("YTD", "SPLY_YEAR"): "IS_YTD_SPLY_YEAR"},
    )
    assert sql == '(SUM(CASE WHEN SALESFACT."IS_YTD_SPLY_YEAR" THEN SALESFACT."UNITS"::FLOAT END))'
    assert "MAX_DATE" not in sql
    assert "CURRENT_DATE" not in sql
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
