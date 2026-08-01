"""Hard regression guarantee: for any DAX expression Tiers 1-4 (or, on
Pipeline B, the separate rule engine) can resolve, Tier 5
(DaxTranslationService / Tier5Service) must never be invoked at all.

This is deliberately not an assertion on the deterministic tier's output —
it is a spy on Tier5Service.translate itself, wired to raise if called, so
a future change that accidentally routes an already-resolvable expression
through the LLM path fails these tests immediately rather than relying on
this being an emergent property of call order.

semabridge.dax_translation.service.DaxTranslationService.translate_metric
itself already has direct coverage for this
(Tests/dax_translation/test_service.py::test_tier1_result_returned_without_reaching_tier5).
The three tests below cover the three real pipeline call sites that sit on
top of it or call Tier5Service directly, one per migrated pipeline:

  - Pipeline A: converter/dax_translator.py -> DAXTranslator.translate()
  - Pipeline B: connectors/translator.py -> MetricExpressionTranslator._try_llm_metric_fallback_expression
  - Pipeline C: connectors/databricks_publisher.py -> DatabricksPublisher._resolve_measure_sql
"""
from __future__ import annotations

import pytest

from semabridge.dax_translation.tier5.service import Tier5Service


def _fail_if_tier5_called(*_args, **_kwargs):
    raise AssertionError(
        "Tier5Service.translate was called for an expression Tiers 1-4 "
        "should have already resolved — the tier-ordering invariant is broken."
    )


@pytest.fixture
def tier5_translate_explodes(monkeypatch):
    """Patches Tier5Service at the class level so it explodes regardless of
    where/how an instance is constructed (all three call sites build a
    fresh Tier5Service()/DaxTranslationService() per call rather than
    reusing a stored instance)."""
    monkeypatch.setattr(Tier5Service, "translate", _fail_if_tier5_called)


def test_pipeline_a_dax_translator_never_reaches_tier5_for_resolvable_expression(
    tier5_translate_explodes,
):
    from semabridge.converter.dax_translator import DAXTranslator

    translator = DAXTranslator()
    result = translator.translate(
        "SUM('SalesFact'[Units])",
        table_alias="salesfact",
        dataset_name="SalesFact",
    )

    assert result.is_success is True
    assert result.tier in (1, 2, 3, 4)
    assert result.sql is not None


def test_pipeline_b_metric_translator_never_reaches_tier5_for_resolvable_expression(
    tier5_translate_explodes,
):
    from semabridge.connectors.translator import MetricExpressionTranslator
    from semabridge.utils.identifiers import IdentifierSanitizer

    class _Metric:
        expression = "SUM('SalesFact'[Units])"
        unique_name = "TotalUnits"
        dataset = "SalesFact"

    translator = MetricExpressionTranslator(
        identifier_sanitizer=IdentifierSanitizer(force_uppercase=True)
    )

    # This particular expression happens to resolve via the separate rule
    # engine (dax_rule_translator's own direct_agg pattern) before
    # DaxTranslationService is even reached — which is itself a valid
    # instance of the invariant (something upstream of Tier 5 resolved it,
    # so Tier 5 is never called). DaxTranslationService's own Tier 1-4 gate
    # is covered directly and unconditionally by
    # test_service.py::test_tier1_result_returned_without_reaching_tier5.
    sql = translator._try_llm_metric_fallback_expression(
        metric=_Metric(),
        metric_name="TotalUnits",
        table_alias="SALESFACT",
        alias_by_raw={},
        dataset_col_lookup={"SalesFact": {"UNITS"}},
        dataset_aliases={"SalesFact": "SALESFACT"},
        metric_name_set={"TotalUnits"},
        all_physical_col_names=set(),
        emittable_metric_name_set=set(),
        skipped_metric_names=set(),
    )

    assert sql is not None
    assert "UNITS" in sql.upper()


def test_pipeline_c_databricks_publisher_never_reaches_tier5_for_resolvable_expression(
    tier5_translate_explodes,
):
    from semabridge.connectors.databricks_publisher import DatabricksPublisher
    from semabridge.core.settings import DatabricksConfig
    from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, DataType

    cfg = DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )
    publisher = DatabricksPublisher(cfg)

    dataset = SMLDataset(
        unique_name="Sales",
        label="Sales",
        columns=[SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL)],
    )
    metric = SMLMetric(
        unique_name="TotalRevenue",
        dataset="Sales",
        expression="SUM('Sales'[Revenue])",
    )
    sml_model = SMLModel(
        unique_name="test_model", label="Test", datasets=[dataset], metrics=[metric]
    )

    from semabridge.connectors.databricks_publisher import TRANSLATION_TYPE_DAX_LLM

    sql_expr, translation_type = publisher._resolve_measure_sql(metric, dataset, sml_model=sml_model)

    assert sql_expr is not None
    assert translation_type != TRANSLATION_TYPE_DAX_LLM
