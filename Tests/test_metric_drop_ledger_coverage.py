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

    def _resolve_column_name_for_dataset(self, cols, col_name):
        return col_name if col_name in cols else None


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


def test_dollar_sign_metric_name_is_sanitized_not_dropped():
    """'$' in a metric name is no longer a hardcoded drop reason --
    identifier_sanitizer's general character-replacement table (the same one
    that turns '@' into 'AT' and '#' into 'NUM' for every other metric) turns
    'Sales $' into 'SALES_DOL' and the metric is emitted normally, exactly
    like any metric with punctuation in its name."""
    builder = _builder()
    metric = SimpleNamespace(
        unique_name="Sales $", dataset="SalesFact", expression="",
        sql_expression=None, source_column="Revenue",
        aggregation=SimpleNamespace(value="sum"),
    )

    # build_for_osi (not build_for_sml): the OSI path sanitizes source_column
    # directly instead of resolving it through a live schema_manager, which
    # this test's fixture doesn't provide -- irrelevant to what's being
    # exercised here (the '$'-name path), same as the other tests in this
    # module that don't need a real schema_manager either.
    lines = builder.build_for_osi(
        _model([metric]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"REVENUE"}},
        alias_by_raw={},
        all_physical_col_names={"REVENUE"},
        emittable_metric_name_set=set(),
    )

    assert lines == ['  SALESFACT."SALES_DOL" AS SUM(SALESFACT."REVENUE")']
    assert builder.drop_ledger.to_json() == []


def test_dollar_sanitized_name_colliding_with_another_metric_does_not_merge():
    """Safety check for the fix above: 'Sales $' and 'Sales Dol' both
    sanitize_alias to the identical 'SALES_DOL' base -- confirms the
    existing duplicate-name disambiguation machinery (metric_base_totals /
    metric_signature_seen / _resolve_unique_metric_alias, the same
    mechanism any two colliding names go through regardless of which
    character caused the collision) assigns each a distinct suffixed
    identifier instead of silently merging two distinct metrics into one
    DDL line."""
    builder = _builder()
    metric_dollar = SimpleNamespace(
        unique_name="Sales $", dataset="SalesFact", expression="",
        sql_expression=None, source_column="Revenue",
        aggregation=SimpleNamespace(value="sum"),
    )
    metric_spelled_out = SimpleNamespace(
        unique_name="Sales Dol", dataset="SalesFact", expression="",
        sql_expression=None, source_column="Discount",
        aggregation=SimpleNamespace(value="sum"),
    )

    lines = builder.build_for_osi(
        _model([metric_dollar, metric_spelled_out]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"REVENUE", "DISCOUNT"}},
        alias_by_raw={},
        all_physical_col_names={"REVENUE", "DISCOUNT"},
        emittable_metric_name_set=set(),
    )

    # Both metrics must survive as two distinct, disambiguated lines --
    # never collapse into one (which would silently drop one metric's data).
    assert len(lines) == 2
    assert lines[0] != lines[1]
    joined = "\n".join(lines)
    assert 'SUM(SALESFACT."REVENUE")' in joined
    assert 'SUM(SALESFACT."DISCOUNT")' in joined
    # Neither line uses the bare, un-disambiguated "SALES_DOL" name --
    # collision detection must have kicked in for both.
    assert 'SALESFACT."SALES_DOL"' not in joined
    assert builder.drop_ledger.to_json() == []


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


