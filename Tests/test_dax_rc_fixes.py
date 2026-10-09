import pytest
from semabridge.converter.dax_ast_parser import (
    DaxAstParser,
    DaxSqlRenderer,
    validate_emitted_sql,
    extract_measure_references_from_ast,
    ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS,
    ADVISORY_NOTE_CALCULATE_FILTER_SEMANTICS,
    try_ast_translate,
)
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIModel, OSIDataset, OSIColumn, OSIMetric, OSIRelationship,
    OSIDataType, OSIAggregationType, OSICardinality, OSICrossFilterDirection
)


class TestValidationGate:
    def test_rejects_sql_with_dax_syntax(self):
        assert validate_emitted_sql("SUM(fact.AMOUNT) + BLANK()") is False
        assert validate_emitted_sql("CALCULATE(SUM(fact.AMOUNT))") is False
        assert validate_emitted_sql("SUM(CASE WHEN FILTER(t, x) THEN 1 END)") is False
        assert validate_emitted_sql("DIVIDE(a, b)") is False
        assert validate_emitted_sql("fact.[Amount]") is False
        assert validate_emitted_sql("SUM(fact.\"AMOUNT\")") is True
        assert validate_emitted_sql("DATEADD(YEAR, -1, CURRENT_DATE())") is True
        assert validate_emitted_sql("(x) / NULLIF(y, 0)") is True


class TestDivideBlankTranslation:
    def test_divide_blank_emits_nullif_without_blank(self):
        renderer = DaxSqlRenderer("fact")
        ast = DaxAstParser().parse("DIVIDE([A], [B], BLANK())")
        sql = renderer.render(ast)
        assert "BLANK" not in sql
        assert "NULLIF" in sql
        assert sql == '(fact."A") / NULLIF(fact."B", 0)'

    def test_divide_two_args_emits_nullif(self):
        renderer = DaxSqlRenderer("fact")
        ast = DaxAstParser().parse("DIVIDE([A], [B])")
        sql = renderer.render(ast)
        assert sql == '(fact."A") / NULLIF(fact."B", 0)'


class TestAliasResolutionRelationshipEnforcement:
    def test_unknown_table_without_alias_or_relationship_fails_closed(self):
        renderer = DaxSqlRenderer(
            table_alias="factspend",
            primary_table_name="FactSpend",
            table_aliases={"FactSpend": "factspend", "Scenario": "scenario"},
            relationships=[],
        )
        ast = DaxAstParser().parse('CALCULATE(SUM(FactSpend[Amount]), OtherTable[Col]="Val")')
        sql = renderer.render(ast)
        assert sql is None  # OtherTable not in table_aliases

    def test_table_in_aliases_without_relationship_path_fails_closed(self):
        renderer = DaxSqlRenderer(
            table_alias="factspend",
            primary_table_name="FactSpend",
            table_aliases={"FactSpend": "factspend", "Scenario": "scenario"},
            relationships=[],  # No relationship between FactSpend and Scenario
        )
        ast = DaxAstParser().parse('CALCULATE(SUM(FactSpend[Amount]), Scenario[ScenarioDescription]="Actual")')
        sql = renderer.render(ast)
        assert sql is None  # No path from FactSpend to Scenario

    def test_table_with_valid_relationship_resolves_and_adds_advisory(self):
        rel = OSIRelationship(
            unique_name="REL_FACTSPEND_KEY__SCENARIO_KEY",
            from_dataset="FactSpend",
            from_columns=["ScenarioKey"],
            to_dataset="Scenario",
            to_columns=["ScenarioKey"],
            cardinality=OSICardinality.MANY_TO_ONE,
            cross_filter_direction=OSICrossFilterDirection.SINGLE,
            is_active=True,
        )
        advisories = []
        renderer = DaxSqlRenderer(
            table_alias="factspend",
            primary_table_name="FactSpend",
            table_aliases={"FactSpend": "factspend", "Scenario": "scenario"},
            relationships=[rel],
            advisory_categories=advisories,
        )
        ast = DaxAstParser().parse('CALCULATE(SUM(FactSpend[Amount]), Scenario[ScenarioDescription]="Actual")')
        sql = renderer.render(ast)
        assert sql is not None
        assert 'scenario."SCENARIODESCRIPTION" = \'Actual\'' in sql
        assert ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS in advisories


class TestAstBasedMeasureClassification:
    def test_ast_distinguishes_column_from_measure(self):
        refs = extract_measure_references_from_ast("CALCULATE([Amount], Scenario[ScenarioDescription] = 'Actual')")
        assert refs == {"Amount"}
        assert "ScenarioDescription" not in refs
        assert "Scenario" not in refs

    def test_ast_extracts_all_nested_measure_refs(self):
        refs = extract_measure_references_from_ast("DIVIDE([Actual] - [Plan], [Plan], BLANK())")
        assert refs == {"Actual", "Plan"}


class TestDependencyResolutionCycleReporting:
    def test_circular_dependency_is_detected_and_reported(self):
        osi_model = OSIModel(
            unique_name="CycleModel",
            label="Cycle Model",
            source_platform="powerbi",
            datasets=[
                OSIDataset(
                    unique_name="Fact",
                    columns=[OSIColumn(unique_name="Val", data_type=OSIDataType.FLOAT)],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="MetricA",
                    label="Metric A",
                    dataset="Fact",
                    expression="[MetricB] + 1",
                    aggregation=OSIAggregationType.NONE,
                ),
                OSIMetric(
                    unique_name="MetricB",
                    label="Metric B",
                    dataset="Fact",
                    expression="[MetricA] + 1",
                    aggregation=OSIAggregationType.NONE,
                ),
            ],
        )
        converter = OSIToSMLConverter()
        sml = converter.from_osi(osi_model)
        for m in sml.metrics:
            assert m.sync_enabled is False
            assert "Circular measure dependency cycle detected" in (m.sync_failure_reason or "")
            assert "MetricA" in (m.sync_failure_reason or "")
            assert "MetricB" in (m.sync_failure_reason or "")
