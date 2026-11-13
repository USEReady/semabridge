"""Tests for time-intelligence anchor resolution when the date column lives on a
related dimension table joined by a non-date key (e.g. fact.PeriodKey -> calendar.PeriodKey -> calendar.Date).
"""
import pytest
from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    Cardinality, DataType
)
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.anchor_flag_rerender import rerender_anchor_dependent_metrics
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.run_summary import RunStatus, create_run_summary
from semabridge.core.executor import CLIExecutor


@pytest.fixture
def config():
    return SnowflakeConfig(account="test", user="test", database="DB", schema_name="SCHEMA")


def test_time_intelligence_dimension_date_anchor_ytd_and_sply_real_sql(config):
    """Generic fixture with fact.PeriodKey(string) -> calendar(PeriodKey PK, Date)
    and TOTALYTD / SAMEPERIODLASTYEAR on calendar[Date]: YTD and SPLY must be REAL SQL, not NULL.
    """
    emitter = SnowflakeEmitter(config)
    emitter.sf_behavior.auto_create_enriched_view = True
    emitter.sf_behavior.use_enriched_view_for_metrics = True

    model = SMLModel(
        unique_name="PeriodKeyModel",
        datasets=[
            SMLDataset(
                unique_name="FactSales",
                is_fact=True,
                source_table="FACT_SALES",
                columns=[
                    SMLColumn(unique_name="PeriodKey", data_type=DataType.STRING),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="Calendar",
                source_table="CALENDAR",
                columns=[
                    SMLColumn(unique_name="PeriodKey", data_type=DataType.STRING, is_key=True),
                    SMLColumn(unique_name="Date", data_type=DataType.DATE),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="rel_FactSales_Calendar",
                from_dataset="FactSales",
                from_columns=["PeriodKey"],
                to_dataset="Calendar",
                to_columns=["PeriodKey"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Sales_YTD",
                dataset="FactSales",
                expression="TOTALYTD(SUM(FactSales[Amount]), Calendar[Date])",
            ),
            SMLMetric(
                unique_name="Sales_SPLY",
                dataset="FactSales",
                expression="CALCULATE(SUM(FactSales[Amount]), SAMEPERIODLASTYEAR(Calendar[Date]))",
            ),
        ],
    )

    # 1. Fact date column resolution identifies joined date anchor
    info = emitter._resolve_fact_anchor_info(model, "FactSales")
    assert info is not None
    assert info["mode"] == "joined"
    assert info["dim_table"] == "Calendar"
    assert info["fact_fk"] == "PeriodKey"
    assert info["dim_pk"] == "PeriodKey"
    assert info["dim_date_col"] == "Date"
    assert info["can_duplicate"] is False

    # 2. Predicted eligible tables and flag map
    eligible = emitter._predict_eligible_enrichment_fact_tables(model)
    assert "FactSales" in eligible
    pred_map = emitter.predict_anchor_flag_map(model)
    assert "factsales" in pred_map
    assert ("YTD",) in pred_map["factsales"]
    assert ("SPLY_YEAR",) in pred_map["factsales"]

    # 3. Enriched view generation joins dimension and projects data-anchored MAX_DATE & flags
    planned = emitter._create_enriched_view(model, None, fact_table="FactSales", dry_run=True)
    assert planned is not None
    view_name, view_sql = planned
    assert "FactSales_ENRICHED" in view_name
    assert 'LEFT JOIN "DB"."SCHEMA"."CALENDAR" dt_anchor ON f."PERIODKEY" = dt_anchor."PERIODKEY"' in view_sql
    assert 'dt_anchor."COL_DATE" AS "COL_DATE"' in view_sql or 'dt_anchor."DATE" AS "DATE"' in view_sql
    assert 'COALESCE(MAX(dt_anchor.' in view_sql
    assert 'AS "IS_YTD"' in view_sql
    assert 'AS "IS_SPLY_YEAR"' in view_sql

    # 4. Re-render metrics with flag map replaces placeholders with REAL SQL
    dataset_col_lookup, dataset_aliases = DAXTranslator.build_schema_lookup(model.datasets)
    rerendered = rerender_anchor_dependent_metrics(
        model,
        pred_map,
        dataset_col_lookup,
        dataset_aliases,
        label="confirmed",
    )
    assert rerendered >= 2

    metric_ytd = next(m for m in model.metrics if m.unique_name == "Sales_YTD")
    metric_sply = next(m for m in model.metrics if m.unique_name == "Sales_SPLY")

    assert metric_ytd.sql_expression is not None
    assert "IS_YTD" in metric_ytd.sql_expression
    assert "NULL" not in metric_ytd.sql_expression.upper() or "THEN" in metric_ytd.sql_expression.upper()
    assert metric_ytd.sync_failure_reason is None

    assert metric_sply.sql_expression is not None
    assert "IS_SPLY_YEAR" in metric_sply.sql_expression
    assert metric_sply.sync_failure_reason is None

    # 5. Semantic view DDL emission
    emitter._enriched_view_mapping["FactSales"] = planned[0]
    ddls = emitter.generate_ddls(model)
    assert len(ddls) >= 1
    ddl = ddls[0]

    assert "FACTSALES_ENRICHED" in ddl or "FactSales_ENRICHED" in ddl
    assert 'FACTSALES."SALES_YTD" AS' in ddl
    assert 'FACTSALES."SALES_SPLY" AS' in ddl
    assert 'CAST(NULL AS' not in ddl


def test_time_intelligence_duplicate_risk_fails_closed(config):
    """If the join can duplicate fact rows (non-unique key on dimension or MANY_TO_MANY),
    fail closed with a clear reason and emit clean NULL comment without 'failed: failed'.
    """
    emitter = SnowflakeEmitter(config)
    emitter.sf_behavior.auto_create_enriched_view = True

    model_dup = SMLModel(
        unique_name="DupModel",
        datasets=[
            SMLDataset(
                unique_name="FactSales",
                is_fact=True,
                source_table="FACT_SALES",
                columns=[
                    SMLColumn(unique_name="PeriodKey", data_type=DataType.STRING),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="Calendar",
                source_table="CALENDAR",
                columns=[
                    SMLColumn(unique_name="PeriodKey", data_type=DataType.STRING, is_key=False),
                    SMLColumn(unique_name="Date", data_type=DataType.DATE),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="rel_FactSales_Calendar",
                from_dataset="FactSales",
                from_columns=["PeriodKey"],
                to_dataset="Calendar",
                to_columns=["PeriodKey"],
                cardinality=Cardinality.MANY_TO_MANY,
                is_active=True,
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Sales_YTD",
                dataset="FactSales",
                expression="TOTALYTD(SUM(FactSales[Amount]), Calendar[Date])",
            ),
        ],
    )

    info = emitter._resolve_fact_anchor_info(model_dup, "FactSales")
    assert info is not None
    assert info["can_duplicate"] is True
    assert "may duplicate fact rows" in info["duplicate_reason"]

    planned = emitter._create_enriched_view(model_dup, None, fact_table="FactSales", dry_run=True)
    assert planned is None
    assert "factsales" in emitter._failed_enrichment_datasets

    # Test clean NULL comment
    dataset_col_lookup, dataset_aliases = DAXTranslator.build_schema_lookup(model_dup.datasets)
    rerender_anchor_dependent_metrics(
        model_dup,
        {},
        dataset_col_lookup,
        dataset_aliases,
        failed_enrichment_datasets=emitter._failed_enrichment_datasets,
        failed_enrichment_errors=emitter._failed_enriched_view_datasets,
    )
    metric = model_dup.metrics[0]
    assert metric.sql_expression is None
    assert "may duplicate fact rows" in metric.sync_failure_reason
    assert "failed: failed" not in metric.sync_failure_reason.lower()


def test_run_warning_status_when_high_null_metrics():
    """Mark the run WARNING when > N% of metrics are NULL placeholders."""
    summary = create_run_summary(
        project_id="test_proj",
        run_id="test_run",
        source_type="pbix",
        target_type="snowflake",
    )

    class MockModel:
        metrics = [
            SMLMetric(unique_name=f"m_{i}", dataset="ds", sql_expression=None if i < 5 else "SUM(a)")
            for i in range(10)
        ]

    class MockExecutor:
        summary = None
        _sml_model = MockModel()
        project_id = "test_proj"
        run_id = "test_run"
        _db_manager = None

    MockExecutor._step_10_finalize = CLIExecutor._step_10_finalize
    executor = MockExecutor()
    executor.summary = summary
    res = executor._step_10_finalize(status=RunStatus.SUCCESS)

    assert res.status == RunStatus.WARNING
