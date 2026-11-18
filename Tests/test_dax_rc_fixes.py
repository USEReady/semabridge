import pytest
from semabridge.converter.dax_ast_parser import (
    DaxAstParser,
    DaxSqlRenderer,
    validate_emitted_sql,
    extract_measure_references_from_ast,
    ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS,
    ADVISORY_NOTE_CALCULATE_FILTER_SEMANTICS,
    try_ast_translate,
    dax_calculate_filters_unreachable_dimension,
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
        # Operands are parenthesized only when needed (_wrap_parens_for_op, 2afb203).
        assert sql == 'fact."A" / NULLIF(fact."B", 0)'

    def test_divide_two_args_emits_nullif(self):
        renderer = DaxSqlRenderer("fact")
        ast = DaxAstParser().parse("DIVIDE([A], [B])")
        sql = renderer.render(ast)
        # Operands are parenthesized only when needed (_wrap_parens_for_op, 2afb203).
        assert sql == 'fact."A" / NULLIF(fact."B", 0)'


class TestAliasResolutionRelationshipEnforcement:
    @staticmethod
    def _vendor_rel():
        # FactSpend takes part in a relationship, so the renderer knows its
        # graph; since 5e4ab8e (F2) a primary table with NO relationships at
        # all (e.g. a measure table) resolves other tables by name instead.
        return OSIRelationship(
            unique_name="REL_FACTSPEND_VENDORKEY__VENDOR_VENDORKEY",
            from_dataset="FactSpend",
            from_columns=["VendorKey"],
            to_dataset="Vendor",
            to_columns=["VendorKey"],
            cardinality=OSICardinality.MANY_TO_ONE,
            cross_filter_direction=OSICrossFilterDirection.SINGLE,
            is_active=True,
        )

    def test_unknown_table_without_alias_or_relationship_fails_closed(self):
        renderer = DaxSqlRenderer(
            table_alias="factspend",
            primary_table_name="FactSpend",
            table_aliases={"FactSpend": "factspend", "Scenario": "scenario"},
            relationships=[self._vendor_rel()],
        )
        ast = DaxAstParser().parse('CALCULATE(SUM(FactSpend[Amount]), OtherTable[Col]="Val")')
        sql = renderer.render(ast)
        assert sql is None  # OtherTable is not aliased and has no path from FactSpend

    def test_table_in_aliases_without_relationship_path_fails_closed(self):
        # Since 81c8743 an explicitly aliased table resolves to its alias in
        # the renderer; an unreachable CALCULATE filter is caught by the
        # dedicated detector instead (Snowflake rejects it with 010211).
        dax = 'CALCULATE(SUM(FactSpend[Amount]), Scenario[ScenarioDescription]="Actual")'
        result = dax_calculate_filters_unreachable_dimension(
            dax,
            calling_dataset="FactSpend",
            relationships=[self._vendor_rel()],
        )
        assert result is not None
        assert result.filtered_table == "Scenario"

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
