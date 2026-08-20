"""Regression test for a real customer bug (proj-dem): a lag-period
measure (SAMEPERIODLASTYEAR wrapping another measure reference) silently
and PERMANENTLY resolved to a same-period reference to its own base
measure, on the very first, context-less translation attempt -- before the
pipeline ever had a chance to resolve it properly.

Real case: TOTAL_UNITS_YTD_SPLY = CALCULATE([TOTAL_UNITS_YTD],
SAMEPERIODLASTYEAR('Date'[Date])). osi_to_sml.py's very first per-metric
translate() call (before Step 3c/3d's multi-pass, full-model-context
resolution) passes no metrics_context at all. With no measure registry,
[TOTAL_UNITS_YTD] inside the lag-period wrapper can never be resolved, so
dax_ast_parser.py's "unshifted fallback" (allow_unshifted_fallback=True)
unconditionally fired and shipped a same-period bare column reference
(SALESFACT."TOTAL_UNITS_YTD") as TOTAL_UNITS_YTD_SPLY's "resolved" SQL.
Because osi_to_sml.py's _resolve_metric_dependencies treats any already-set
metric.sql_expression as done (`if metric.sql_expression: continue`), this
wrong, premature result was never revisited even once full model context
became available -- and it poisoned every downstream metric that referenced
it (TOTAL_UNITS_YTD_VAR = [TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY] silently
became TOTAL_UNITS_YTD - TOTAL_UNITS_YTD, i.e. always zero), while also
producing a shape (one inlined raw aggregate minus one bare metric
citation) Snowflake's semantic view validator rejects with error 010218.

The fix (dax_translator.py's Tier 3 block): gate allow_unshifted_fallback
on `bool(metrics_context)` instead of always True. Without any measure
registry, deferring costs nothing -- the metric simply isn't resolved yet,
exactly like any other dependency-gated metric, and gets a fair shot once
_resolve_metric_dependencies calls translate() again with the full model.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.converter.dax_translator import DAXTranslator


def _metric(unique_name, dataset, expression):
    return SimpleNamespace(
        unique_name=unique_name, dataset=dataset, expression=expression, sql_expression=None,
    )


def test_lag_period_of_unresolved_measure_defers_instead_of_faking_same_period_sql():
    """The exact first-pass shape: no metrics_context at all. Must NOT
    silently succeed with a same-period placeholder -- must defer (fail
    this attempt) so a later, full-context pass gets a real shot."""
    t = DAXTranslator()
    dax = "CALCULATE([TOTAL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))"

    result = t.translate(dax, "SALESFACT", "SalesFact", metric_name="TOTAL_UNITS_YTD_SPLY", skip_tier5=True)

    assert not result.is_success


def test_lag_period_of_unresolved_measure_still_falls_back_when_metrics_context_present_but_genuinely_unshiftable():
    """Positive control: allow_unshifted_fallback must still fire when
    metrics_context IS present but the referenced measure genuinely can't be
    date-shifted by this renderer (e.g. it isn't in the model at all) --
    this preserves the original safety-net behavior, just no longer
    triggered by the absence of any context whatsoever."""
    t = DAXTranslator()
    metrics = [_metric("TOTAL_UNITS_YTD_SPLY", "SalesFact",
                        "CALCULATE([NOT_A_REAL_MEASURE], SAMEPERIODLASTYEAR('Date'[Date]))")]
    dax = metrics[0].expression

    result = t.translate(dax, "SALESFACT", "SalesFact", metric_name="TOTAL_UNITS_YTD_SPLY",
                          metrics_context=metrics, skip_tier5=True)

    assert result.is_success
    assert 'SALESFACT."NOT_A_REAL_MEASURE"' in result.sql.upper() or "NOT_A_REAL_MEASURE" in result.sql.upper()


def test_full_pipeline_two_stage_resolution_produces_correctly_shifted_sply_not_a_self_reference():
    """End-to-end reproduction of the real bug across both pipeline stages:
    osi_to_sml.py's first, no-context per-metric pass, followed by
    _resolve_metric_dependencies's multi-pass full-context resolution.

    Before the fix: TOTAL_UNITS_YTD_SPLY resolved (wrongly, on stage 1) to
    a same-period reference to TOTAL_UNITS_YTD, and TOTAL_UNITS_YTD_VAR
    became TOTAL_UNITS_YTD - TOTAL_UNITS_YTD (always zero) with a Snowflake-
    invalid mixed shape. After the fix: TOTAL_UNITS_YTD_SPLY correctly
    resolves in stage 2 with real DATEADD-shifted SQL, and TOTAL_UNITS_YTD_VAR
    is a clean SUM(...) - SUM(...) variance.
    """
    t = DAXTranslator()
    metrics = [
        _metric("TOTAL_UNITS", "SalesFact", "SUM('SalesFact'[Units])"),
        _metric("TOTAL_UNITS_YTD", "SalesFact", "TOTALYTD([TOTAL_UNITS], 'Date'[Date])"),
        _metric("TOTAL_UNITS_YTD_SPLY", "SalesFact",
                "CALCULATE([TOTAL_UNITS_YTD], SAMEPERIODLASTYEAR('Date'[Date]))"),
        _metric("TOTAL_UNITS_YTD_VAR", "SalesFact", "[TOTAL_UNITS_YTD]-[TOTAL_UNITS_YTD_SPLY]"),
    ]

    # Stage 1: osi_to_sml.py's initial per-metric pass -- no metrics_context.
    for metric in metrics:
        result = t.translate(metric.expression, "SALESFACT", metric.dataset,
                              metric_name=metric.unique_name, skip_tier5=True)
        if result.is_success:
            metric.sql_expression = result.sql

    # None of these should have resolved yet -- TOTAL_UNITS depends on
    # nothing and Tier 1 handles it directly without metrics_context, but
    # the time-intelligence ones all need real dependency context.
    by_name = {m.unique_name: m for m in metrics}
    assert by_name["TOTAL_UNITS_YTD_SPLY"].sql_expression is None
    assert by_name["TOTAL_UNITS_YTD_VAR"].sql_expression is None

    # Stage 2: _resolve_metric_dependencies's multi-pass, full-context loop.
    for _pass in range(3):
        for metric in metrics:
            if metric.sql_expression:
                continue
            result = t.translate(metric.expression, "SALESFACT", metric.dataset,
                                  metric_name=metric.unique_name, metrics_context=metrics, skip_tier5=True)
            if result.is_success:
                metric.sql_expression = result.sql

    sply_sql = by_name["TOTAL_UNITS_YTD_SPLY"].sql_expression
    var_sql = by_name["TOTAL_UNITS_YTD_VAR"].sql_expression
    assert sply_sql is not None
    assert var_sql is not None

    # Must be a REAL, shifted prior-year computation -- not a same-period
    # bare reference back to TOTAL_UNITS_YTD.
    assert "DATEADD" in sply_sql.upper()
    assert sply_sql.upper() != 'SALESFACT."TOTAL_UNITS_YTD"'

    # TOTAL_UNITS_YTD_VAR must combine two real aggregates, never cite
    # TOTAL_UNITS_YTD a second time (the self-subtraction bug).
    assert var_sql.upper().count('SALESFACT."TOTAL_UNITS_YTD"') == 0
    assert var_sql.upper().count("SUM(") >= 2
