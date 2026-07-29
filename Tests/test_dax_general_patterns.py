"""
Tests proving the generalized, deterministic rule-based DAX handling added
for: CALCULATE-wrapping-a-measure as a sub-expression, binary arithmetic
between measure references (CALCULATE-wrapped or not), deeply nested
IF/SWITCH, string-producing root expressions, zero-data-dependency constant
expressions, and threshold-conditional aggregation branching.

All DAX bodies use placeholder/fake names (Measure_A, SomeTable, SomeColumn,
etc.) — none of this is tied to any real model, past or present.
"""
from types import SimpleNamespace

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.dax_ast_parser import (
    dax_has_zero_data_dependencies,
    dax_root_is_string_producing,
)
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIDataType,
    OSIMetric,
    OSIAggregationType,
)
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    DataType,
    AggregationType,
)


def _metric(name, sql):
    return SimpleNamespace(unique_name=name, sql_expression=sql)


class TestCategory1CalculateAsSubExpression:
    """CALCULATE([Measure]) nested inside IF/arithmetic (not the whole metric body)."""

    def test_calculate_wrapped_measure_as_if_branch(self):
        translator = DAXTranslator()
        metrics_context = [
            _metric("Measure_A", 'SUM(fact."COL_A")'),
            _metric("Measure_B", 'SUM(fact."COL_B")'),
        ]
        dax = "IF('SomeTable'[SomeColumn] > 0, CALCULATE([Measure_A]), CALCULATE([Measure_B]))"
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)
        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert "CASE WHEN" in result.sql
        assert 'SUM(fact."COL_A")' in result.sql
        assert 'SUM(fact."COL_B")' in result.sql

    def test_calculate_wrapped_measure_as_arithmetic_operand(self):
        translator = DAXTranslator()
        metrics_context = [_metric("Measure_A", 'SUM(fact."COL_A")')]
        dax = "CALCULATE([Measure_A]) * 2"
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)
        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert 'SUM(fact."COL_A")' in result.sql
        assert "2" in result.sql


class TestCategory2BinaryArithmeticBetweenMeasures:
    """Binary arithmetic between two measure references, CALCULATE-wrapped or not."""

    def test_arithmetic_between_two_calculate_wrapped_measures(self):
        translator = DAXTranslator()
        metrics_context = [
            _metric("Measure_A", 'SUM(fact."COL_A")'),
            _metric("Measure_B", 'SUM(fact."COL_B")'),
        ]
        dax = "CALCULATE([Measure_A]) - CALCULATE([Measure_B])"
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)
        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert 'SUM(fact."COL_A")' in result.sql
        assert 'SUM(fact."COL_B")' in result.sql
        assert " - " in result.sql

    def test_arithmetic_between_one_calculate_wrapped_and_one_bare_measure(self):
        translator = DAXTranslator()
        metrics_context = [
            _metric("Measure_A", 'SUM(fact."COL_A")'),
            _metric("Measure_B", 'SUM(fact."COL_B")'),
        ]
        dax = "CALCULATE([Measure_A]) / [Measure_B]"
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)
        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert 'SUM(fact."COL_A")' in result.sql
        assert 'SUM(fact."COL_B")' in result.sql


class TestCategory3NestedConditionalsAreNotDepthLimited:
    """Deeply nested IF is not a rendering/recursion limitation; a failure is
    always attributable to one unsupported construct in a branch, not to
    nesting depth itself — proven by holding depth constant and varying only
    whether every leaf is supported."""

    def test_four_level_nested_if_with_only_supported_constructs_succeeds(self):
        translator = DAXTranslator()
        metrics_context = [_metric("Measure_A", 'SUM(fact."COL_A")')]
        dax = (
            "IF([Measure_A] > 100, 4, "
            "IF([Measure_A] > 75, 3, "
            "IF([Measure_A] > 50, 2, "
            "IF([Measure_A] > 25, 1, 0))))"
        )
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)
        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert result.sql.count("CASE WHEN") == 4

    def test_same_nesting_depth_fails_entirely_when_one_leaf_is_unsupported(self):
        translator = DAXTranslator()
        metrics_context = [_metric("Measure_A", 'SUM(fact."COL_A")')]
        dax = (
            "IF([Measure_A] > 100, 4, "
            "IF([Measure_A] > 75, 3, "
            "IF([Measure_A] > 50, CROSSFILTER('TableX'[ColX], 'TableY'[ColY], BOTH), 1)))"
        )
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)
        # The unsupported CROSSFILTER leaf aborts the WHOLE render (all-or-
        # nothing propagation of DaxRenderError) — the identical nesting
        # depth succeeded above, so this failure is attributable to the
        # unsupported construct, not to nesting depth.
        assert not result.is_success


