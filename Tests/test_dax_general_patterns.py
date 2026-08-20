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


class TestAllExceptContextTransitionFailsClosed:
    """ALLEXCEPT/ALL inside CALCULATE means "recompute this aggregate
    re-partitioned independently of the query's own grouping" — inherently
    a SQL window function. Snowflake's semantic-view METRICS clause
    forbids window functions, and this codebase has no derived-metric/
    precomputed-column mechanism to materialize the windowed value another
    way — genuinely unsupported today, not just untranslated. This used to
    silently "succeed" with an OVER(...) clause that deploys fine but
    returns NULL at query time (dax_ast_parser.py's
    DaxSqlRenderer._render_calculate). It must now fail closed with a
    specific, accurate reason, for both ALL and ALLEXCEPT, and for both
    Fabric/PBIX extraction pipelines (OSI and TMSL)."""

    _EXPECTED_REASON_FRAGMENT = "requires re-partitioning the aggregate"

    def test_allexcept_fails_closed_via_osi_pipeline(self):
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

        assert metric.sync_enabled is False
        assert metric.sql_expression is None
        assert metric.sync_failure_reason is not None
        assert self._EXPECTED_REASON_FRAGMENT in metric.sync_failure_reason
        assert " OVER " not in f" {metric.sync_failure_reason} "

    def test_all_fails_closed_via_direct_translate_call(self):
        from semabridge.converter.dax_translator import DAXTranslator

        translator = DAXTranslator()
        dax = "CALCULATE(SUM('SomeTable'[SomeColumn]), ALL('SomeTable'))"

        complexity = translator.analyze_complexity(dax)
        assert complexity["sync_enabled"] is False
        assert self._EXPECTED_REASON_FRAGMENT in complexity["failure_reason"]

        result = translator.translate(dax, table_alias="sometable", dataset_name="SomeTable")
        assert result.is_success is False
        assert result.sql is None


class TestUnquotedTableQualifiedColumnParses:
    """DAX allows omitting quotes around a table name with no spaces
    (TableName[Column], not just 'Table Name'[Column]) — the parser used
    to only recognize the quoted form, silently mis-splitting the unquoted
    form into two separate tokens (found while generalizing the two
    hardcoded fixture translators removed from dax_rule_translator.py:
    both targeted DAX that used exactly this unquoted syntax)."""

    def test_unquoted_table_column_used_as_calculate_filter_resolves(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = (
            "CALCULATE(SUM('SomeTable'[SomeColumn]), "
            'SomeOtherTable[SomeFlag]="Yes")'
        )
        sql = try_ast_translate(dax, table_alias="sometable")
        assert sql is not None
        assert "SOMEFLAG" in sql.upper()
        assert "= 'Yes'" in sql

    def test_unquoted_and_quoted_table_column_forms_produce_identical_sql(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        unquoted = 'CALCULATE(SUM(\'SomeTable\'[SomeColumn]), SomeOtherTable[SomeFlag]="Yes")'
        quoted = "CALCULATE(SUM('SomeTable'[SomeColumn]), 'SomeOtherTable'[SomeFlag]=\"Yes\")"
        assert try_ast_translate(unquoted, table_alias="sometable") == try_ast_translate(
            quoted, table_alias="sometable"
        )


class TestCalculateFilterGeneralizesToMeasureReferenceBase:
    """CALCULATE(<measure reference>, <filter>) — e.g. the shape behind the
    removed translate_sentiment_gap fixture, a difference of two such
    CALCULATE blocks — used to only work when CALCULATE's first argument
    was a direct aggregate call (SUM(...) etc.); a bare measure reference
    raised instead of falling back to injecting the filter into the
    measure's own already-resolved aggregate, the same fallback already
    used for TOTALYTD/lag-period measure-reference bases."""

    def test_calculate_of_measure_reference_with_filter_resolves(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = 'CALCULATE([Measure_A], SomeOtherTable[SomeFlag]="Yes")'
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            measure_sql_map={"Measure_A": 'AVG(sometable."SOMECOLUMN")'},
            known_measure_names={"Measure_A"},
        )
        assert sql is not None
        assert "CASE WHEN" in sql.upper()
        assert "SOMEFLAG" in sql.upper()
        assert "= 'Yes'" in sql
        assert "AVG(" in sql.upper()

    def test_difference_of_two_filtered_measure_references_preserves_order(self):
        """The exact shape translate_sentiment_gap targeted — and the exact
        bug it had: it discarded which side of the subtraction was which,
        always emitting a fixed order regardless of the real DAX. The
        general fix must not repeat that — the "No" filter must render on
        the side the DAX actually put it on."""
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = (
            'CALCULATE([Measure_A], SomeOtherTable[SomeFlag]="No") - '
            'CALCULATE([Measure_A], SomeOtherTable[SomeFlag]="Yes")'
        )
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            measure_sql_map={"Measure_A": 'AVG(sometable."SOMECOLUMN")'},
            known_measure_names={"Measure_A"},
        )
        assert sql is not None
        left, _, right = sql.partition(" - ")
        assert "'No'" in left
        assert "'Yes'" in right

    def test_calculate_of_unresolved_measure_reference_still_fails_closed(self):
        """A measure reference the caller has no resolved SQL for yet must
        still fail closed (defer to a later pass), not be silently treated
        as a column — the fallback must not weaken this existing guard."""
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = 'CALCULATE([Measure_A], SomeOtherTable[SomeFlag]="Yes")'
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            known_measure_names={"Measure_A"},  # known, but absent from measure_sql_map
        )
        assert sql is None

    def test_isblank_guarded_difference_resolves_with_correct_null_semantics(self):
        """The exact real shape behind translate_sentiment_gap: an
        IF(ISBLANK(...)||ISBLANK(...), BLANK(), <difference>) null-guard
        wrapper around the CALCULATE-of-measure-reference difference.
        BLANK() had no renderer support at all (found while verifying this
        exact real shape) — a trivial, universal BLANK()->NULL mapping,
        not a fixture. The old hardcoded fixture silently DROPPED this
        null-guard entirely (its regex only pattern-matched the inner
        difference and ignored everything around it) — the general fix
        must actually preserve the guard's semantics, not just match more
        inputs."""
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = (
            'IF(ISBLANK(CALCULATE([Measure_A], SomeOtherTable[SomeFlag]="No"))'
            '||ISBLANK(CALCULATE([Measure_A], SomeOtherTable[SomeFlag]="Yes")), '
            'BLANK(), CALCULATE([Measure_A], SomeOtherTable[SomeFlag]="No") - '
            'CALCULATE([Measure_A], SomeOtherTable[SomeFlag]="Yes"))'
        )
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            measure_sql_map={"Measure_A": 'AVG(sometable."SOMECOLUMN")'},
            known_measure_names={"Measure_A"},
        )
        assert sql is not None
        assert sql.upper().startswith("CASE WHEN")
        assert "IS NULL" in sql.upper()
        assert "THEN NULL ELSE" in sql.upper()
        assert "'No'" in sql and "'Yes'" in sql


