from __future__ import annotations

from types import SimpleNamespace

from semabridge.compiler.compiler import DAXCompiler
from semabridge.compiler.schema_resolver import SemanticSchemaResolver
from semabridge.connectors.sql_validator import validate_sql_expression
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.intermediate.models import (
    OSIColumn,
    OSIDataset,
    OSIModel,
    OSIRelationship,
    OSIDataType,
    OSICardinality,
)


def _compiler_model() -> SimpleNamespace:
    return SimpleNamespace(
        datasets=[
            SimpleNamespace(unique_name="Finance Fact", label="Finance Fact", source_table="Finance Fact", columns=[SimpleNamespace(unique_name="Revenue"), SimpleNamespace(unique_name="Date")], is_fact=True, is_hidden=False),
            SimpleNamespace(unique_name="HR Fact", label="HR Fact", source_table="HR Fact", columns=[SimpleNamespace(unique_name="Headcount")], is_fact=True, is_hidden=False),
            SimpleNamespace(unique_name="Manufacturing Fact", label="Manufacturing Fact", source_table="Manufacturing Fact", columns=[SimpleNamespace(unique_name="Units")], is_fact=True, is_hidden=False),
            SimpleNamespace(unique_name="Fiscal Calendar", label="Fiscal Calendar", source_table="Fiscal Calendar", columns=[SimpleNamespace(unique_name="Date"), SimpleNamespace(unique_name="Year"), SimpleNamespace(unique_name="Quarter"), SimpleNamespace(unique_name="Month")], is_fact=False, is_hidden=False),
        ],
        relationships=[],
        metrics=[
            SimpleNamespace(unique_name="Revenue", dataset="Finance Fact", expression="SUM('Finance Fact'[Revenue])", sql_expression='SUM("FINANCE FACT"."REVENUE")'),
            SimpleNamespace(unique_name="Headcount", dataset="HR Fact", expression="SUM('HR Fact'[Headcount])", sql_expression='SUM("HR FACT"."HEADCOUNT")'),
            SimpleNamespace(unique_name="Units", dataset="Manufacturing Fact", expression="SUM('Manufacturing Fact'[Units])", sql_expression='SUM("MANUFACTURING FACT"."UNITS")'),
            SimpleNamespace(unique_name="YTD Revenue", dataset="Finance Fact", expression="TOTALYTD([Revenue], 'Fiscal Calendar'[Date])", sql_expression=None),
        ],
    )


def test_generalized_models_validate_and_plan_aliases() -> None:
    model = _compiler_model()
    resolver = SemanticSchemaResolver(model=model, metrics=model.metrics)

    alias_a = resolver.plan_alias("Category Product", lineage="Finance Fact")
    alias_b = resolver.plan_alias("Category Product", lineage="Finance Fact")
    alias_c = resolver.plan_alias("Category Product", lineage="HR Fact")

    assert alias_a == alias_b
    assert alias_a != alias_c

    result = DAXCompiler().compile_expression(
        "TOTALYTD([Revenue], 'Fiscal Calendar'[Date])",
        model=model,
        metrics=model.metrics,
        metric_name="YTD Revenue",
        table_alias="Finance Fact",
        dataset_name="Finance Fact",
    )

    assert result.is_success is True
    assert result.sql is not None
    assert validate_sql_expression(result.sql).is_valid is True