class TestCategory4StringProducingRoot:
    """A DAX body whose root operation produces a string cannot be a
    Snowflake semantic-view metric — detected purely from AST root shape,
    with no per-model/field logic."""

    def test_concatenate_root_is_string_producing(self):
        assert dax_root_is_string_producing('CONCATENATE("Hello", "World")') is True

    def test_left_function_root_is_string_producing(self):
        assert dax_root_is_string_producing("LEFT('SomeTable'[SomeColumn], 3)") is True

    def test_bare_string_literal_root_is_string_producing(self):
        assert dax_root_is_string_producing('"Just A Literal"') is True

    def test_numeric_aggregation_root_is_not_string_producing(self):
        assert dax_root_is_string_producing("SUM('SomeTable'[SomeColumn])") is False

    def test_calculate_wrapped_aggregation_root_is_not_string_producing(self):
        assert dax_root_is_string_producing("CALCULATE(SUM('SomeTable'[SomeColumn]))") is False

    def test_string_producing_metric_excluded_by_design_through_osi_pipeline(self, monkeypatch):
        # Force every later Tier-5 batch pass to report a fabricated
        # "success" for anything handed to it. If the by-design exclusion
        # were ever passed through as a translation candidate (the exact bug
        # this closes), this mock would "resolve" it and the assertions
        # below would fail — proving the exclusion is never re-attempted,
        # not merely proving today's environment has no working LLM.
        def _fake_batch_translate_tier5(self, metrics_list):
            return {
                name: SimpleNamespace(is_success=True, sql="'SHOULD NOT APPEAR'", tier=5)
                for name, _dax, _alias, _dataset in metrics_list
            }

        monkeypatch.setattr(DAXTranslator, "batch_translate_tier5", _fake_batch_translate_tier5)

        converter = OSIToSMLConverter()
        osi_model = OSIModel(
            unique_name="synthetic-model",
            label="Synthetic Model",
            source_platform="fabric",
            datasets=[
                OSIDataset(
                    unique_name="SomeTable",
                    columns=[OSIColumn(unique_name="SomeColumn", data_type=OSIDataType.STRING)],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Fake_Label_Field",
                    label="Fake Label Field",
                    dataset="SomeTable",
                    expression="CONCATENATE('SomeTable'[SomeColumn], \" - suffix\")",
                    aggregation=OSIAggregationType.NONE,
                )
            ],
        )
        sml_model = converter.from_osi(osi_model)
        metric = next(m for m in sml_model.metrics if m.unique_name == "Fake_Label_Field")
        assert metric.sync_enabled is False
        assert "BY_DESIGN_EXCLUDED" in (metric.sync_failure_reason or "")
        assert not metric.sql_expression