def test_date_expression_compared_to_integer_column_is_dropped_not_emitted():
    """Regression test for a real incident: a Tier-5 (Anthropic) translation
    for a rolling-12-month metric produced a date-arithmetic expression
    (DATE_ADDDAYSTODATE-shaped) compared directly against an INTEGER
    surrogate-key column. Nothing validated that type mismatch before it
    reached Snowflake, and the resulting DDL crashed the entire deploy --
    not just this one metric.

    Placeholder names only (no real project/metric names) -- this reproduces
    the *shape* of the failure: a date-producing function combined with a
    column whose model-declared data_type is integer. Two more metrics are
    included as siblings to prove the rest of the deploy proceeds normally
    around the dropped one.
    """
    translator = MetricExpressionTranslator(IdentifierSanitizer())
    builder = _builder(translator=translator)

    bad_metric = SimpleNamespace(
        unique_name="Rolling Metric Bad", dataset="SalesFact", expression="",
        sql_expression=(
            'SUM(CASE WHEN SALESFACT.MONTHINDEX > '
            'DATE_ADDDAYSTODATE(NEGATE(12), SALESFACT.MAX_DATE) '
            'THEN SALESFACT."UNITS" ELSE 0 END)'
        ),
        source_column=None, aggregation=None,
    )
    sibling_metric = SimpleNamespace(
        unique_name="Sibling Metric", dataset="SalesFact", expression="",
        sql_expression=None, source_column="Units",
        aggregation=SimpleNamespace(value="sum"),
    )

    model = SimpleNamespace(
        metrics=[bad_metric, sibling_metric],
        unique_name="model", label="model",
        datasets=[
            SimpleNamespace(
                unique_name="SalesFact",
                columns=[
                    SimpleNamespace(unique_name="MONTHINDEX", data_type="integer"),
                    SimpleNamespace(unique_name="MAX_DATE", data_type="date"),
                    SimpleNamespace(unique_name="UNITS", data_type="integer"),
                ],
            )
        ],
    )

    # build_for_osi (not build_for_sml): the sibling metric's direct
    # source_column+aggregation path sanitizes source_column directly
    # instead of resolving it through a live schema_manager, which this
    # test's fixture doesn't provide -- same reasoning as the '$'-name test
    # above; irrelevant to what's being exercised here (the type-mismatch
    # check on the bad metric's sql_expression).
    lines = builder.build_for_osi(
        model,
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"MONTHINDEX", "MAX_DATE", "UNITS"}},
        alias_by_raw={},
        all_physical_col_names={"MONTHINDEX", "MAX_DATE", "UNITS"},
        emittable_metric_name_set=set(),
    )

    # (a) the mismatched metric never reaches the DDL ...
    joined = "\n".join(lines)
    assert "DATE_ADDDAYSTODATE" not in joined
    assert "Rolling Metric Bad" not in joined

    # (b) ... and is recorded with a clear reason, not silently vanished
    records = builder.drop_ledger.to_json()
    bad_records = [r for r in records if r["entity_name"] == "Rolling Metric Bad"]
    assert len(bad_records) == 1
    assert bad_records[0]["stage"] == DropStage.DDL_EMISSION.value
    assert "Type mismatch" in bad_records[0]["reason"]
    assert "DATE" in bad_records[0]["reason"]
    assert "INTEGER" in bad_records[0]["reason"]

    # (c) the rest of the deploy proceeds normally -- the sibling metric
    # still gets emitted with its own, unrelated, valid line.
    assert len(lines) == 1
    assert 'SUM(SALESFACT."UNITS")' in lines[0]
    assert not any(r["entity_name"] == "Sibling Metric" for r in records)


def test_nested_aggregate_expression_is_dropped_not_emitted():
    """Regression test for a real incident: a Tier-5 translation for a
    rolling-window metric produced SUM(CASE WHEN MAX(...) ... END) -- an
    aggregate nested inside another aggregate's row-level argument. Nothing
    validated that structural shape before it reached Snowflake, and the
    resulting DDL crashed the entire deploy with 010218 (not just this one
    metric).

    Placeholder names only (no real project/metric names) -- this reproduces
    the *shape* of the failure, general over any aggregate pair, not tied to
    SUM/MAX specifically. A sibling metric is included to prove the rest of
    the deploy proceeds normally around the dropped one.
    """
    translator = MetricExpressionTranslator(IdentifierSanitizer())
    builder = _builder(translator=translator)

    bad_metric = SimpleNamespace(
        unique_name="Rolling Metric Bad", dataset="SalesFact", expression="",
        sql_expression=(
            'SUM(CASE WHEN MAX(SALESFACT."MONTHINDEX") - SALESFACT."MONTHINDEX" < 12 '
            'THEN SALESFACT."UNITS" ELSE 0 END)'
        ),
        source_column=None, aggregation=None,
    )
    sibling_metric = SimpleNamespace(
        unique_name="Sibling Metric", dataset="SalesFact", expression="",
        sql_expression=None, source_column="Units",
        aggregation=SimpleNamespace(value="sum"),
    )

    lines = builder.build_for_osi(
        _model([bad_metric, sibling_metric]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"MONTHINDEX", "UNITS"}},
        alias_by_raw={},
        all_physical_col_names={"MONTHINDEX", "UNITS"},
        emittable_metric_name_set=set(),
    )

    # (a) the nested-aggregate metric never reaches the DDL ...
    joined = "\n".join(lines)
    assert "Rolling Metric Bad" not in joined
    assert 'MAX(SALESFACT."MONTHINDEX")' not in joined

    # (b) ... and is recorded with a clear reason, not silently vanished or
    # crashing the build.
    records = builder.drop_ledger.to_json()
    bad_records = [r for r in records if r["entity_name"] == "Rolling Metric Bad"]
    assert len(bad_records) == 1
    assert bad_records[0]["stage"] == DropStage.DDL_EMISSION.value
    assert "Nested aggregate" in bad_records[0]["reason"]
    assert "SUM" in bad_records[0]["reason"] and "MAX" in bad_records[0]["reason"]

    # (c) the rest of the deploy proceeds normally -- the sibling metric
    # still gets emitted with its own, unrelated, valid line.
    assert len(lines) == 1
    assert 'SUM(SALESFACT."UNITS")' in lines[0]
    assert not any(r["entity_name"] == "Sibling Metric" for r in records)


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