class TestSemanticGeneralization:
    def test_sales_model_generalization(self) -> None:
        model = OSIModel(
            unique_name="SalesModel",
            datasets=[
                OSIDataset(
                    unique_name="Sales",
                    source_table="FCT_SALES",
                    columns=[
                        OSIColumn(unique_name="ProductID", source_expression="PROD_ID", data_type=OSIDataType.STRING, is_key=True),
                        OSIColumn(unique_name="Revenue", source_expression="REV_AMT", data_type=OSIDataType.FLOAT, is_measure_candidate=True),
                    ],
                )
            ],
        )

        translator = DAXTranslator(osi_model=model)
        metrics = [SimpleNamespace(unique_name="TotalRevenue", expression="SUM(Sales[Revenue])", sql_expression=None)]

        translator.translate_with_dependencies(metrics, "Sales", "Sales")
        sql = metrics[0].sql_expression

        assert sql is not None
        assert sql.upper().startswith("SUM(")
        assert "REVENUE" in sql.upper() or "REV_AMT" in sql.upper()

    def test_finance_model_generalization(self) -> None:
        model = OSIModel(
            unique_name="FinanceModel",
            datasets=[
                OSIDataset(
                    unique_name="FinanceFact",
                    source_table="FIN_FACT",
                    columns=[
                        OSIColumn(unique_name="Amount", source_expression="VAL_USD", data_type=OSIDataType.FLOAT, is_measure_candidate=True),
                        OSIColumn(unique_name="ForecastScenario", source_expression="SCENARIO_KEY", data_type=OSIDataType.INTEGER, is_key=True),
                    ],
                ),
                OSIDataset(
                    unique_name="ScenarioDim",
                    source_table="SCENARIO_DIM",
                    columns=[
                        OSIColumn(unique_name="ScenarioName", source_expression="SCENARIO_NAME", data_type=OSIDataType.STRING),
                        OSIColumn(unique_name="ScenarioID", source_expression="SCENARIO_KEY", data_type=OSIDataType.INTEGER, is_key=True),
                    ],
                ),
            ],
            relationships=[
                OSIRelationship(
                    unique_name="REL_FINANCEFACT_FORECASTSCENARIO__SCENARIODIM_SCENARIOID",
                    from_dataset="FinanceFact",
                    from_columns=["ForecastScenario"],
                    to_dataset="ScenarioDim",
                    to_columns=["ScenarioID"],
                    cardinality=OSICardinality.MANY_TO_ONE,
                )
            ],
        )

        translator = DAXTranslator(osi_model=model)
        metrics = [SimpleNamespace(unique_name="ForecastValue", expression='CALCULATE(SUM(FinanceFact[Amount]), ScenarioDim[ScenarioName] = "Forecast")', sql_expression=None)]

        translator.translate_with_dependencies(metrics, "FinanceFact", "FinanceFact")
        sql = metrics[0].sql_expression

        assert sql is not None
        assert "CASE WHEN" in sql.upper()
        assert "FORECAST" in sql.upper()
        assert "SCENARIO" in sql.upper()

    def test_hr_model_generalization(self) -> None:
        model = OSIModel(
            unique_name="HRModel",
            datasets=[
                OSIDataset(
                    unique_name="EmployeeFact",
                    source_table="EMP_FACT",
                    columns=[
                        OSIColumn(unique_name="Headcount", source_expression="EMP_COUNT", data_type=OSIDataType.INTEGER, is_measure_candidate=True),
                        OSIColumn(unique_name="BusinessUnitID", source_expression="BU_ID", data_type=OSIDataType.INTEGER, is_key=True),
                    ],
                ),
                OSIDataset(
                    unique_name="BusinessUnitDim",
                    source_table="BU_DIM_TABLE",
                    columns=[
                        OSIColumn(unique_name="Business Unit", source_expression="BU_NAME", data_type=OSIDataType.STRING),
                        OSIColumn(unique_name="BusinessUnitID", source_expression="BU_ID", data_type=OSIDataType.INTEGER, is_key=True),
                    ],
                ),
            ],
        )

        translator = DAXTranslator(osi_model=model)
        metrics = [SimpleNamespace(unique_name="BUHeadcount", expression='CALCULATE(SUM(EmployeeFact[Headcount]), BusinessUnitDim[Business Unit] = "HR")', sql_expression=None)]

        translator.translate_with_dependencies(metrics, "EmployeeFact", "EmployeeFact")
        sql = metrics[0].sql_expression

        assert sql is not None
        assert "CASE WHEN" in sql.upper()
        assert "HR" in sql

    def test_manufacturing_model_custom_calendar_generalization(self) -> None:
        model = OSIModel(
            unique_name="MfgModel",
            datasets=[
                OSIDataset(
                    unique_name="ProductionFact",
                    source_table="MFG_PROD_FACT",
                    columns=[
                        OSIColumn(unique_name="UnitsProduced", source_expression="UNITS_COUNT", data_type=OSIDataType.INTEGER, is_measure_candidate=True),
                        OSIColumn(unique_name="ProductionDate", source_expression="PROD_DT", data_type=OSIDataType.DATE, is_key=True),
                    ],
                ),
                OSIDataset(
                    unique_name="MfgCalendar",
                    source_table="MFG_DATE_DIM",
                    columns=[
                        OSIColumn(unique_name="ProductionDate", source_expression="PROD_DT", data_type=OSIDataType.DATE, is_key=True),
                        OSIColumn(unique_name="FiscalYear", source_expression="FS_YR", data_type=OSIDataType.INTEGER),
                        OSIColumn(unique_name="FiscalMonth", source_expression="FS_MTH", data_type=OSIDataType.INTEGER),
                    ],
                ),
            ],
        )

        translator = DAXTranslator(osi_model=model)
        metrics = [SimpleNamespace(unique_name="YTDUnits", expression="TOTALYTD(SUM(ProductionFact[UnitsProduced]), MfgCalendar[ProductionDate])", sql_expression=None)]

        translator.translate_with_dependencies(metrics, "ProductionFact", "ProductionFact")
        sql = metrics[0].sql_expression

        assert sql is not None
        assert "OVER" in sql.upper()
        assert "PARTITION BY" in sql.upper()
        assert "ORDER BY" in sql.upper()