class TestCategory5ZeroDataDependencyConstants:
    """A DAX body with no column/measure/table reference has nothing to
    aggregate — detected purely by walking the AST for the absence of any
    ColumnRefNode/MeasureRefNode."""

    def test_bare_numeric_literal_has_zero_dependencies(self):
        assert dax_has_zero_data_dependencies("42") is True

    def test_bare_string_literal_has_zero_dependencies(self):
        assert dax_has_zero_data_dependencies('"Constant Value"') is True

    def test_arithmetic_over_literals_only_has_zero_dependencies(self):
        assert dax_has_zero_data_dependencies("(1 + 2) * 3") is True

    def test_expression_referencing_a_column_has_data_dependency(self):
        assert dax_has_zero_data_dependencies("SUM('SomeTable'[SomeColumn])") is False

    def test_expression_referencing_a_measure_has_data_dependency(self):
        assert dax_has_zero_data_dependencies("[Measure_A] + 1") is False

    def test_constant_metric_excluded_by_design_through_osi_pipeline(self, monkeypatch):
        def _fake_batch_translate_tier5(self, metrics_list):
            return {
                name: SimpleNamespace(is_success=True, sql="'SHOULD NOT APPEAR'", tier=5)
                for name, _dax, _alias, _dataset in metrics_list
            }

        monkeypatch.setattr(DAXTranslator, "batch_translate_tier5", _fake_batch_translate_tier5)

        converter = OSIToSMLConverter()
        osi_model = OSIModel(
            unique_name="synthetic-model",
            label="Synthetic Model",
            source_platform="fabric",
            datasets=[
                OSIDataset(
                    unique_name="SomeTable",
                    columns=[OSIColumn(unique_name="SomeColumn", data_type=OSIDataType.FLOAT)],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Fake_Constant_Metric",
                    label="Fake Constant Metric",
                    dataset="SomeTable",
                    expression="42",
                    aggregation=OSIAggregationType.NONE,
                )
            ],
        )
        sml_model = converter.from_osi(osi_model)
        metric = next(m for m in sml_model.metrics if m.unique_name == "Fake_Constant_Metric")
        assert metric.sync_enabled is False
        assert "BY_DESIGN_EXCLUDED" in (metric.sync_failure_reason or "")


class TestCategory6ThresholdConditionalAggregation:
    """IF/SWITCH branching on a comparison against an aggregation function —
    proven to need no bespoke pattern-matcher: it's the same general nested-
    conditional grammar as Category 3, composed with a plain comparison
    condition whose left side happens to be an aggregation call."""

    def test_if_chain_with_aggregation_threshold_conditions(self):
        translator = DAXTranslator()
        dax = (
            "IF(AVERAGE('SomeTable'[SomeColumn]) > 100, 3, "
            "IF(AVERAGE('SomeTable'[SomeColumn]) > 50, 2, 1))"
        )
        result = translator.translate(dax, "fact", "SomeTable")
        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert "AVG(" in result.sql
        assert result.sql.count("CASE WHEN") == 2


class TestFunctionWrappedMeasureArithmeticNeverLeaksDaxSyntax:
    """A function call wrapping bracket-arithmetic between two measures
    (e.g. INT(DIVIDE([A], [B])*100)) must never be emitted as SQL with the
    DAX function name left untranslated — the naive token-substitution
    fallbacks can only rewrite [Measure] references, not surrounding
    function-call syntax, so they must decline and let a function-aware
    tier (the AST renderer) handle it instead."""

    def test_int_wrapped_divide_of_two_measures_has_no_literal_dax_syntax(self):
        translator = DAXTranslator()
        metrics_context = [
            _metric("Measure_A", 'SUM(fact."COL_A"::FLOAT)'),
            _metric("Measure_B", 'SUM(fact."COL_B"::FLOAT)'),
        ]
        dax = "INT(DIVIDE([Measure_A], [Measure_B])*100)"
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)

        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert "DIVIDE" not in result.sql.upper(), f"literal DAX DIVIDE leaked into SQL: {result.sql!r}"
        assert "INT(" not in result.sql.upper(), f"literal DAX INT( leaked into SQL: {result.sql!r}"
        assert "DIV0" in result.sql.upper()
        assert 'SUM(fact."COL_A"' in result.sql
        assert 'SUM(fact."COL_B"' in result.sql

    def test_dependency_translation_declines_rather_than_guesses(self):
        translator = DAXTranslator()
        metrics_context = [
            _metric("Measure_A", 'SUM(fact."COL_A"::FLOAT)'),
            _metric("Measure_B", 'SUM(fact."COL_B"::FLOAT)'),
        ]
        dax = "INT(DIVIDE([Measure_A], [Measure_B])*100)"
        assert translator._try_dependency_translation(dax, "fact", "SomeTable", metrics_context) is None
        assert translator._try_branching(dax, metrics_context) is None