class TestLagPeriodOfMeasureReferenceShiftsWholeWindow:
    """CALCULATE([SomeMeasure], SAMEPERIODLASTYEAR/PREVIOUSYEAR/PREVIOUSMONTH/
    PREVIOUSQUARTER(...)) — e.g. the exact shape behind the
    TOTAL_UNITS_YTD_SPLY bug. translate_sameperiodlastyear used to emit a
    bare reference to the wrapped measure by name (table_alias."MeasureName"),
    which Snowflake's semantic-view engine rejects with "a metric must
    directly refer to another aggregate-level expression" — because that
    is genuinely what the SQL did. The general fix (inlining the
    referenced measure's own resolved SQL, then shifting its date anchor)
    must never regress to that bare reference for any of the four
    lag-period functions, and the shift must actually move the WHOLE
    window back — not leave a stale, unshifted inner bound that
    contradicts a freshly-shifted outer one (confirmed, via real SQL
    execution against synthetic data below, this is not just "doesn't
    crash")."""

    _MEASURE_SQL_MAP = {
        "Measure_A": (
            'SUM(CASE WHEN calendar."COL_DATE" >= DATE_TRUNC(\'YEAR\', sometable."MAX_DATE") '
            'AND calendar."COL_DATE" <= sometable."MAX_DATE" THEN sometable."UNITS"::FLOAT END)'
        )
    }

    @staticmethod
    def _lag_dax(func: str) -> str:
        return f"CALCULATE([Measure_A], {func}('Date'[Date]))"

    def test_none_of_the_four_lag_functions_ever_emit_a_bare_metric_name_reference(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        for func in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER"):
            sql = try_ast_translate(
                self._lag_dax(func),
                table_alias="sometable",
                date_alias="calendar",
                measure_sql_map=self._MEASURE_SQL_MAP,
                known_measure_names={"Measure_A"},
            )
            assert sql is not None, f"{func}: expected a resolved translation"
            # The bug this replaces: a bare qualified reference to the
            # wrapped measure's OWN name, e.g. sometable."Measure_A" —
            # must never appear; the measure's real underlying SQL
            # (SUM(...UNITS...)) must be inlined instead.
            assert '"Measure_A"' not in sql, f"{func}: bare metric-name reference leaked into {sql!r}"
            assert "UNITS" in sql.upper(), f"{func}: expected the inlined measure's own column, got {sql!r}"

    def test_rule_engine_also_never_emits_the_bare_reference_for_any_of_the_four(self):
        """translate_sameperiodlastyear itself (the rule engine, which used
        to be the actual source of the bug) must decline unconditionally
        now — for every one of the four functions, not just the one named
        in its own name."""
        from semabridge.converter.dax_rule_translator import rule_based_translation

        for func in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER"):
            result = rule_based_translation(self._lag_dax(func), "sometable")
            assert result is None or '"Measure_A"' not in result

    def test_shifted_window_executes_and_returns_the_correct_prior_year_sum(self):
        """Real SQL execution (DuckDB) against synthetic rows spanning two
        years — not just "doesn't crash". Proves the shifted window
        actually selects last year's rows (non-empty, correct sum) and not
        an empty/self-contradictory range."""
        import re
        import duckdb
        from semabridge.converter.dax_ast_parser import try_ast_translate

        sql = try_ast_translate(
            self._lag_dax("SAMEPERIODLASTYEAR"),
            table_alias="sometable",
            date_alias="calendar",
            measure_sql_map=self._MEASURE_SQL_MAP,
            known_measure_names={"Measure_A"},
        )
        assert sql is not None

        # Snowflake DATEADD(unit, n, expr) -> DuckDB (expr +/- INTERVAL n unit).
        # A test-only dialect shim, not a production code path.
        def _to_duckdb_dateadd(m: re.Match) -> str:
            unit, amount, expr = m.group(1), int(m.group(2)), m.group(3)
            sign = "-" if amount < 0 else "+"
            return f"({expr} {sign} INTERVAL {abs(amount)} {unit})"

        duckdb_sql = re.sub(
            r"DATEADD\s*\(\s*(YEAR|QUARTER|MONTH)\s*,\s*(-?\d+)\s*,\s*([^()]+(?:\([^()]*\)[^()]*)*)\)",
            _to_duckdb_dateadd,
            sql,
        )
        # Substitute the scalar MAX_DATE anchor with a literal (it's a
        # precomputed, per-scan-constant column in the real enriched view;
        # a literal is the accurate equivalent for a single test dataset).
        duckdb_sql = duckdb_sql.replace('sometable."MAX_DATE"', "DATE '2024-06-15'")
        duckdb_sql = duckdb_sql.replace('calendar."COL_DATE"', "combined.col_date")
        duckdb_sql = duckdb_sql.replace('sometable."UNITS"', "combined.units")

        con = duckdb.connect()
        con.execute("CREATE TABLE combined (col_date DATE, units DOUBLE)")
        con.executemany(
            "INSERT INTO combined VALUES (?, ?)",
            [
                ("2023-02-01", 10.0),   # last year's YTD window -> should count
                ("2023-05-01", 20.0),   # last year's YTD window -> should count
                ("2023-08-01", 999.0),  # last year, AFTER last year's MAX_DATE-equivalent -> must NOT count
                ("2024-03-01", 500.0),  # THIS year -> must NOT count (this is the exact bug: an
                                        # unshifted inner bound would wrongly include this)
            ],
        )
        result = con.execute(f"SELECT {duckdb_sql} FROM combined").fetchone()[0]
        assert result is not None, "shifted window must not be empty/NULL for a plausible dataset"
        assert result == 30.0, f"expected only the two last-year-YTD rows (10+20=30), got {result}"

    def test_unresolved_referenced_measure_fails_closed_with_a_real_reason(self):
        """If the wrapped measure hasn't been resolved yet (absent from
        measure_sql_map), there is nothing to inline — must fail closed
        (defer to a later pass), and the DropLedger reason must say why,
        not just "DAX translation failed"."""
        from semabridge.converter.dax_ast_parser import (
            try_ast_translate,
            dax_lag_period_of_measure_reference_failure_reason,
        )

        dax = self._lag_dax("SAMEPERIODLASTYEAR")
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            date_alias="calendar",
            known_measure_names={"Measure_A"},  # known, but absent from measure_sql_map
        )
        assert sql is None

        reason = dax_lag_period_of_measure_reference_failure_reason(dax)
        assert reason is not None
        assert "Measure_A" in reason
        assert "SAMEPERIODLASTYEAR" in reason.upper() or "PREVIOUS" in reason.upper()

    def test_unrelated_dax_shape_is_not_flagged_by_the_classifier(self):
        from semabridge.converter.dax_ast_parser import dax_lag_period_of_measure_reference_failure_reason

        assert dax_lag_period_of_measure_reference_failure_reason("SUM('SomeTable'[SomeColumn])") is None
        # A lag-period function wrapping a DIRECT aggregate (not a measure
        # reference) resolves through a completely different, already-working
        # branch — must not be misclassified as the unresolvable-measure case.
        assert dax_lag_period_of_measure_reference_failure_reason(
            "CALCULATE(SUM('SomeTable'[SomeColumn]), SAMEPERIODLASTYEAR('Date'[Date]))"
        ) is None


