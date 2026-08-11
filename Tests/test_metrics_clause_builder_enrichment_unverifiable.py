"""Regression test for the real incident: dry-run's DDL-emission
schema-validation step hard-rejected an already-correctly-translated
flag-column metric (Total Units YTD -> SUM(CASE WHEN SALESFACT.IS_YTD
THEN SALESFACT.UNITS ELSE NULL END)) with "Column 'IS_YTD' not found in
dataset 'SalesFact'" -- because IS_YTD only exists on the enriched view
_create_enriched_view builds at real-deploy time, and dry-run's trial
DDL-build pass has no live connection to see it.

Uses the REAL MetricExpressionTranslator (not a stub) so
_validate_metric_column_references genuinely fails on IS_YTD, exactly
reproducing the real failure this fix closes -- not a mocked-away
version of it.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.converter.time_intelligence_shapes import (
    ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE,
)
from semabridge.utils.identifiers import IdentifierSanitizer


class DummySanitizer:
    def format_physical_column_ref(self, alias: str, col: str, model_name=None):
        return f'{alias}."{col}"'


def _builder():
    return MetricsClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=None,
        sanitizer=DummySanitizer(),
        translator=MetricExpressionTranslator(identifier_sanitizer=IdentifierSanitizer()),
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
    )


def _model(metrics):
    return SimpleNamespace(metrics=metrics, unique_name="model", label="model")


def _flag_column_metric(**overrides):
    defaults = dict(
        unique_name="Total Units YTD",
        dataset="SalesFact",
        expression="TOTALYTD([Total Units], 'Date'[Date])",
        sql_expression='SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS" ELSE NULL END)',
        source_column=None,
        aggregation=None,
        sync_enabled=True,
        sync_failure_reason=None,
        advisory_notes=[],
        advisory_categories=[],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# dataset_col_lookup deliberately mirrors the real incident exactly: the
# model's own declared SalesFact columns, with no IS_YTD (that column only
# gets created by _create_enriched_view at real-deploy time).
_DATASET_COL_LOOKUP = {"SalesFact": {"UNITS", "COL_DATE"}}
_DATASET_ALIASES = {"SalesFact": "SALESFACT"}
_DATASET_BY_NAME = {"SalesFact": SimpleNamespace(is_fact=True)}


def test_flag_column_metric_is_emitted_not_dropped():
    """The core fix: the metric must still appear in the emitted METRICS
    clause lines, with its real SQL intact -- not silently discarded."""
    metric = _flag_column_metric()
    builder = _builder()

    lines = builder.build_for_sml(
        _model([metric]),
        dataset_aliases=_DATASET_ALIASES,
        dataset_by_name=_DATASET_BY_NAME,
        dataset_col_lookup=_DATASET_COL_LOOKUP,
        alias_by_raw={},
        all_physical_col_names={"UNITS", "COL_DATE"},
        emittable_metric_name_set=set(),
    )

    assert any("IS_YTD" in line for line in lines), lines
    assert builder.drop_ledger.to_json() == []  # no hard DDL-emission drop recorded


def test_flag_column_metric_gets_the_honest_advisory_category_and_note():
    metric = _flag_column_metric()
    builder = _builder()

    builder.build_for_sml(
        _model([metric]),
        dataset_aliases=_DATASET_ALIASES,
        dataset_by_name=_DATASET_BY_NAME,
        dataset_col_lookup=_DATASET_COL_LOOKUP,
        alias_by_raw={},
        all_physical_col_names={"UNITS", "COL_DATE"},
        emittable_metric_name_set=set(),
    )

    assert metric.advisory_categories == [ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE]
    assert len(metric.advisory_notes) == 1
    assert "Cannot verify in dry-run" in metric.advisory_notes[0]
    assert "IS_YTD" in metric.advisory_notes[0]
    assert "SalesFact" in metric.advisory_notes[0]


def test_metric_with_a_genuinely_unknown_column_is_still_hard_dropped():
    """Negative control -- the fix must be selective, not a blanket
    'never fail schema validation' downgrade. A metric referencing a
    column that ISN'T a predicted enrichment flag column (no
    time-intelligence shape at all here) must still be dropped exactly
    as before."""
    metric = _flag_column_metric(
        unique_name="Bogus Metric",
        expression="SUM([Units])",  # no time-intelligence shape at all
        sql_expression='SUM(SALESFACT."TOTALLY_MADE_UP_COLUMN")',
    )
    builder = _builder()

    lines = builder.build_for_sml(
        _model([metric]),
        dataset_aliases=_DATASET_ALIASES,
        dataset_by_name=_DATASET_BY_NAME,
        dataset_col_lookup=_DATASET_COL_LOOKUP,
        alias_by_raw={},
        all_physical_col_names={"UNITS", "COL_DATE"},
        emittable_metric_name_set=set(),
    )

    assert not any("TOTALLY_MADE_UP_COLUMN" in line for line in lines)
    dropped = builder.drop_ledger.to_json()
    assert len(dropped) == 1
    assert dropped[0]["entity_name"] == "Bogus Metric"
    assert metric.advisory_categories == []  # no false-positive advisory tagging


def test_metric_whose_shape_predicts_a_different_flag_column_is_still_hard_dropped():
    """Negative control, narrower: the metric DOES have a resolved
    time-intelligence shape (YTD), but its SQL references a column that
    is NOT that shape's predicted flag column -- a genuinely unrelated
    missing column must still hard-fail, not be waved through just
    because the metric happens to be time-intelligence-shaped at all."""
    metric = _flag_column_metric(
        sql_expression='SUM(CASE WHEN SALESFACT."SOME_OTHER_UNKNOWN_COLUMN" THEN SALESFACT."UNITS" ELSE NULL END)',
    )
    builder = _builder()

    builder.build_for_sml(
        _model([metric]),
        dataset_aliases=_DATASET_ALIASES,
        dataset_by_name=_DATASET_BY_NAME,
        dataset_col_lookup=_DATASET_COL_LOOKUP,
        alias_by_raw={},
        all_physical_col_names={"UNITS", "COL_DATE"},
        emittable_metric_name_set=set(),
    )

    dropped = builder.drop_ledger.to_json()
    assert len(dropped) == 1
    assert metric.advisory_categories == []