class TestUnresolvedMeasureGuard:
    """The AST renderer cannot tell 'a measure with no SQL yet' apart from
    'a column referenced in iterator context' (both use bracket syntax) — so
    when a bracket reference names a real, still-unresolved metric, the
    translator must decline rather than silently render it as a column."""

    def test_reference_to_a_known_but_unresolved_measure_is_not_silently_accepted(self):
        translator = DAXTranslator()
        # Measure_B is a REAL metric in metrics_context but has no
        # sql_expression yet (simulating it not having been translated in
        # this pass). The renderer would otherwise silently treat
        # [Measure_B] as a raw column named MEASURE_B.
        metrics_context = [
            _metric("Measure_A", 'SUM(fact."COL_A")'),
            SimpleNamespace(unique_name="Measure_B", sql_expression=None),
        ]
        dax = "CALCULATE([Measure_A]) - CALCULATE([Measure_B])"
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)
        assert not result.is_success


class TestSyncFailureReasonClearedOnSuccess:
    """A metric's sync_failure_reason must never survive alongside
    sync_enabled=True — whichever pass or tier is the one that eventually
    succeeds, it must clear any reason a prior, more pessimistic pass left
    behind. Regression coverage for the same "later pass doesn't fully
    reconcile state" class of bug as the by-design-exclusion re-attempt
    issue, but on the success side rather than the exclusion side."""

    def test_metric_pessimistically_preclassified_then_actually_translated_has_no_stale_reason(self):
        # analyze_complexity() flags CALCULATE(agg, ALLEXCEPT(...)) as an
        # "Unsupported DAX pattern" up front (sync_enabled=False, a reason
        # set — verified directly against DAXTranslator.analyze_complexity)
        # — but the AST renderer actually translates this shape (an
        # aggregate windowed by ALLEXCEPT's partition columns) correctly,
        # within the very same _convert_metric() call. The final metric must
        # show success cleanly, with no leftover reason from that earlier,
        # overly pessimistic classification.
        converter = OSIToSMLConverter()
        osi_model = OSIModel(
            unique_name="synthetic-model",
            label="Synthetic Model",
            source_platform="fabric",
            datasets=[
                OSIDataset(
                    unique_name="SomeTable",
                    columns=[
                        OSIColumn(unique_name="SomeColumn", data_type=OSIDataType.FLOAT),
                        OSIColumn(unique_name="SomeColumn2", data_type=OSIDataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Windowed_Sum",
                    label="Windowed Sum",
                    dataset="SomeTable",
                    expression=(
                        "CALCULATE(SUM('SomeTable'[SomeColumn]), "
                        "ALLEXCEPT('SomeTable', 'SomeTable'[SomeColumn2]))"
                    ),
                    aggregation=OSIAggregationType.NONE,
                )
            ],
        )
        sml_model = converter.from_osi(osi_model)
        metric = next(m for m in sml_model.metrics if m.unique_name == "Windowed_Sum")

        assert metric.sync_enabled is True
        assert metric.sync_failure_reason is None, (
            f"expected no stale failure reason on a successfully-translated "
            f"metric, got: {metric.sync_failure_reason!r}"
        )
        assert metric.sql_expression is not None
        assert "PARTITION BY" in metric.sql_expression

    def test_metric_that_fails_first_pass_and_succeeds_second_pass_has_no_stale_reason(self):
        # "Downstream_Metric" forward-references two metrics defined later in
        # the list, so _convert_metric()'s own first-pass translate() call
        # (no metrics_context yet) cannot resolve it and records a "deferred"
        # reason. Once all metrics are loaded, _resolve_metric_dependencies()
        # resolves it on a second pass — the stale first-pass reason must not
        # survive that success.
        converter = OSIToSMLConverter()
        osi_model = OSIModel(
            unique_name="synthetic-model",
            label="Synthetic Model",
            source_platform="fabric",
            datasets=[
                OSIDataset(
                    unique_name="SomeTable",
                    columns=[
                        OSIColumn(unique_name="ColumnA", data_type=OSIDataType.FLOAT),
                        OSIColumn(unique_name="ColumnB", data_type=OSIDataType.FLOAT),
                    ],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Downstream_Metric",
                    label="Downstream Metric",
                    dataset="SomeTable",
                    expression="[Upstream_A] - [Upstream_B]",
                    aggregation=OSIAggregationType.NONE,
                ),
                OSIMetric(
                    unique_name="Upstream_A",
                    label="Upstream A",
                    dataset="SomeTable",
                    expression="SUM([ColumnA])",
                    aggregation=OSIAggregationType.NONE,
                ),
                OSIMetric(
                    unique_name="Upstream_B",
                    label="Upstream B",
                    dataset="SomeTable",
                    expression="SUM([ColumnB])",
                    aggregation=OSIAggregationType.NONE,
                ),
            ],
        )
        sml_model = converter.from_osi(osi_model)
        metric = next(m for m in sml_model.metrics if m.unique_name == "Downstream_Metric")

        assert metric.sync_enabled is True
        assert metric.sync_failure_reason is None, (
            f"expected the second-pass success to clear the first-pass "
            f"deferred reason, got: {metric.sync_failure_reason!r}"
        )
        assert metric.sql_expression is not None


class TestByDesignExclusionSurvivesDDLEmission:
    """DDL emission (MetricsClauseBuilder) is a separate, independent
    translation attempt from SML conversion — it has its own LLM and
    basic-DAX-pattern fallbacks, neither of which know about an earlier
    by-design exclusion. A naive fallback can "succeed" at converting a
    constant DAX expression (e.g. a bare string literal) into equally-valid
    but meaningless SQL (e.g. an empty string literal), silently undoing the
    exclusion. This must never happen, for any model — not just metrics
    named NUMSPACE01/NUMSPACE02."""

    def _build_emitter(self) -> SnowflakeEmitter:
        config = SnowflakeConfig(
            account="test.local",
            user="test_user",
            password="test_password",
            warehouse="test_wh",
            database="test_db",
            schema_name="test_schema",
            role="test_role",
        )
        return SnowflakeEmitter(config, ConnectorBehavior())

    def test_constant_metric_never_reaches_the_metrics_clause(self):
        emitter = self._build_emitter()
        sml = SMLModel(
            unique_name="synthetic_model",
            datasets=[
                SMLDataset(
                    unique_name="SomeTable",
                    source_table="SomeTable",
                    columns=[SMLColumn(unique_name="SomeColumn", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Real_Measure",
                    dataset="SomeTable",
                    sql_expression='SUM(SOMETABLE."SOMECOLUMN")',
                    aggregation=AggregationType.SUM,
                    sync_enabled=True,
                ),
                SMLMetric(
                    unique_name="Placeholder_Spacer",
                    dataset="SomeTable",
                    # Mirrors a real Fabric "spacer" measure: a DAX empty-
                    # string literal, no sql_expression, classified by-design
                    # excluded earlier in the pipeline (as tmsl_to_sml.py /
                    # osi_to_sml.py would have done).
                    expression='""',
                    sql_expression=None,
                    sync_enabled=False,
                    sync_failure_reason=(
                        "BY_DESIGN_EXCLUDED: expression has no column/measure/table "
                        "reference (a compile-time constant) — there is nothing to "
                        "aggregate, so this is not a DAX translation failure."
                    ),
                ),
            ],
        )

        ddls = emitter.generate_ddls(sml)
        ddl_text = "\n".join(ddls)

        assert "PLACEHOLDER_SPACER" not in ddl_text.upper(), (
            "by-design-excluded metric must not appear in the emitted DDL at all"
        )
        assert 'REAL_MEASURE" AS SUM(' in ddl_text.upper() or "REAL_MEASURE" in ddl_text.upper()

        drop_names = {r["entity_name"] for r in emitter.drop_ledger.to_json()}
        assert "Placeholder_Spacer" in drop_names
        by_design_records = [
            r for r in emitter.drop_ledger.to_json()
            if r["entity_name"] == "Placeholder_Spacer"
        ]
        assert by_design_records and by_design_records[0]["by_design"] is True


class TestDivideGeneralizesToAnyMeasurePair:
    """DIVIDE([Numerator], [Denominator][, alt]) must keep its division for
    ANY pair of measure/column names — not just one hardcoded pair. This is
    a regression test for a bug where the rule-based DIVIDE translator only
    produced a real division when the DAX literally named one specific
    historical measure pair, silently returning numerator-only SQL (or
    None) for every other DIVIDE-shaped expression."""

    def test_divide_of_placeholder_measures_keeps_division(self):
        from semabridge.converter.dax_rule_translator import translate_divide_measures

        dax = "DIVIDE([Placeholder Numerator Measure], [Placeholder Denominator Measure], 0)"
        sql = translate_divide_measures(dax, "fact")

        assert sql is not None, "DIVIDE with unfamiliar measure names must still translate"
        assert "/" in sql, f"division operator missing from translated SQL: {sql!r}"
        assert "NULLIF" in sql.upper(), f"safe-division guard missing: {sql!r}"
        assert "COALESCE" in sql.upper()

    def test_divide_of_columns_keeps_division_via_rule_based_translation(self):
        from semabridge.converter.dax_rule_translator import rule_based_translation

        dax = "DIVIDE([SomeNumeratorColumn], [SomeDenominatorColumn], 0)"
        sql = rule_based_translation(dax, "fact")

        assert sql is not None
        assert "/" in sql, f"division operator missing from translated SQL: {sql!r}"
        assert "NULLIF" in sql.upper()

    def test_llm_candidate_missing_division_is_rejected_for_any_dax(self):
        from semabridge.connectors.translator import MetricExpressionTranslator

        dax = "DIVIDE([Placeholder Numerator Measure], [Placeholder Denominator Measure], 0)"
        numerator_only_sql = 'SUM(fact."SOME_NUMERATOR_COLUMN")'

        assert MetricExpressionTranslator._dax_divide_lost_its_division(dax, numerator_only_sql) is True
        divided_sql = 'SUM(fact."SOME_NUMERATOR_COLUMN") / NULLIF(SUM(fact."SOME_DENOMINATOR_COLUMN"), 0)'
        assert MetricExpressionTranslator._dax_divide_lost_its_division(dax, divided_sql) is False


class TestMeasureReferenceNeverLeaksAsColumnRef:
    """A DAX measure referencing another measure by name ([OtherMeasure])
    must never be rendered as a direct column reference (ALIAS."OTHERMEASURE")
    when [OtherMeasure] is a real, tracked measure that just hasn't been
    translated yet — that would silently reference a physical column that
    doesn't exist. This is a regression test for that bug.

    Crucially, an unqualified bracket reference the caller's model has NO
    measure-registry knowledge of at all (e.g. plain SUM([Amount]), the
    overwhelmingly common shape throughout this codebase) must still fall
    back to a column reference exactly as before — the AST parser cannot
    distinguish "this bracket names a column" from "this bracket names a
    measure" at the token level, so the fix must not break that pre-existing,
    load-bearing convention while fixing the narrower measure-reference bug.
    """

    def test_unresolved_but_known_measure_ref_fails_closed_not_as_column(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "[Placeholder_Other_Measure] + 1"
        sql = try_ast_translate(
            dax,
            table_alias="fact",
            date_alias="COL_DATE",
            measure_sql_map={},
            known_measure_names={"Placeholder_Other_Measure"},
        )

        assert sql is None, (
            f"unresolved-but-known measure reference must fail closed (None), not fabricate SQL: {sql!r}"
        )

    def test_bracket_name_outside_measure_registry_still_falls_back_to_column(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "[SomeColumn] + 1"
        sql = try_ast_translate(dax, table_alias="fact", date_alias="COL_DATE", measure_sql_map={})

        assert sql is not None, "bracket name with no measure-registry knowledge must still translate"
        assert 'fact."SOMECOLUMN"' in sql, (
            f"expected the pre-existing column-reference fallback to be preserved: {sql!r}"
        )

    def test_resolved_measure_ref_inlines_referenced_measure_sql(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "[Placeholder_Other_Measure] + 1"
        sql = try_ast_translate(
            dax,
            table_alias="fact",
            date_alias="COL_DATE",
            measure_sql_map={"Placeholder_Other_Measure": 'SUM(fact."SOME_COLUMN")'},
        )

        assert sql is not None
        assert 'SUM(fact."SOME_COLUMN")' in sql
        assert '"PLACEHOLDER_OTHER_MEASURE"' not in sql.upper(), (
            f"measure reference leaked as a dot-notation column reference: {sql!r}"
        )

    def test_all_measure_names_includes_measures_with_no_sql_yet(self):
        # DAXTranslator must tell the renderer about every tracked measure —
        # not just the ones already resolved — so a genuinely unresolved
        # measure reference fails closed instead of falling through to the
        # column-reference fallback meant for names outside the registry.
        translator = DAXTranslator()
        metrics_context = [_metric("Placeholder_Other_Measure", None)]

        all_names = translator._all_measure_names(metrics_context)
        resolved = translator._build_resolved_measures_map("fact", metrics_context)

        assert "Placeholder_Other_Measure" in all_names
        assert "Placeholder_Other_Measure" not in resolved

    def test_measure_referencing_resolved_measure_through_full_translator_inlines_it(self):
        translator = DAXTranslator()
        metrics_context = [
            _metric("Placeholder_Other_Measure", 'SUM(fact."SOME_COLUMN")'),
        ]
        dax = "[Placeholder_Other_Measure] + 1"
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)

        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert '"PLACEHOLDER_OTHER_MEASURE"' not in result.sql.upper(), (
            f"resolved measure reference must be inlined, not left as a column reference: {result.sql!r}"
        )
        assert 'SUM(fact."SOME_COLUMN")' in result.sql


class TestTimeIntelligenceCarriesADateRangeFilter:
    """Time-intelligence DAX (TOTALYTD, SAMEPERIODLASTYEAR, etc.) must
    produce SQL with an actual, distinct date-range filter bounding it to
    the intended period — not the same value as the non-time-filtered base
    measure, and not a bare window function (OVER), which a Snowflake
    semantic-view METRICS clause does not support. This is a regression
    test for a bug where time-intelligence functions were dispatched to a
    branch that emitted an unbounded OVER(...) window function (or, for
    SAMEPERIODLASTYEAR/DATEADD/etc., were rejected outright) before the
    already-correct CASE-WHEN-bounded AST renderer ever got a chance."""

    def test_totalytd_of_direct_aggregation_has_distinct_bounded_filter(self):
        translator = DAXTranslator()

        base_dax = "SUM([SomeColumn])"
        base_result = translator.translate(base_dax, "fact", "SomeTable")

        ytd_dax = "TOTALYTD(SUM([SomeColumn]), 'SomeDateTable'[SomeDateColumn])"
        ytd_result = translator.translate(ytd_dax, "fact", "SomeTable")

        assert ytd_result.is_success, f"expected TOTALYTD to translate, got sql={ytd_result.sql!r}"
        assert "OVER" not in ytd_result.sql.upper(), (
            f"time-intelligence SQL must not be a window function (unsupported in METRICS clause): {ytd_result.sql!r}"
        )
        assert "DATE_TRUNC" in ytd_result.sql.upper() and "CASE WHEN" in ytd_result.sql.upper(), (
            f"expected a real date-range CASE WHEN filter: {ytd_result.sql!r}"
        )
        assert base_result.sql != ytd_result.sql, (
            "YTD translation must differ from the non-time-filtered base measure "
            f"(base={base_result.sql!r}, ytd={ytd_result.sql!r})"
        )

    def test_totalytd_of_measure_reference_inlines_without_nested_aggregate(self):
        translator = DAXTranslator()
        metrics_context = [
            _metric("Placeholder_Other_Measure", 'SUM(fact."SOME_COLUMN"::FLOAT)'),
        ]
        dax = "TOTALYTD([Placeholder_Other_Measure], 'SomeDateTable'[SomeDateColumn])"
        result = translator.translate(dax, "fact", "SomeTable", metrics_context=metrics_context)

        assert result.is_success, f"expected TOTALYTD-of-measure-ref to translate, got sql={result.sql!r}"
        sql_upper = result.sql.upper()
        assert "OVER" not in sql_upper
        assert "DATE_TRUNC" in sql_upper and "CASE WHEN" in sql_upper
        assert sql_upper.count("SUM(") == 1, (
            f"must not nest an outer aggregate around the already-aggregated measure SQL: {result.sql!r}"
        )
        assert '"PLACEHOLDER_OTHER_MEASURE"' not in sql_upper

    def test_sameperiodlastyear_is_no_longer_rejected_outright(self):
        translator = DAXTranslator()

        dax = "CALCULATE(SUM([SomeColumn]), SAMEPERIODLASTYEAR('SomeDateTable'[SomeDateColumn]))"
        result = translator.translate(dax, "fact", "SomeTable")

        assert result.sql is not None, (
            "SAMEPERIODLASTYEAR must get a chance at the AST renderer instead of being "
            "flatly rejected before any tier runs"
        )
        assert "OVER" not in result.sql.upper()