class TestLagPeriodOfMeasureReferenceGeneralizesToAnyCombiningStructure:
    """_inject_case_filter_into_rendered_aggregate used to only handle a
    bare aggregate or a top-level '+'/'-' split (e.g. [A] - [B]) — a
    ratio measure like [% Units Market Share] = IF([Total]=0, 0,
    DIVIDE([VanArsdel], [Total])) declined outright when wrapped in
    SAMEPERIODLASTYEAR/PREVIOUSYEAR/etc, the exact real shape behind
    '% Units Market Share SPLY' failing with a generic "not automatically
    translatable" reason. The general fix finds and filters every leaf
    SUM/AVG/MIN/MAX/COUNT call wherever it appears, leaving the
    surrounding IF/CASE/division structure untouched — this is
    mathematically correct for any combination of same-grain aggregates,
    not just addition/subtraction."""

    def test_division_only_base_measure_resolves(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "CALCULATE([Market_Share], SAMEPERIODLASTYEAR('Date'[Date]))"
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            measure_sql_map={
                "Market_Share": 'SUM(sometable."VAN_UNITS"::FLOAT) / SUM(sometable."TOTAL_UNITS"::FLOAT)'
            },
            known_measure_names={"Market_Share"},
        )
        assert sql is not None
        assert '"Market_Share"' not in sql
        # Both the numerator and denominator aggregates must be
        # independently filtered -- not just one side, and not the
        # whole ratio wrapped in one outer aggregate.
        assert sql.upper().count("SUM(CASE WHEN") == 2
        assert "VAN_UNITS" in sql.upper() and "TOTAL_UNITS" in sql.upper()

    def test_if_guarded_ratio_resolves_the_exact_pct_units_market_share_shape(self):
        """The exact real shape behind '% Units Market Share SPLY'
        failing: IF(<agg>=0, 0, DIVIDE(<agg>, <agg>)). All three
        aggregate leaves (the guard's own check, and both sides of the
        division) must be filtered identically."""
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "CALCULATE([Market_Share], SAMEPERIODLASTYEAR('Date'[Date]))"
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            measure_sql_map={
                "Market_Share": (
                    'CASE WHEN SUM(sometable."TOTAL_UNITS"::FLOAT) = 0 THEN 0 '
                    'ELSE SUM(sometable."VAN_UNITS"::FLOAT) / SUM(sometable."TOTAL_UNITS"::FLOAT) END'
                )
            },
            known_measure_names={"Market_Share"},
        )
        assert sql is not None
        assert sql.upper().count("SUM(CASE WHEN") == 3
        assert "VAN_UNITS" in sql.upper() and "TOTAL_UNITS" in sql.upper()
        # The IF/CASE guard structure itself must survive untouched.
        assert "= 0 THEN 0 ELSE" in sql

    def test_if_guarded_ratio_executes_and_returns_the_correct_prior_year_value(self):
        """Real SQL execution (DuckDB), not just "doesn't crash" — proves
        the shifted, leaf-filtered ratio actually computes the correct
        prior-year value and excludes the current year's row, the same
        standard test_shifted_window_executes_and_returns_the_correct_
        prior_year_sum above already holds the plain-aggregate case to."""
        import re
        import duckdb
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "CALCULATE([Market_Share], SAMEPERIODLASTYEAR('Date'[Date]))"
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            date_alias="calendar",
            measure_sql_map={
                "Market_Share": (
                    'CASE WHEN SUM(sometable."TOTAL_UNITS"::FLOAT) = 0 THEN 0 '
                    'ELSE SUM(sometable."VAN_UNITS"::FLOAT) / SUM(sometable."TOTAL_UNITS"::FLOAT) END'
                )
            },
            known_measure_names={"Market_Share"},
        )
        assert sql is not None

        sql = sql.replace("CURRENT_DATE()", "DATE '2024-06-15'")

        def _convert_dateadd(expr: str) -> str:
            out, i = [], 0
            pattern = re.compile(r"DATEADD\s*\(")
            while True:
                m = pattern.search(expr, i)
                if not m:
                    out.append(expr[i:])
                    break
                out.append(expr[i:m.start()])
                depth, j = 0, m.end() - 1
                start_args = m.end()
                while True:
                    if expr[j] == "(":
                        depth += 1
                    elif expr[j] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                unit, amount, inner_expr = [p.strip() for p in expr[start_args:j].split(",", 2)]
                sign = "-" if int(amount) < 0 else "+"
                out.append(f"({inner_expr} {sign} INTERVAL {abs(int(amount))} {unit})")
                i = j + 1
            return "".join(out)

        sql = _convert_dateadd(sql)
        sql = sql.replace('calendar."COL_DATE"', "combined.col_date")
        sql = sql.replace('sometable."TOTAL_UNITS"', "combined.total_units")
        sql = sql.replace('sometable."VAN_UNITS"', "combined.van_units")

        con = duckdb.connect()
        con.execute("CREATE TABLE combined (col_date DATE, total_units DOUBLE, van_units DOUBLE)")
        con.executemany(
            "INSERT INTO combined VALUES (?, ?, ?)",
            [
                ("2023-02-01", 100.0, 40.0),   # last year -> counts
                ("2023-05-01", 100.0, 30.0),   # last year -> counts (running: 70/200)
                ("2024-03-01", 999.0, 999.0),  # THIS year -> must NOT count
            ],
        )
        result = con.execute(f"SELECT {sql} FROM combined").fetchone()[0]
        assert result is not None
        assert abs(result - 0.35) < 1e-9, f"expected 70/200=0.35, got {result}"

    def test_nested_aggregate_still_fails_closed(self):
        """A rendered SQL where one aggregate's own argument contains
        ANOTHER aggregate call (e.g. a mistaken SUM(AVG(x))) must still be
        declined, not rewritten -- this shape means it isn't simple
        leaf-level aggregation, and guessing would risk producing
        SUM(...SUM(...)...), which Snowflake disallows."""
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "CALCULATE([Weird_Measure], SAMEPERIODLASTYEAR('Date'[Date]))"
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            measure_sql_map={"Weird_Measure": 'SUM(AVG(sometable."SOMECOLUMN"))'},
            known_measure_names={"Weird_Measure"},
        )
        assert sql is None


