"""Regression tests for Stage 2's rerender_anchor_dependent_metrics()
(connectors/anchor_flag_rerender.py).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from semabridge.connectors.anchor_flag_rerender import rerender_anchor_dependent_metrics
from semabridge.converter import dax_translator as dax_translator_module
from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel, DataType


def _model_with_ytd_and_plain_metric() -> SMLModel:
    return SMLModel(
        unique_name="synthetic_model",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Date", data_type=DataType.DATE),
                ],
            ),
            SMLDataset(
                unique_name="Date",
                source_table="DATE_DIM",
                columns=[SMLColumn(unique_name="Date", data_type=DataType.DATE)],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Units_YTD",
                dataset="SalesFact",
                expression="TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
                sql_expression=(
                    'SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', CURRENT_DATE()) '
                    'AND COL_DATE."COL_DATE" <= CURRENT_DATE() THEN SALESFACT."UNITS"::FLOAT END)'
                ),
            ),
            SMLMetric(
                unique_name="Total_Units",
                dataset="SalesFact",
                expression="SUM('SalesFact'[Units])",
                sql_expression='SUM(SALESFACT."UNITS")',
            ),
        ],
    )


def _dataset_lookup_and_aliases(model: SMLModel):
    dataset_col_lookup = {
        ds.unique_name: {c.unique_name.upper() for c in ds.columns} for ds in model.datasets
    }
    dataset_aliases = {ds.unique_name: ds.unique_name.upper() for ds in model.datasets}
    return dataset_col_lookup, dataset_aliases


def test_no_anchor_flag_map_leaves_shaped_metric_at_current_date_fallback():
    model = _model_with_ytd_and_plain_metric()
    original_sql = model.metrics[0].sql_expression
    dataset_col_lookup, dataset_aliases = _dataset_lookup_and_aliases(model)

    count = rerender_anchor_dependent_metrics(model, {}, dataset_col_lookup, dataset_aliases)

    assert count == 0
    assert model.metrics[0].sql_expression == original_sql


def test_flag_present_upgrades_shaped_metric_to_flag_column_reference():
    model = _model_with_ytd_and_plain_metric()
    dataset_col_lookup, dataset_aliases = _dataset_lookup_and_aliases(model)
    anchor_flag_map = {"salesfact": {("YTD",): "IS_YTD"}}

    count = rerender_anchor_dependent_metrics(
        model, anchor_flag_map, dataset_col_lookup, dataset_aliases, label="predicted"
    )

    assert count == 1
    ytd_metric = next(m for m in model.metrics if m.unique_name == "Total_Units_YTD")
    assert 'SALESFACT."IS_YTD"' in ytd_metric.sql_expression
    assert "MAX_DATE" not in ytd_metric.sql_expression
    assert "CURRENT_DATE" not in ytd_metric.sql_expression


def test_unshaped_metric_never_retranslated_even_with_flag_map_present():
    """The plain SUM metric has no time-intelligence shape at all -- it
    must not be touched, regardless of what anchor_flag_map contains."""
    model = _model_with_ytd_and_plain_metric()
    dataset_col_lookup, dataset_aliases = _dataset_lookup_and_aliases(model)
    anchor_flag_map = {"salesfact": {("YTD",): "IS_YTD"}}
    plain_metric = next(m for m in model.metrics if m.unique_name == "Total_Units")
    original_sql = plain_metric.sql_expression

    rerender_anchor_dependent_metrics(model, anchor_flag_map, dataset_col_lookup, dataset_aliases)

    assert plain_metric.sql_expression == original_sql


def test_divergence_downgrades_metric_back_to_current_date_fallback():
    """The Step 9 (confirmed) safety-net case: a metric was previously
    upgraded to a flag-column reference by a predicted pass, but the
    confirmed map has NO entry for its fact table (enrichment didn't
    actually establish an anchor for real) -- retranslating with the
    now-empty per-dataset map must downgrade it back to the safe
    CURRENT_DATE() fallback, not leave a dangling reference to a flag
    column that will never exist."""
    model = _model_with_ytd_and_plain_metric()
    dataset_col_lookup, dataset_aliases = _dataset_lookup_and_aliases(model)
    ytd_metric = next(m for m in model.metrics if m.unique_name == "Total_Units_YTD")

    # Simulate: a predicted pass already wrongly upgraded this metric.
    ytd_metric.sql_expression = 'SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS"::FLOAT END)'

    # Confirmed pass: real enrichment never established this fact table's
    # anchor (e.g. cursor error) -- no entry for "salesfact" at all.
    count = rerender_anchor_dependent_metrics(
        model, {}, dataset_col_lookup, dataset_aliases, label="confirmed"
    )

    assert count == 1
    assert 'SALESFACT."IS_YTD"' not in ytd_metric.sql_expression
    assert "CURRENT_DATE()" in ytd_metric.sql_expression
    assert "MAX_DATE" not in ytd_metric.sql_expression


def test_one_dax_translator_instance_reused_for_the_whole_pass(monkeypatch):
    model = SMLModel(
        unique_name="synthetic_model_multi",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Date", data_type=DataType.DATE),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Units_YTD",
                dataset="SalesFact",
                expression="TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
            ),
            SMLMetric(
                unique_name="Revenue_YTD",
                dataset="SalesFact",
                expression="TOTALYTD(SUM('SalesFact'[Revenue]), 'Date'[Date])",
            ),
        ],
    )
    dataset_col_lookup, dataset_aliases = _dataset_lookup_and_aliases(model)
    anchor_flag_map = {"salesfact": {("YTD",): "IS_YTD"}}

    construction_count = {"n": 0}
    real_init = dax_translator_module.DAXTranslator.__init__

    def _counting_init(self, *a, **k):
        construction_count["n"] += 1
        return real_init(self, *a, **k)

    monkeypatch.setattr(dax_translator_module.DAXTranslator, "__init__", _counting_init)

    count = rerender_anchor_dependent_metrics(model, anchor_flag_map, dataset_col_lookup, dataset_aliases)

    assert count == 2
    assert construction_count["n"] == 1, (
        "one DAXTranslator() instance must be reused for the whole pass, "
        "not constructed once per metric"
    )


def test_never_invokes_tier5_llm_service(monkeypatch):
    """skip_tier5=True must be honored unconditionally -- this pass never
    makes an LLM call, regardless of how many shaped metrics it processes."""
    model = _model_with_ytd_and_plain_metric()
    dataset_col_lookup, dataset_aliases = _dataset_lookup_and_aliases(model)
    anchor_flag_map = {"salesfact": {("YTD",): "IS_YTD"}}

    def _explode(*a, **k):
        raise AssertionError("Tier5Service must never be constructed by this pass")

    monkeypatch.setattr(
        dax_translator_module.DAXTranslator, "_get_tier5_service", _explode
    )

    # Must not raise -- if Tier 5 were ever reached, _explode above would fire.
    rerender_anchor_dependent_metrics(model, anchor_flag_map, dataset_col_lookup, dataset_aliases)


def test_never_calls_batch_translate_tier5(monkeypatch):
    """Direct confirmation of the "no interaction with the existing
    Tier-5 batching fixes in osi_to_sml.py/tmsl_to_sml.py" requirement:
    this pass calls DAXTranslator.translate() per-metric with
    skip_tier5=True, never DAXTranslator.batch_translate_tier5() (the
    method osi_to_sml.py/tmsl_to_sml.py's own Step 6 batching calls) --
    so it can never overlap with or duplicate that batching work, by
    construction. If this pass ever called it, this test would fail."""
    model = _model_with_ytd_and_plain_metric()
    dataset_col_lookup, dataset_aliases = _dataset_lookup_and_aliases(model)
    anchor_flag_map = {"salesfact": {("YTD",): "IS_YTD"}}

    def _explode(*a, **k):
        raise AssertionError(
            "rerender_anchor_dependent_metrics must never call batch_translate_tier5 "
            "-- that is osi_to_sml.py's/tmsl_to_sml.py's own Step 6 batching entry point"
        )

    monkeypatch.setattr(dax_translator_module.DAXTranslator, "batch_translate_tier5", _explode)

    count = rerender_anchor_dependent_metrics(model, anchor_flag_map, dataset_col_lookup, dataset_aliases)
    assert count == 1


def test_no_shaped_metrics_returns_zero_without_constructing_translator(monkeypatch):
    """Models with zero time-intelligence metrics (the vast majority) must
    be a complete no-op -- not even a DAXTranslator() gets constructed."""
    model = SMLModel(
        unique_name="synthetic_model_no_shapes",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[SMLColumn(unique_name="Units", data_type=DataType.DECIMAL)],
            )
        ],
        metrics=[
            SMLMetric(unique_name="Total_Units", dataset="SalesFact", expression="SUM('SalesFact'[Units])")
        ],
    )
    dataset_col_lookup, dataset_aliases = _dataset_lookup_and_aliases(model)

    def _explode(*a, **k):
        raise AssertionError("DAXTranslator must never be constructed for a shape-less model")

    monkeypatch.setattr(dax_translator_module.DAXTranslator, "__init__", _explode)

    count = rerender_anchor_dependent_metrics(
        model, {"salesfact": {("YTD",): "IS_YTD"}}, dataset_col_lookup, dataset_aliases
    )
    assert count == 0
