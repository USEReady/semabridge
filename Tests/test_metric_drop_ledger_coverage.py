"""Regression tests for the previously-silent metric drop points in
MetricsClauseBuilder — each of these used to remove a metric line with no
DropLedger record at all, which is exactly what
semabridge.core.reconciliation's "unaccounted" bucket is designed to catch.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.utils.identifiers import IdentifierSanitizer


class DummySanitizer:
    def format_physical_column_ref(self, alias: str, col: str, model_name=None):
        return f'{alias}."{col}"'


class DummyTranslator:
    def prefetch_openai_metric_translations(self, **kwargs):
        pass

    def _try_llm_metric_fallback_expression(self, **kwargs):
        return None

    def _try_basic_dax_metric_fallback_expression(self, *args, **kwargs):
        return None

    def _auto_qualify_cross_table_refs(self, expr, aliases):
        return expr


def _builder(translator=None):
    return MetricsClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=None,
        sanitizer=DummySanitizer(),
        translator=translator or DummyTranslator(),
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
    )


def _model(metrics):
    return SimpleNamespace(metrics=metrics, unique_name="model", label="model")


def test_dollar_sign_metric_name_is_recorded_not_silently_dropped():
    builder = _builder()
    metric = SimpleNamespace(
        unique_name="Sales $", dataset="SalesFact", expression="",
        sql_expression=None, source_column="Revenue",
        aggregation=SimpleNamespace(value="sum"),
    )

    lines = builder.build_for_sml(
        _model([metric]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"REVENUE"}},
        alias_by_raw={},
        all_physical_col_names={"REVENUE"},
        emittable_metric_name_set=set(),
    )

    assert lines == []
    records = builder.drop_ledger.to_json()
    assert len(records) == 1
    assert records[0]["entity_kind"] == "metric"
    assert records[0]["entity_name"] == "Sales $"
    assert records[0]["stage"] == DropStage.DDL_EMISSION.value


def test_metric_with_no_dataset_alias_is_recorded_not_silently_dropped():
    builder = _builder()
    metric = SimpleNamespace(
        unique_name="Orphan Metric", dataset="NoSuchDataset", expression="",
        sql_expression=None, source_column="X",
        aggregation=SimpleNamespace(value="sum"),
    )

    lines = builder.build_for_sml(
        _model([metric]),
        dataset_aliases={},  # "NoSuchDataset" never got a TABLES alias
        dataset_by_name={},
        dataset_col_lookup={},
        alias_by_raw={},
        all_physical_col_names=set(),
        emittable_metric_name_set=set(),
    )

    assert lines == []
    records = builder.drop_ledger.to_json()
    assert len(records) == 1
    assert records[0]["entity_name"] == "Orphan Metric"
    assert records[0]["dataset"] == "NoSuchDataset"


def test_metric_referencing_a_dropped_metric_gets_pruned_with_ledger_record():
    """The concrete bug found via reconciliation against a real run: a metric
    whose SQL references another metric's name (valid at emission time,
    since the referenced name is a known metric) is silently pruned once
    that referenced metric never actually produced a line. Reproduces the
    exact cascade traced against run 4fa695d8 (PCT_CATEGORY_COMPETE_SHARE /
    ATINDICATOR01 chain), collapsed to a minimal two-metric case."""
    translator = MetricExpressionTranslator(IdentifierSanitizer())
    builder = _builder(translator=translator)

    derived = SimpleNamespace(
        unique_name="Derived Metric", dataset="SalesFact", expression="",
        sql_expression='SALESFACT."BASE_METRIC" * 2',
        source_column=None, aggregation=None,
    )
    base = SimpleNamespace(
        unique_name="Base Metric", dataset="SalesFact", expression="",
        sql_expression=None, source_column=None, aggregation=None,
    )

    lines = builder.build_for_sml(
        _model([derived, base]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"X"}},
        alias_by_raw={},
        all_physical_col_names={"X"},
        emittable_metric_name_set=set(),
    )

    # Both metrics vanish from the DDL: Base has nothing to emit, and Derived
    # only referenced Base's name -- once Base never materializes, Derived's
    # line is unresolvable and gets pruned.
    assert lines == []

    records = builder.drop_ledger.to_json()
    names = {r["entity_name"] for r in records}
    assert "Base Metric" in names  # already-instrumented "nothing to emit" path
    assert "Derived Metric" in names  # the fix: cascading prune is now recorded
    derived_record = next(r for r in records if r["entity_name"] == "Derived Metric")
    assert derived_record["stage"] == DropStage.DDL_EMISSION.value


def test_stale_null_cast_sql_expression_is_recorded_not_silently_emitted():
    """A metric whose stored sql_expression is already the NULL-cast
    placeholder (e.g. persisted from an earlier translation attempt that
    declined to translate) must not be emitted as if it were real SQL --
    it has to be skipped and recorded via the ledger, same as any other
    untranslatable metric, using only placeholder names."""
    translator = MetricExpressionTranslator(IdentifierSanitizer())
    builder = _builder(translator=translator)
    metric = SimpleNamespace(
        unique_name="Placeholder Metric", dataset="SalesFact",
        expression="", sql_expression="CAST(NULL AS DOUBLE)",
        source_column=None, aggregation=None,
    )

    lines = builder.build_for_sml(
        _model([metric]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"X"}},
        alias_by_raw={},
        all_physical_col_names={"X"},
        emittable_metric_name_set=set(),
    )

    assert lines == []
    records = builder.drop_ledger.to_json()
    assert len(records) == 1
    assert records[0]["entity_name"] == "Placeholder Metric"
    assert records[0]["stage"] == DropStage.DAX_TRANSLATION.value


def test_real_sql_expression_mentioning_null_keyword_is_still_emitted():
    """No false positives: a real sql_expression that legitimately contains
    the word NULL (an IS NULL guard) is a different shape from the bare
    CAST(NULL AS DOUBLE) placeholder and must still be emitted normally."""
    translator = MetricExpressionTranslator(IdentifierSanitizer())
    builder = _builder(translator=translator)
    metric = SimpleNamespace(
        unique_name="Real Metric", dataset="SalesFact",
        expression="",
        sql_expression='CASE WHEN SALESFACT."X" IS NULL THEN 0 ELSE SALESFACT."X" END',
        source_column=None, aggregation=None,
    )

    lines = builder.build_for_sml(
        _model([metric]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"X"}},
        alias_by_raw={},
        all_physical_col_names={"X"},
        emittable_metric_name_set=set(),
    )

    assert len(lines) == 1
    assert "IS NULL" in lines[0]
    assert builder.drop_ledger.to_json() == []


def test_record_removed_metric_lines_maps_ddl_name_back_to_unique_name():
    """Direct test of the diff-based instrumentation helper: given a
    before/after pair of METRICS-clause lines, it records the metric that
    disappeared using its original unique_name (not the sanitized DDL
    alias), so ledger entries stay comparable to the SML snapshot's names."""
    builder = _builder()
    before = [
        '  SALESFACT."REVENUE" AS SUM(SALESFACT."REVENUE"),',
        '  SALESFACT."UNITS" AS SUM(SALESFACT."UNITS")',
    ]
    after = [
        '  SALESFACT."REVENUE" AS SUM(SALESFACT."REVENUE")',
    ]
    builder._record_removed_metric_lines(
        before, after,
        ddl_name_to_unique={"UNITS": "Total Units Sold", "REVENUE": "Revenue"},
        ddl_name_to_dataset={"UNITS": "SalesFact", "REVENUE": "SalesFact"},
        reason="test removal reason",
    )
    records = builder.drop_ledger.to_json()
    assert len(records) == 1
    assert records[0]["entity_name"] == "Total Units Sold"
    assert records[0]["dataset"] == "SalesFact"
    assert records[0]["reason"] == "test removal reason"