class TestLagPeriodUnshiftedFallbackIsOptInOnly:
    """allow_unshifted_fallback lets a lag-period-of-measure-reference
    shape this renderer can't safely date-filter (e.g. a nested aggregate)
    ship the referenced measure's own unmodified SQL instead of failing
    closed -- valid SQL, but the CURRENT period's value, not shifted to the
    requested prior period. Must be strictly opt-in: every existing caller
    (allow_unshifted_fallback defaulted/omitted) keeps failing closed
    exactly as before."""

    _NESTED_AGG_DAX = "CALCULATE([Weird_Measure], SAMEPERIODLASTYEAR('Date'[Date]))"
    _NESTED_AGG_MAP = {"Weird_Measure": 'SUM(AVG(sometable."SOMECOLUMN"))'}

    def test_default_still_fails_closed_unchanged(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        sql = try_ast_translate(
            self._NESTED_AGG_DAX,
            table_alias="sometable",
            measure_sql_map=self._NESTED_AGG_MAP,
            known_measure_names={"Weird_Measure"},
        )
        assert sql is None

    def test_opted_in_ships_the_unshifted_base_sql_and_records_the_advisory(self):
        from semabridge.converter.dax_ast_parser import (
            try_ast_translate,
            ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK,
        )

        advisories = []
        sql = try_ast_translate(
            self._NESTED_AGG_DAX,
            table_alias="sometable",
            measure_sql_map=self._NESTED_AGG_MAP,
            known_measure_names={"Weird_Measure"},
            allow_unshifted_fallback=True,
            advisory_categories=advisories,
        )
        assert sql is not None
        assert 'SUM(AVG(sometable."SOMECOLUMN"))' in sql
        # Genuinely unshifted -- no CASE WHEN date-range filter was injected.
        assert "CASE WHEN" not in sql
        assert advisories == [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK]

    def test_cache_hit_still_replays_the_advisory_category(self):
        """The module-level AST cache must not let the advisory flag get
        silently dropped on a second call with a fresh advisory_categories
        list -- only the render pass that first produced this cache entry
        would otherwise ever populate it."""
        from semabridge.converter.dax_ast_parser import (
            try_ast_translate,
            ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK,
        )

        first = []
        try_ast_translate(
            self._NESTED_AGG_DAX,
            table_alias="sometable",
            measure_sql_map=self._NESTED_AGG_MAP,
            known_measure_names={"Weird_Measure"},
            allow_unshifted_fallback=True,
            advisory_categories=first,
        )
        second = []
        sql = try_ast_translate(
            self._NESTED_AGG_DAX,
            table_alias="sometable",
            measure_sql_map=self._NESTED_AGG_MAP,
            known_measure_names={"Weird_Measure"},
            allow_unshifted_fallback=True,
            advisory_categories=second,
        )
        assert sql is not None
        assert second == [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK]

    def test_same_dax_without_fallback_is_unaffected_by_a_prior_fallback_call(self):
        """The cache key must distinguish allow_unshifted_fallback=True
        from False for otherwise-identical calls -- a prior opted-in call
        populating the cache must never leak its result to a caller that
        did not opt in."""
        from semabridge.converter.dax_ast_parser import try_ast_translate

        try_ast_translate(
            self._NESTED_AGG_DAX,
            table_alias="sometable",
            measure_sql_map=self._NESTED_AGG_MAP,
            known_measure_names={"Weird_Measure"},
            allow_unshifted_fallback=True,
            advisory_categories=[],
        )
        sql = try_ast_translate(
            self._NESTED_AGG_DAX,
            table_alias="sometable",
            measure_sql_map=self._NESTED_AGG_MAP,
            known_measure_names={"Weird_Measure"},
        )
        assert sql is None


class TestTotalPeriodToDateOfMeasureReferenceNeverLeaksBareName:
    """TOTALYTD/TOTALMTD/TOTALQTD([SomeMeasure], 'Date'[Date]) — found while
    reconciling Item 4 against a real deploy: translate_time_intelligence_with_anchors
    (the rule engine's TOTALYTD/MTD/QTD handler) had the exact same bug as
    translate_sameperiodlastyear — when its first argument wasn't a direct
    SUM(...)-style aggregate call, it fell back to treating the bracket
    name as if it were a physical column (table_alias."SomeMeasure"),
    silently emitting a bare reference to another metric by name. This is
    what actually produced TOTAL_UNITS_YTD's wrong SQL in the real deploy
    (and, because that "succeeded", permanently blocked
    _resolve_metric_dependencies's later, context-aware pass from ever
    correctly re-resolving it)."""

    def test_rule_engine_declines_for_a_measure_reference_for_all_three_functions(self):
        from semabridge.converter.dax_rule_translator import translate_time_intelligence_with_anchors

        for func in ("TOTALYTD", "TOTALMTD", "TOTALQTD"):
            dax = f"{func}([Measure_A], 'Date'[Date])"
            result = translate_time_intelligence_with_anchors(dax, "sometable")
            assert result is None, f"{func}: must decline a measure reference, not guess a column"
            assert '"Measure_A"' not in (result or "")

    def test_rule_engine_still_resolves_a_direct_aggregate_for_all_three_functions(self):
        """Declining for measure references must not regress the
        legitimate, unambiguous shape this function was built for."""
        from semabridge.converter.dax_rule_translator import translate_time_intelligence_with_anchors

        for func in ("TOTALYTD", "TOTALMTD", "TOTALQTD"):
            dax = f"{func}(SUM('SomeTable'[SomeColumn]), 'Date'[Date])"
            result = translate_time_intelligence_with_anchors(dax, "sometable")
            assert result is not None, f"{func}: direct aggregate call must still resolve"
            assert "SOMECOLUMN" in result.upper()

    def test_measure_reference_resolves_generally_via_ast_renderer_instead(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "TOTALYTD([Measure_A], 'Date'[Date])"
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            date_alias="calendar",
            measure_sql_map={"Measure_A": 'SUM(sometable."SOMECOLUMN")'},
            known_measure_names={"Measure_A"},
        )
        assert sql is not None
        assert '"Measure_A"' not in sql
        assert "SOMECOLUMN" in sql.upper()


class TestTimeIntelligenceAnchorsUseNativeExpressionsNotSyntheticColumns:
    """translate_time_intelligence_with_anchors and translate_rolling_12_months
    used to reference synthetic enriched-view anchor columns (MAX_DATE,
    MAX_MONTHINDEX) that either aren't in scope inside a semantic view's
    METRICS clause (MAX_DATE) or are never actually computed anywhere in
    the enriched-view builder at all (MAX_MONTHINDEX). Both now use native
    CURRENT_DATE()-derived expressions instead — matching the same fix
    already applied to connectors/translator.py's parallel TOTALYTD/
    SAMEPERIODLASTYEAR implementation (commit e4c8322), which this file's
    copy had never been synced to."""

    def test_totalytd_uses_current_date_not_synthetic_max_date_anchor(self):
        from semabridge.converter.dax_rule_translator import translate_time_intelligence_with_anchors

        dax = "TOTALYTD(SUM('SomeTable'[SomeColumn]), 'Date'[Date])"
        result = translate_time_intelligence_with_anchors(dax, "sometable")
        assert result is not None
        assert "MAX_DATE" not in result.upper()
        assert "CURRENT_DATE()" in result

    def test_rolling_12_months_uses_native_month_index_not_synthetic_anchor(self):
        from semabridge.converter.dax_rule_translator import translate_rolling_12_months

        dax = (
            "SUM([SomeColumn]) WHERE MonthIndex <= MAX(AnythingHere) "
            "AND MonthIndex > MAX(AnythingHere) - 12"
        )
        result = translate_rolling_12_months(dax, "sometable")
        assert result is not None
        assert "MAX_MONTHINDEX" not in result.upper()
        assert "EXTRACT(YEAR FROM CURRENT_DATE())" in result
        assert "EXTRACT(MONTH FROM CURRENT_DATE())" in result
        assert "SOMECOLUMN" in result.upper()


class TestDivideOperandNeverLeaksBareMeasureName:
    """DIVIDE([Numerator], [Denominator]) — found alongside the TOTALYTD
    bug during the same real-deploy reconciliation: _resolve_divide_operand_sql
    had the identical defect — an operand that wasn't a direct aggregate
    call fell back to SUM(table_alias.name), i.e. a bare reference to
    another metric by name if the operand happened to be a measure rather
    than a column (confirmed live: PCT_UNITS_MARKET_SHARE, which divides
    two other named measures, deployed as CAST(NULL AS DOUBLE))."""

    def test_bare_operand_declines_rather_than_guessing_a_column(self):
        from semabridge.converter.dax_rule_translator import _resolve_divide_operand_sql

        assert _resolve_divide_operand_sql("Some Measure Name", "sometable") is None

    def test_explicit_aggregate_operand_still_resolves(self):
        from semabridge.converter.dax_rule_translator import _resolve_divide_operand_sql

        sql = _resolve_divide_operand_sql("SUM('SomeTable'[SomeColumn])", "sometable")
        assert sql is not None
        assert "SOMECOLUMN" in sql.upper()

    def test_divide_of_measure_references_resolves_generally_via_ast_renderer(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "DIVIDE([Measure_A], [Measure_B], 0)"
        sql = try_ast_translate(
            dax,
            table_alias="sometable",
            measure_sql_map={
                "Measure_A": 'SUM(sometable."NUM_COL")',
                "Measure_B": 'SUM(sometable."DEN_COL")',
            },
            known_measure_names={"Measure_A", "Measure_B"},
        )
        assert sql is not None
        assert "NUM_COL" in sql.upper() and "DEN_COL" in sql.upper()
        assert '"Measure_A"' not in sql and '"Measure_B"' not in sql


class TestIteratorGeneralizesToFilteredRowSet:
    """SUMX/AVERAGEX/MINX/MAXX(FILTER(table, predicate), expr) — e.g. the
    shape behind the removed translate_vanarsdel_flag fixture — is
    mathematically equivalent to reducing over a CASE-gated full row-set
    for a predicate made of simple, non-aggregating per-row comparisons
    ANDed together. Must generalize to any table/column/value, and must
    fail closed (not guess) for anything more complex than that shape."""

    def test_sumx_of_filtered_table_generalizes_to_any_table_column_value(self):
        from semabridge.converter.dax_rule_translator import translate_iterator

        dax = 'SUMX(FILTER(SomeTable, SomeTable[SomeFlag] = "SomeValue"), [SomeColumn])'
        sql = translate_iterator(dax, "sometable")
        assert sql is not None
        assert sql == 'SUM(CASE WHEN SOMETABLE."SOMEFLAG" = \'SomeValue\' THEN SOMETABLE."SOMECOLUMN"::FLOAT ELSE 0 END)'

    def test_averagex_of_filtered_table_uses_avg_not_sum(self):
        from semabridge.converter.dax_rule_translator import translate_iterator

        dax = 'AVERAGEX(FILTER(SomeTable, SomeTable[SomeFlag] = "SomeValue"), [SomeColumn])'
        sql = translate_iterator(dax, "sometable")
        assert sql is not None
        assert sql.startswith("AVG(CASE WHEN")

    def test_sumx_of_filter_with_cross_table_predicate_generalizes(self):
        """The exact shape translate_vanarsdel_flag hardcoded a single
        table/column for — must now work for a filter table/column that
        has nothing to do with the historical PRODUCT.ISVANARSDEL case."""
        from semabridge.converter.dax_rule_translator import translate_iterator

        dax = 'SUMX(FILTER(FactTable, DimensionTable[Category] = "Electronics"), [Revenue])'
        sql = translate_iterator(dax, "facttable")
        assert sql is not None
        assert "DIMENSIONTABLE" in sql
        assert "CATEGORY" in sql
        assert "'Electronics'" in sql

    def test_sumx_of_filter_with_or_predicate_fails_closed(self):
        """An OR-joined predicate can't be reduced to a single ANDed CASE
        WHEN without changing its meaning — must decline, not guess."""
        from semabridge.converter.dax_rule_translator import translate_iterator

        dax = (
            'SUMX(FILTER(SomeTable, SomeTable[A] = "X" OR SomeTable[B] = "Y"), '
            "[SomeColumn])"
        )
        assert translate_iterator(dax, "sometable") is None

    def test_sumx_of_filter_with_cross_row_aggregation_predicate_fails_closed(self):
        """A predicate that itself depends on a cross-row aggregation
        (e.g. comparing against a CALCULATE'd average) is not a per-row
        comparison — reducing it to a scalar CASE WHEN would silently
        change what the expression computes. Must decline."""
        from semabridge.converter.dax_rule_translator import translate_iterator

        dax = (
            "SUMX(FILTER(SomeTable, SomeTable[SomeColumn] > "
            "CALCULATE(AVERAGE(SomeTable[SomeColumn]))), [SomeColumn])"
        )
        assert translate_iterator(dax, "sometable") is None

    def test_sumx_of_filter_with_nested_filter_predicate_fails_closed(self):
        """A predicate containing its own nested FILTER(...) is not a
        simple per-row comparison either — must decline for the same
        reason as the cross-row-aggregation case."""
        from semabridge.converter.dax_rule_translator import translate_iterator

        dax = (
            "SUMX(FILTER(SomeTable, COUNTX(FILTER(OtherTable, "
            'OtherTable[Flag] = "Yes"), OtherTable[Amount]) > 0), [SomeColumn])'
        )
        assert translate_iterator(dax, "sometable") is None

    def test_sumx_without_filter_wrapper_is_unaffected(self):
        """The pre-existing, simpler SUMX(table, expr) shape (no FILTER)
        must keep working exactly as before."""
        from semabridge.converter.dax_rule_translator import translate_iterator

        dax = "SUMX(SomeTable, [ColumnA] * [ColumnB])"
        sql = translate_iterator(dax, "sometable")
        assert sql is not None
        assert "CASE WHEN" not in sql.upper()
        assert "SOMETABLE.\"COLUMNA\"" in sql.upper()


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
    ANY pair of *unambiguous, direct-aggregate* operands — not just one
    hardcoded pair. This is a regression test for a bug where the
    rule-based DIVIDE translator only produced a real division when the
    DAX literally named one specific historical measure pair, silently
    returning numerator-only SQL (or None) for every other DIVIDE-shaped
    expression.

    A bare, unqualified operand name (e.g. [SomeMeasure]) is a *different*,
    later-found bug, not a case this class asserts must "just work": this
    rule-engine function (a pure text-pattern rule, like every other
    function in dax_rule_translator.py) has no measure registry or schema
    to check against, so it can't tell "this is a column to sum" apart
    from "this is a reference to another measure" — guessing the former
    used to silently emit a bare reference to the measure by name, which
    Snowflake's semantic-view engine rejects with "a metric must directly
    refer to another aggregate-level expression" (confirmed via a real
    live deploy — see TOTAL_UNITS_YTD/PCT_UNITS_MARKET_SHARE). It must now
    decline for that ambiguous shape and defer to
    dax_ast_parser.py's DaxSqlRenderer, which has the measure registry to
    resolve it correctly (see TestCalculateFilterGeneralizesToMeasureReferenceBase
    for that general-path coverage)."""

    def test_divide_of_bare_ambiguous_names_declines(self):
        """The rule engine has no way to tell a bare [Name] apart from a
        reference to another measure — it must decline rather than guess a
        column, for any pair of names, not just the one shape found live."""
        from semabridge.converter.dax_rule_translator import translate_divide_measures, rule_based_translation

        dax = "DIVIDE([Placeholder Numerator Measure], [Placeholder Denominator Measure], 0)"
        assert translate_divide_measures(dax, "fact") is None
        assert rule_based_translation(dax, "fact") is None

    def test_divide_of_bare_names_resolves_generally_via_ast_renderer(self):
        """Declining in the rule engine isn't a capability loss — the
        general AST path resolves the exact same DIVIDE-of-bare-names shape
        correctly, for any pair, whether the names turn out to be physical
        columns (no measure-registry knowledge at all, the common case) or
        other measures (resolved via measure_sql_map, or failed closed
        until they are)."""
        from semabridge.converter.dax_ast_parser import try_ast_translate

        # Case 1: bare names with no measure-registry knowledge at all ->
        # falls back to column references, same as every other bracket
        # reference this renderer has never had schema/measure context for.
        dax = "DIVIDE([SomeNumeratorColumn], [SomeDenominatorColumn], 0)"
        sql = try_ast_translate(dax, table_alias="fact")
        assert sql is not None
        assert sql.upper().startswith("DIV0(")  # DAX DIVIDE's own null-safe semantics, not a bare '/'
        assert "SOMENUMERATORCOLUMN" in sql.upper() and "SOMEDENOMINATORCOLUMN" in sql.upper()

        # Case 2: bare names that ARE tracked measures -> inlines their own
        # resolved SQL instead of guessing a column.
        dax2 = "DIVIDE([Placeholder Numerator Measure], [Placeholder Denominator Measure], 0)"
        sql2 = try_ast_translate(
            dax2,
            table_alias="fact",
            measure_sql_map={
                "Placeholder Numerator Measure": 'SUM(fact."NUM_COL")',
                "Placeholder Denominator Measure": 'SUM(fact."DEN_COL")',
            },
            known_measure_names={"Placeholder Numerator Measure", "Placeholder Denominator Measure"},
        )
        assert sql2 is not None
        assert "NUM_COL" in sql2.upper() and "DEN_COL" in sql2.upper()

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


class TestMeasureReferenceMatchingIsCaseInsensitive:
    """DAX/Analysis Services measure-name resolution is case-insensitive —
    [TOTAL UNITS] and [Total Units] refer to the same measure regardless of
    how either bracket reference happens to be cased. _render_measure_ref's
    lookups against measure_sql_map/known_measure_names must honor that, or
    a perfectly valid, unremarkable real-world model (one formula typed in
    a different case than the measure's own registered name) permanently
    fails to resolve for reasons that have nothing to do with DAX grammar.

    Deliberately scoped to case only, not whitespace/underscore — that is
    a distinct, separately-handled concern (mapping-override rename
    sequencing), and conflating the two would risk matching genuinely
    different measures that only happen to differ by spacing.
    """

    def test_resolved_measure_ref_matches_despite_different_case(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "[PLACEHOLDER OTHER MEASURE] + 1"
        sql = try_ast_translate(
            dax,
            table_alias="fact",
            date_alias="COL_DATE",
            measure_sql_map={"Placeholder Other Measure": 'SUM(fact."SOME_COLUMN")'},
        )

        assert sql is not None, "a case-only difference must not block resolution"
        assert 'SUM(fact."SOME_COLUMN")' in sql
        assert '"PLACEHOLDER OTHER MEASURE"' not in sql.upper(), (
            f"measure reference leaked as a column reference: {sql!r}"
        )

    def test_known_but_unresolved_measure_ref_fails_closed_despite_different_case(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "[PLACEHOLDER OTHER MEASURE] + 1"
        sql = try_ast_translate(
            dax,
            table_alias="fact",
            date_alias="COL_DATE",
            measure_sql_map={},
            known_measure_names={"Placeholder Other Measure"},
        )

        assert sql is None, (
            f"case-insensitively known-but-unresolved measure ref must still fail closed: {sql!r}"
        )

    def test_unrelated_name_still_falls_back_to_column_regardless_of_case(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "[SomeColumn] + 1"
        sql = try_ast_translate(
            dax,
            table_alias="fact",
            date_alias="COL_DATE",
            measure_sql_map={"Placeholder Other Measure": 'SUM(fact."SOME_COLUMN")'},
            known_measure_names={"Placeholder Other Measure"},
        )

        assert sql is not None, "a name outside the registry entirely must still fall back to a column ref"
        assert 'fact."SOMECOLUMN"' in sql, (
            f"expected the pre-existing column-reference fallback to be preserved: {sql!r}"
        )


class TestDateAliasDefaultMatchesEstablishedSnowflakeConvention:
    """The Date/Calendar dimension's default alias must be consistent
    everywhere it's assumed, not guessed separately per module. Before this
    fix, dax_translator.py/dax_ast_parser.py defaulted to "CALENDAR" while
    dax_rule_translator.py's own _date_alias() (and the Snowflake emitter's
    "Map common DAX name -> physical name (e.g. Date -> COL_DATE)" naming
    convention) already used "COL_DATE" — so a TOTALYTD/SAMEPERIODLASTYEAR
    expression resolved through the AST renderer produced SQL referencing a
    table alias ("CALENDAR") the deployed semantic view never declares,
    silently rejected at DDL-emission time in favor of CAST(NULL AS DOUBLE).
    """

    def test_dax_translator_default_date_alias_is_col_date(self):
        translator = DAXTranslator()
        assert translator._get_date_alias() == "COL_DATE"

    def test_try_ast_translate_default_date_alias_is_col_date(self):
        from semabridge.converter.dax_ast_parser import try_ast_translate

        dax = "TOTALYTD(SUM('SomeTable'[SomeColumn]), 'Date'[Date])"
        sql = try_ast_translate(dax, table_alias="fact")

        assert sql is not None
        assert 'COL_DATE."COL_DATE"' in sql, (
            f"expected the default date alias to be COL_DATE, not CALENDAR: {sql!r}"
        )
        assert "CALENDAR" not in sql.upper()

    def test_full_translator_totalytd_uses_col_date_alias_by_default(self):
        translator = DAXTranslator()
        dax = "TOTALYTD(SUM('SomeTable'[SomeColumn]), 'Date'[Date])"
        result = translator.translate(dax, "fact", "SomeTable")

        assert result.is_success, f"expected translation to succeed, got sql={result.sql!r}"
        assert 'COL_DATE."COL_DATE"' in result.sql
        assert "CALENDAR" not in result.sql.upper()


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

    def test_lag_period_of_unshiftable_measure_ships_advisory_flagged_fallback_instead_of_failing(self):
        """DAXTranslator.translate()'s Tier-3 time-intel call site opts into
        allow_unshifted_fallback=True -- a lag-period function wrapping a
        measure this renderer can't safely date-filter (e.g. a nested
        aggregate) now succeeds with the base measure's own unmodified SQL,
        tagged via DAXTranslationResult.advisory_categories, instead of
        falling through to Tier 4/5."""
        translator = DAXTranslator()
        metrics_context = [_metric("Weird_Measure", 'SUM(AVG(sometable."SOMECOLUMN"))')]
        dax = "CALCULATE([Weird_Measure], SAMEPERIODLASTYEAR('Date'[Date]))"

        result = translator.translate(dax, "sometable", "SomeTable", metrics_context=metrics_context)

        assert result.is_success
        assert result.sql is not None
        assert result.tier == 3
        assert 'SUM(AVG(sometable."SOMECOLUMN"))' in result.sql
        assert "CASE WHEN" not in result.sql.upper()
        from semabridge.converter.dax_ast_parser import ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK
        assert result.advisory_categories == [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK]


class TestGetRequiredDimensionsNeverHardcodesOrSubstringMatches:
    """get_required_dimensions used to (a) hardcode a literal
    "'Calendar'[Date]" dimension whenever any time-intelligence function
    appeared in the DAX — wrong for any customer whose date table isn't
    literally named "Calendar" with a "Date" column, and with no schema
    access available to this function to resolve the real one — and (b)
    exclude bracketed references from GROUP BY using a raw substring
    check against English business terms (AMOUNT/SALES/REVENUE/PRICE/
    COST/QTY), which would wrongly drop a genuine dimension like
    "Costco_Region" (contains "COST") from the required GROUP BY list."""

    def test_time_intelligence_no_longer_hardcodes_a_calendar_date_literal(self):
        translator = DAXTranslator()
        dims = translator.get_required_dimensions(
            "TOTALYTD(SUM('SomeTable'[SomeColumn]), 'SomeDateTable'[SomeDateColumn])"
        )
        assert "'Calendar'[Date]" not in dims

    def test_real_dimension_reference_containing_a_value_term_substring_is_kept(self):
        """Regression: 'Costco_Region' contains 'COST' as a substring but
        is clearly a dimension, not a cost/value measure column."""
        translator = DAXTranslator()
        dims = translator.get_required_dimensions("SUM('Geo'[Costco_Region])")
        assert "'Geo'[Costco_Region]" in dims

    def test_genuine_value_column_whole_word_is_still_excluded(self):
        translator = DAXTranslator()
        dims = translator.get_required_dimensions("SUM('SalesFact'[Amount])")
        assert "'SalesFact'[Amount]" not in dims

    def test_multiple_references_mix_of_dimension_and_value_columns(self):
        translator = DAXTranslator()
        dims = translator.get_required_dimensions(
            "CALCULATE(SUM('SalesFact'[Revenue]), 'Product'[Costco_Supplier])"
        )
        assert "'SalesFact'[Revenue]" not in dims
        assert "'Product'[Costco_Supplier]" in dims


class TestResolvePhysicalColNameNeverGuessesAnUnverifiedName:
    """_resolve_physical_col_name used to hardcode a "COL_{name}" prefix
    convention (this codebase's own internal synthetic-anchor naming, not
    a general customer schema convention) and fall back to an unbounded
    substring match with no word-boundary check — DAX "ID" would match
    CUSTOMERID/PRODUCTID/VALID_FLAG/anything containing those two letters
    — then always returned a guessed name, never "not found". One call
    site feeds the guess directly into a live UPDATE statement's SELECT
    column, so a false substring match meant silently pulling data from
    the wrong physical column."""

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

    def test_resolves_exact_case_insensitive_match(self):
        emitter = self._build_emitter()
        existing = {"WIDGET_COUNT", "GADGET_TOTAL"}
        assert emitter._resolve_physical_col_name("widget_count", existing) == "WIDGET_COUNT"

    def test_resolves_space_to_underscore_normalized_match(self):
        emitter = self._build_emitter()
        existing = {"WIDGET_COUNT"}
        assert emitter._resolve_physical_col_name("Widget Count", existing) == "WIDGET_COUNT"

    def test_does_not_guess_a_col_prefixed_name_that_does_not_exist(self):
        """No hardcoded 'COL_' convention — a customer whose real schema
        doesn't use that prefix must not get a fabricated column name."""
        emitter = self._build_emitter()
        existing = {"GADGET_TOTAL"}  # no COL_WIDGET anywhere
        assert emitter._resolve_physical_col_name("Widget", existing) == ""

    def test_does_not_substring_match_an_unrelated_column(self):
        """Synthetic placeholder proving the old unbounded substring bug:
        DAX column 'ID' must not match a real column that merely contains
        those letters, like VALID_FLAG."""
        emitter = self._build_emitter()
        existing = {"VALID_FLAG"}
        assert emitter._resolve_physical_col_name("ID", existing) == ""

    def test_returns_empty_string_not_a_guess_when_nothing_matches(self):
        emitter = self._build_emitter()
        existing = {"COMPLETELY_UNRELATED_COLUMN"}
        assert emitter._resolve_physical_col_name("Widget Count", existing) == ""