def test_record_ddl_deployment_drops_records_every_metric_nulled_in_one_sweep():
    """The bug found via the proj-test-1 investigation: SemanticDDLSanitizer.
    remediate_invalid_identifier's METRICS-clause sweep can null out several
    metrics in a single pass when they all reference the same rejected
    identifier (e.g. a shared anchor column like MAX_DATE, qualified as
    "SALESFACT.MAX_DATE" so it doesn't match the bare-token special case).
    _record_ddl_deployment_drops must record a drop for every one of them —
    not just whichever single name _resolve_metric_name_for_invalid_identifier
    happens to resolve first, which is all it used to do."""
    fake_self = SimpleNamespace(_dropped_metrics=[], drop_ledger=DropLedger())
    nulled_metric_names = ["TOTAL_UNITS_YTD", "TOTAL_UNITS_SPLY", "TOTAL_UNITS_YTD_VAR"]

    SnowflakeEmitter._record_ddl_deployment_drops(
        fake_self,
        "SALESFACT.MAX_DATE",
        nulled_metric_names,
        "<ddl text, unused when nulled_metric_names is non-empty>",
        RuntimeError("000904: invalid identifier 'SALESFACT.MAX_DATE'"),
    )

    assert {d["metric"] for d in fake_self._dropped_metrics} == set(nulled_metric_names)
    assert len(fake_self._dropped_metrics) == 3

    records = fake_self.drop_ledger.to_json()
    assert len(records) == 3
    assert {r["entity_name"] for r in records} == set(nulled_metric_names)
    assert all(r["entity_kind"] == "metric" for r in records)
    assert all(r["stage"] == DropStage.DDL_DEPLOYMENT.value for r in records)


def test_record_ddl_deployment_drops_falls_back_to_single_name_when_sweep_nulled_no_metric():
    """When the remediated identifier was fixed inside TABLES/RELATIONSHIPS/
    DIMENSIONS instead of METRICS, remediate_invalid_identifier's
    nulled_metric_names is empty (no metric was nulled at all) —
    _record_ddl_deployment_drops must fall back to resolving one name from
    the raw DDL text, exactly as it did before this fix, so that path's
    existing behavior (some record, even if approximate, rather than none)
    is unchanged."""
    fake_self = SimpleNamespace(
        _dropped_metrics=[],
        drop_ledger=DropLedger(),
        # A staticmethod, so no bound `self` is needed to call it this way.
        _resolve_metric_name_for_invalid_identifier=SnowflakeEmitter._resolve_metric_name_for_invalid_identifier,
    )
    ddl = 'METRICS (\n  SALESFACT."SOME_METRIC" AS SUM(SALESFACT."BAD_COL")\n);'

    SnowflakeEmitter._record_ddl_deployment_drops(
        fake_self, "SALESFACT.BAD_COL", [], ddl, RuntimeError("boom"),
    )

    assert len(fake_self._dropped_metrics) == 1
    assert fake_self._dropped_metrics[0]["metric"] == "SOME_METRIC"
    records = fake_self.drop_ledger.to_json()
    assert len(records) == 1
    assert records[0]["entity_name"] == "SOME_METRIC"
