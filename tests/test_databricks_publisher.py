"""Tests for DatabricksPublisher — metadata table + measure views."""

import re
from unittest.mock import patch

import pytest
import yaml

from semabridge.connectors.databricks_publisher import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    DEPLOY_REASON_CROSS_TABLE,
    DEPLOY_REASON_DAX_NOT_SUPPORTED,
    DEPLOY_REASON_VALIDATION_FAILED,
    DEPLOY_STATUS_DEPLOYED,
    DEPLOY_STATUS_NOT_DEPLOYED,
    TRANSLATION_TYPE_AGGREGATION_BUILT,
    TRANSLATION_TYPE_DAX_SKIPPED,
    TRANSLATION_TYPE_DAX_TRANSLATED,
    TRANSLATION_TYPE_SQL_NATIVE,
    VIEW_TYPE_MATERIALIZED,
    VIEW_TYPE_METRIC,
    VIEW_TYPE_SQL,
    DatabricksPublishError,
    DatabricksPublisher,
    ResolvedMeasure,
)
from semabridge.connectors.databricks_measure_translation import (
    TIER_DEFERRED,
    TIER_FILTERED_CONDITIONAL_AGGREGATION,
    TIER_RELATIONSHIP_AWARE_FILTERED_AGGREGATION,
    TIER_SCALAR_SYSTEM_FUNCTION,
    TIER_STANDARD_AGGREGATION,
)
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import (
    AggregationType,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _cfg() -> DatabricksConfig:
    return DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )


def _sales_model(
    *,
    with_sql_expression: bool = False,
    with_source_column: bool = False,
    with_group_by: bool = False,
    dax_only: bool = False,
) -> SMLModel:
    """Build a small SML model for testing various measure configurations."""
    metrics: list[SMLMetric] = []

    if with_sql_expression:
        metrics.append(
            SMLMetric(
                unique_name="Total_Revenue",
                dataset="Sales",
                sql_expression="SUM(`REVENUE`)",
                aggregation=AggregationType.SUM,
                group_by_dimensions=["REGION"] if with_group_by else [],
            )
        )
    elif with_source_column:
        metrics.append(
            SMLMetric(
                unique_name="Total_Revenue",
                dataset="Sales",
                source_column="REVENUE",
                aggregation=AggregationType.SUM,
                group_by_dimensions=["REGION"] if with_group_by else [],
            )
        )
    elif dax_only:
        metrics.append(
            SMLMetric(
                unique_name="Total_Revenue",
                dataset="Sales",
                expression="CALCULATE(SUM('Sales'[Revenue]), ALL('Date'))",
                aggregation=AggregationType.SUM,
            )
        )

    return SMLModel(
        unique_name="SalesModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="REGION", data_type=DataType.STRING),
                    SMLColumn(unique_name="DATE", data_type=DataType.DATE),
                ],
            )
        ],
        metrics=metrics,
    )


# ── Original Tests (Preserved) ──────────────────────────────────────────────

def test_generate_sql_statements_creates_single_model_table_with_rows():
    cfg = _cfg()
    model = SMLModel(
        unique_name="annual",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="region", data_type=DataType.STRING),
                    SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="revenue", dataset="Sales"),
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "SEMABRIDGE_MODEL_SNAPSHOT" not in sql
    assert "DROP TABLE IF EXISTS `main`.`public`.`annual`" in sql
    assert "CREATE TABLE `main`.`public`.`annual`" in sql
    assert "'Sales', 'region', 'dimension'" in sql
    assert "'Sales', 'revenue', 'measure'" in sql


def test_generate_sql_statements_uses_int_for_calendar_dimensions():
    cfg = _cfg()
    model = SMLModel(
        unique_name="continent",
        datasets=[
            SMLDataset(
                unique_name="Date",
                columns=[
                    SMLColumn(unique_name="Year", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Quarter", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Month", data_type=DataType.INTEGER),
                ],
            )
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "'Year', 'dimension', 'INT'" in sql
    assert "'Quarter', 'dimension', 'INT'" in sql
    assert "'Month', 'dimension', 'INT'" in sql


def test_generate_sql_statements_sanitizes_invalid_identifiers():
    cfg = _cfg()
    model = SMLModel(
        unique_name="continent",
        datasets=[
            SMLDataset(
                unique_name="continent 1",
                columns=[
                    SMLColumn(unique_name="country.name", data_type=DataType.STRING),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="total sales$", dataset="continent 1"),
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "DROP TABLE IF EXISTS `main`.`public`.`continent`" in sql
    assert "CREATE TABLE `main`.`public`.`continent`" in sql
    assert "'continent_1', 'country_name', 'dimension'" in sql
    assert "'continent_1', 'total_sales', 'measure'" in sql


# ── Measure View Tests ───────────────────────────────────────────────────────

class TestMeasureViewGeneration:
    """Verify measure view SQL generation."""

    def test_sql_expression_priority_creates_view(self):
        """sql_expression takes highest priority and produces a CREATE VIEW."""
        model = _sales_model(with_sql_expression=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert created == 1
        assert skipped == 0
        assert len(stmts) == 1
        assert "CREATE OR REPLACE VIEW" in stmts[0]
        assert "SUM(`revenue`)" in stmts[0]
        assert "`mv_SalesModel_Total_Revenue`" in stmts[0]

    def test_sql_view_rewrites_same_dataset_qualified_sql_expression(self):
        """SQL fallback views should normalize same-dataset qualified identifiers."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(measure_view_type="sql_view")
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="Fact",
                    sql_expression='SUM(fact."REVENUE")',
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="# of Customers",
                    dataset="Fact",
                    sql_expression='COUNT(DISTINCT fact."CUSTOMER_KEY")',
                    aggregation=AggregationType.COUNT_DISTINCT,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert created == 2
        assert skipped == 0
        assert not details
        assert 'fact."REVENUE"' not in stmts[0]
        assert 'fact."CUSTOMER_KEY"' not in stmts[1]
        assert "SUM(`revenue`)" in stmts[0]
        assert "COUNT(DISTINCT `customer_key`)" in stmts[1]
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="Fact",
                    source_column="Revenue",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_auto_initialize_missing_tables",
            return_value=None,
        ), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`fact`",
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"bu_key"},
        ), patch.object(
            publisher,
            "execute_statements",
            return_value=[],
        ) as execute_mock:
            result = publisher.publish(model)

        assert result == "databricks://main/public/Customer Profitability"
        assert execute_mock.called

    def test_sql_view_skips_cross_table_qualified_sql_expression(self):
        """SQL fallback views should skip expressions that still reference another dataset."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(measure_view_type="sql_view")
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Revenue Budget",
                    dataset="Fact",
                    sql_expression='SUM(CASE WHEN scenario."SCENARIO" = \'Budget\' THEN fact."REVENUE" ELSE 0 END)',
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert len(stmts) == 0
        assert created == 0
        assert skipped == 1
        assert details[0]["reason"] == DEPLOY_REASON_CROSS_TABLE

    def test_aggregation_source_column_creates_view(self):
        """aggregation + source_column builds a view when no sql_expression."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        assert "SUM(`REVENUE`)" in stmts[0]

    def test_simple_dax_translated_creates_view(self):
        """Simple DAX like SUM('Table'[Col]) gets translated and produces a view."""
        model = SMLModel(
            unique_name="DaxModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="Sales",
                    expression="SUM('Sales'[REVENUE])",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        assert "SUM(`REVENUE`)" in stmts[0]
        assert "CREATE OR REPLACE VIEW" in stmts[0]

    def test_sql_view_skips_simple_sum_when_metric_view_only_policy_enabled(self):
        """SQL view generation should skip simple DAX SUM when metric-view-only policy is enabled."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="sql_view",
                metric_view_only_sum_translation=True,
            )
        )
        model = SMLModel(
            unique_name="DaxModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="Sales",
                    expression="SUM('Sales'[REVENUE])",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert stmts == []
        assert created == 0
        assert skipped == 1
        assert details[0]["reason"] == DEPLOY_REASON_DAX_NOT_SUPPORTED

    def test_metric_view_still_translates_simple_sum_with_metric_view_only_policy(self):
        """Metric-view generation should continue translating simple DAX SUM under the policy."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                metric_view_only_sum_translation=True,
            )
        )
        model = SMLModel(
            unique_name="DaxModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="Sales",
                    expression="SUM('Sales'[REVENUE])",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_METRIC,
        )

        assert created == 1
        assert skipped == 0
        assert "sum(revenue)" in stmts[0].lower()

    def test_metric_view_renders_project_measures_sum_and_refresh_max(self):
        """Metric-view generation should keep Project Measures SUMs translated instead of nulling them out."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                metric_view_only_sum_translation=True,
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
                SMLDataset(
                    unique_name="Inventory Fact",
                    columns=[
                        SMLColumn(unique_name="Source Value Total Stock", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="WAC Value Total Stock", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="Corporate DSI Last Refreshed",
                    columns=[
                        SMLColumn(unique_name="GL_Refresh_Datetime", data_type=DataType.DATE),
                    ],
                ),
                SMLDataset(
                    unique_name="Inventory Fact Last Refreshed",
                    columns=[
                        SMLColumn(unique_name="GL_Refresh_Datetime", data_type=DataType.DATE),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Today",
                    dataset="Project Measures",
                    expression="TODAY()",
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="Source Value Total Stock",
                    dataset="Project Measures",
                    expression="SUM('Inventory Fact'[Source Value Total Stock])",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    expression='CONCATENATE("Last Refreshed: ", MAX(\'Corporate DSI Last Refreshed\'[GL_Refresh_Datetime]))',
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="Inventory Fact Last Refreshed",
                    dataset="Project Measures",
                    expression='CONCATENATE("Last Refreshed: ", MAX(\'Inventory Fact Last Refreshed\'[GL_Refresh_Datetime]))',
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="WAC Value Total Stock",
                    dataset="Project Measures",
                    expression="SUM('Inventory Fact'[WAC Value Total Stock])",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        def _mock_columns(source_table: str):
            lowered = source_table.lower()
            if "inventory_fact" in lowered:
                return {"source_value_total_stock", "wac_value_total_stock"}
            if "last_refreshed" in lowered:
                return {"gl_refresh_datetime"}
            if "project_measures" in lowered:
                return {"gl_refresh_datetime"}
            return set()

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, expected: expected,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            side_effect=_mock_columns,
        ), patch.object(
            publisher,
            "_reconcile_dataset_schema",
            return_value=None,
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created >= 1
        assert skipped == 0
        assert not details
        project_measures_stmt = next(
            stmt for stmt in stmts if "mv_Inventory_Semantic_Model_Project_Measures" in stmt
        )
        lowered = project_measures_stmt.lower()
        assert "sum(source_value_total_stock)" in lowered
        assert "sum(wac_value_total_stock)" in lowered
        assert "concat('last refreshed: ', cast(max(gl_refresh_datetime) as string))" in project_measures_stmt.lower()

    def test_today_translated_creates_view(self):
        """TODAY() translates directly to current_date()."""
        model = SMLModel(
            unique_name="TodayModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Today",
                    dataset="Sales",
                    expression="TODAY()",
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        assert "current_date()" in stmts[0]

    def test_concatenate_last_refreshed_translated_creates_view(self):
        """CONCATENATE + MAX translates to concat(...) + max(...)."""
        model = SMLModel(
            unique_name="RefreshModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="GL_Refresh_Datetime", data_type=DataType.DATE),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Sales",
                    expression='CONCATENATE("Last Refreshed: ", MAX(\'Sales\'[GL_Refresh_Datetime]))',
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        lowered = stmts[0].lower()
        assert "concat('last refreshed: ', cast(max(" in lowered
        assert " as string))" in lowered
        assert "gl_refresh_datetime" in lowered

    def test_calculate_sum_with_filter_translates_to_case_when(self):
        """CALCULATE(SUM(...), Dates[...] < fiscalMonth) becomes conditional aggregation."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_cross_table_joins=True)
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        sql_expr = publisher._measure_translator.try_simple_dax_to_sql(
            "CALCULATE(SUM('Sales'[IOH_EXCLDNG_LIFO_AMT]), 'Dates'[FISCAL_YR_PERIOD] < fiscalMonth)"
        )

        assert sql_expr is not None
        assert sql_expr.startswith("SUM(CASE WHEN ")
        assert "`dates`.`FISCAL_YR_PERIOD`" in sql_expr
        assert "fiscalMonth" in sql_expr
        assert "`IOH_EXCLDNG_LIFO_AMT`" in sql_expr

    def test_direct_column_aggregation_patterns_translate(self):
        """Type A parser support: SUM(Table[Column]) patterns are translated."""
        publisher = DatabricksPublisher(_cfg())

        source_value_sql = publisher._measure_translator.try_simple_dax_to_sql(
            "SUM('Inventory Fact'[Source Value Total Stock])"
        )
        wac_value_sql = publisher._measure_translator.try_simple_dax_to_sql(
            "SUM('Inventory Fact'[WAC Value Total Stock])"
        )

        assert source_value_sql == "sum(source_value_total_stock)"
        assert wac_value_sql == "sum(wac_value_total_stock)"

    def test_direct_column_aggregation_preserves_table_with_cross_table_joins_enabled(self):
        """Join-aware mode should preserve table lineage for simple SUM translations."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_cross_table_joins=True)
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        source_value_sql = publisher._measure_translator.try_simple_dax_to_sql(
            "SUM('Inventory Fact'[Source Value Total Stock])"
        )

        assert source_value_sql == "sum(inventory_fact.source_value_total_stock)"

    def test_string_aggregation_pattern_translates_with_string_cast(self):
        """Type B parser support: CONCATENATE(..., MAX(Table[Column])) becomes concat + cast."""
        publisher = DatabricksPublisher(_cfg())

        sql_expr = publisher._measure_translator.try_simple_dax_to_sql(
            "CONCATENATE('Last Refreshed: ', MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))"
        )

        assert sql_expr is not None
        assert sql_expr == "concat('Last Refreshed: ', cast(max(gl_refresh_datetime) as string))"

    def test_string_aggregation_keeps_max_unqualified_with_cross_table_joins_enabled(self):
        """Join-aware mode should not over-qualify MAX in refresh text expressions."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_cross_table_joins=True)
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        sql_expr = publisher._measure_translator.try_simple_dax_to_sql(
            "CONCATENATE('Last Refreshed: ', MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))"
        )

        assert sql_expr is not None
        assert sql_expr == "concat('Last Refreshed: ', cast(max(gl_refresh_datetime) as string))"

    def test_calculate_max_with_filter_translates_to_case_when_max(self):
        """CALCULATE(MAX(...), filter) should translate using MAX(CASE WHEN ...)."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_cross_table_joins=True)
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        sql_expr = publisher._measure_translator.try_simple_dax_to_sql(
            "CALCULATE(MAX('Dates'[FISCAL_YR_PERIOD]), 'Dates'[CAL_DT] = TODAY())"
        )

        assert sql_expr is not None
        assert sql_expr.startswith("MAX(CASE WHEN ")
        assert "`dates`.`CAL_DT` = current_date()" in sql_expr
        assert "THEN `Dates`.`FISCAL_YR_PERIOD` ELSE NULL END" in sql_expr

    def test_var_return_with_today_inlines_and_translates(self):
        """Simple VAR/RETURN expressions should inline scalar vars and translate."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_cross_table_joins=True)
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        sql_expr = publisher._measure_translator.try_simple_dax_to_sql(
            "VAR _today = TODAY() RETURN CALCULATE(MAX('Dates'[FISCAL_YR_PERIOD]), 'Dates'[CAL_DT] = _today)"
        )

        assert sql_expr is not None
        assert sql_expr.startswith("MAX(CASE WHEN ")
        assert "`dates`.`CAL_DT` = (current_date())" in sql_expr
        assert "THEN `Dates`.`FISCAL_YR_PERIOD` ELSE NULL END" in sql_expr

    def test_metric_view_yaml_normalizes_easy_measure_expressions(self):
        """Measure-only datasets should still emit metric-view YAML for easy Tier-1 measures."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_low_confidence_drafts=False)
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Source Value Total Stock",
                    dataset="Project Measures",
                    expression="SUM('Inventory Fact'[Source Value Total Stock])",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="WAC Value Total Stock",
                    dataset="Project Measures",
                    expression="SUM('Inventory Fact'[WAC Value Total Stock])",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    expression='CONCATENATE("Last Refreshed: ", MAX(\'Corporate DSI Last Refreshed\'[GL Refresh Datetime]))',
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="Inventory Fact Last Refreshed",
                    dataset="Project Measures",
                    expression='CONCATENATE("Last Refreshed: ", MAX(\'Inventory Fact Last Refreshed\'[GL Refresh Datetime]))',
                    aggregation=AggregationType.NONE,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_METRIC,
        )

        assert created == 1
        assert skipped == 0
        assert not details
        assert len(stmts) == 1
        yaml_text = stmts[0].lower()
        assert "sum(source_value_total_stock)" in yaml_text
        assert "sum(wac_value_total_stock)" in yaml_text
        assert "concat('last refreshed: ', cast(max(gl_refresh_datetime) as string))" in yaml_text
        assert "gl_refresh_datetime" in yaml_text

    def test_complex_dax_skipped_no_view(self):
        """Complex DAX (CALCULATE, IF, etc.) is skipped — no view created."""
        model = _sales_model(dax_only=True)  # Uses CALCULATE — too complex
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 1
        assert len(stmts) == 0
        assert details[0]["reason"] == DEPLOY_REASON_DAX_NOT_SUPPORTED

    def test_dax_tier_classifier_standard_aggregation(self):
        publisher = DatabricksPublisher(_cfg())
        tier = publisher._measure_translator.classify_dax_measure_tier(
            "SUM('Fact'[Amount])"
        )
        assert tier == TIER_STANDARD_AGGREGATION

    def test_dax_tier_classifier_scalar_system_function(self):
        publisher = DatabricksPublisher(_cfg())
        tier = publisher._measure_translator.classify_dax_measure_tier("TODAY()")
        assert tier == TIER_SCALAR_SYSTEM_FUNCTION

    def test_dax_tier_classifier_filtered_conditional_same_table(self):
        publisher = DatabricksPublisher(_cfg())
        tier = publisher._measure_translator.classify_dax_measure_tier(
            "CALCULATE(SUM('Sales'[Amount]), 'Sales'[Status] = \"Closed\")"
        )
        assert tier == TIER_FILTERED_CONDITIONAL_AGGREGATION

    def test_dax_tier_classifier_relationship_aware_cross_table(self):
        publisher = DatabricksPublisher(_cfg())
        tier = publisher._measure_translator.classify_dax_measure_tier(
            "CALCULATE(SUM('Fact'[Amount]), 'DimDate'[Date] < TODAY())"
        )
        assert tier == TIER_RELATIONSHIP_AWARE_FILTERED_AGGREGATION

    def test_dax_tier_classifier_deferred_for_complex_callout(self):
        publisher = DatabricksPublisher(_cfg())
        tier = publisher._measure_translator.classify_dax_measure_tier(
            "VAR _sel = SELECTEDVALUE('Business Units'[Business Unit]) RETURN SWITCH(TRUE(), ISBLANK(_sel), \"\", \"x\")"
        )
        assert tier == TIER_DEFERRED

    def test_no_group_by_pure_aggregate(self):
        """No group_by_dimensions → pure aggregate (no GROUP BY clause)."""
        model = _sales_model(with_sql_expression=True, with_group_by=False)
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(model)

        assert len(stmts) == 1
        assert "GROUP BY" not in stmts[0]

    def test_explicit_group_by_dimensions(self):
        """group_by_dimensions specified → GROUP BY those columns only."""
        model = _sales_model(with_sql_expression=True, with_group_by=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert len(stmts) == 1
        assert "GROUP BY `REGION`" in stmts[0]
        assert "`REGION`" in stmts[0].split("SELECT")[1].split("FROM")[0]  # In SELECT clause

    def test_view_naming_convention(self):
        """View names follow mv_<model>_<measure> convention."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert "`main`.`public`.`mv_SalesModel_Total_Revenue`" in stmts[0]

    def test_special_chars_in_names_sanitized(self):
        """Special characters in measure names are sanitized for SQL safety."""
        model = SMLModel(
            unique_name="My Model!",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total ($)",
                    dataset="Sales",
                    source_column="AMT",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(model)

        # No special characters in view name
        view_name_match = re.search(r'`mv_([^`]+)`', stmts[0])
        assert view_name_match
        assert "$" not in view_name_match.group(1)
        assert "!" not in view_name_match.group(1)


class TestDependencyValidation:
    """Verify dependency validation catches bad references."""

    def test_missing_dataset_skips_view(self):
        """Measure referencing non-existent dataset is skipped."""
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="bad_measure",
                    dataset="NonExistent",
                    source_column="AMT",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 0
        

    def test_missing_source_column_skips_view(self):
        """Measure referencing non-existent column is skipped."""
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="bad_col_measure",
                    dataset="Sales",
                    source_column="MISSING_COLUMN",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 0
        

    def test_cross_table_measure_skipped(self):
        """Measures with multi-table SQL references are skipped in v1."""
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                ),
                SMLDataset(
                    unique_name="Customers",
                    columns=[SMLColumn(unique_name="REGION", data_type=DataType.STRING)],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="cross_measure",
                    dataset="Sales",
                    sql_expression="SUM(sales.AMT) + COUNT(customers.REGION)",
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 1
        assert details[0]["reason"] == DEPLOY_REASON_CROSS_TABLE


class TestMetadataTableDeployStatus:
    """Verify deploy_status and deploy_reason in metadata table rows."""

    def test_deployed_measure_has_deployed_status(self):
        """Measures with valid SQL have deploy_status=DEPLOYED in metadata."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert f"'{DEPLOY_STATUS_DEPLOYED}'" in sql

    def test_dax_only_measure_has_not_deployed_status(self):
        """DAX-only measures have deploy_status=NOT_DEPLOYED in metadata."""
        model = _sales_model(dax_only=True)
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert f"'{DEPLOY_STATUS_NOT_DEPLOYED}'" in sql
        assert DEPLOY_REASON_DAX_NOT_SUPPORTED in sql

    def test_metadata_table_has_status_and_translation_columns(self):
        """Metadata table DDL includes deploy_status, deploy_reason, and translation_type."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts = publisher.generate_sql_statements(model)
        create_table = next(s for s in stmts if s.startswith("CREATE TABLE"))

        assert "deploy_status STRING" in create_table
        assert "deploy_reason STRING" in create_table
        assert "translation_type STRING" in create_table

    def test_deployed_measure_has_translation_type_in_metadata(self):
        """Deployed measures include translation_type in INSERT row."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert TRANSLATION_TYPE_AGGREGATION_BUILT in sql

    def test_sql_native_translation_type(self):
        """Measures with sql_expression get SQL_NATIVE translation type."""
        model = _sales_model(with_sql_expression=True)
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert TRANSLATION_TYPE_SQL_NATIVE in sql

    def test_schema_evolution_uses_drop_create(self):
        """Schema evolution uses DROP+CREATE instead of CREATE IF NOT EXISTS."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts = publisher.generate_sql_statements(model)

        assert any("DROP TABLE IF EXISTS" in s for s in stmts)
        assert not any("CREATE TABLE IF NOT EXISTS" in s for s in stmts)


class TestCombinedViewMode:
    """Verify combined view mode groups measures by dataset."""

    def test_combined_view_single_dataset(self):
        """Combined mode creates one view per dataset with all measures."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(measure_view_mode="combined")
        )
        model = SMLModel(
            unique_name="MyModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="UNITS", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="Sales",
                    source_column="REVENUE",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Total_Units",
                    dataset="Sales",
                    source_column="UNITS",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert created == 1  # One combined view (not two)
        assert skipped == 0
        assert "SUM(`REVENUE`) AS `Total_Revenue`" in stmts[0]
        assert "SUM(`UNITS`) AS `Total_Units`" in stmts[0]
        assert "measures" in stmts[0]  # View name contains 'measures' suffix

    def test_views_disabled_via_behavior(self):
        """Setting create_measure_views=false produces no views."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(create_measure_views=False)
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 0
        assert len(stmts) == 0

    def test_dax_translation_disabled_via_feature_flag(self):
        """When enable_simple_dax_translation=false, simple DAX is skipped."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_simple_dax_translation=False)
        )
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="total",
                    dataset="Sales",
                    expression="SUM('Sales'[AMT])",
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 1  # Skipped because translation is disabled

    def test_combined_sql_mode_hard_stops_when_source_prerequisite_missing(self):
        """Combined SQL/materialized mode must fail closed instead of falling through to per-dataset views."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="sql_view",
            )
        )
        model = SMLModel(
            unique_name="Device",
            datasets=[
                SMLDataset(
                    unique_name="device_inventory",
                    columns=[
                        SMLColumn(unique_name="inventory_count", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Inventory_Count",
                    dataset="device_inventory",
                    source_column="inventory_count",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value=None,
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert created == 0
        assert stmts == []
        assert skipped == 1
        

    def test_combined_sql_mode_renames_measure_alias_when_it_matches_dimension(self):
        """Combined Databricks views must avoid duplicate projected names."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="sql_view",
            )
        )
        model = SMLModel(
            unique_name="ClientData",
            datasets=[
                SMLDataset(
                    unique_name="fact_ops",
                    columns=[
                        SMLColumn(unique_name="daily_delivery_ld_rate", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="region", data_type=DataType.STRING),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="daily_delivery_ld_rate",
                    dataset="fact_ops",
                    source_column="daily_delivery_ld_rate",
                    aggregation=AggregationType.SUM,
                    group_by_dimensions=["daily_delivery_ld_rate", "region"],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`fact_ops`",
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert created == 1
        assert skipped == 0
        assert "    `daily_delivery_ld_rate`" in stmts[0]
        assert "SUM(`daily_delivery_ld_rate`) AS `daily_delivery_ld_rate_metric`" in stmts[0]

    def test_combined_metric_view_skips_table_qualified_sql_expression(self):
        """Combined mode should skip expressions that reference table-qualified columns."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="metric_view",
            )
        )
        model = SMLModel(
            unique_name="ClientData",
            datasets=[
                SMLDataset(
                    unique_name="REP_SFDC_SBQQ__QUOTE__C",
                    columns=[
                        SMLColumn(unique_name="Deal_Score", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Deal Score",
                    dataset="REP_SFDC_SBQQ__QUOTE__C",
                    sql_expression="SUM(`repsfdccustomerprojectc`.`Deal Score`)",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`rep_sfdc_sbqq__quote__c`",
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 0
        assert skipped == 1
        assert stmts == []
        assert any(d.get("reason") == "CROSS_TABLE_NOT_SUPPORTED" for d in details)

    def test_metric_view_skips_when_source_schema_is_missing_required_columns(self):
        """Metric views should fail closed when the physical source lacks required columns."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                source_table_mapping={"Fact": "main.public.fact"},
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="Fact",
                    sql_expression='SUM(fact."REVENUE")',
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, source: source,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"bu_key"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert len(stmts) == 1
        assert created == 1
        assert skipped == 0

    def test_metric_view_source_column_mapping_overrides_physical_names(self):
        """Metric views should honor explicit semantic-to-physical source column mapping."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                source_table_mapping={"Fact": "main.public.fact"},
                source_column_mapping={
                    "Fact": {
                        "Customer Key": "customer_id",
                    }
                },
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="# of Customers",
                    dataset="Fact",
                    source_column="Customer Key",
                    aggregation=AggregationType.COUNT,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, source: source,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"customer_id"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "`customer_id` AS `Customer_Key`" in stmts[0]

    def test_combined_metric_view_rewrites_alias_qualified_measure_references(self):
        """Combined mode should rewrite f/d1-qualified measure expressions to projected columns."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="metric_view",
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="BU_Key", data_type=DataType.STRING),
                        SMLColumn(unique_name="Customer_Key", data_type=DataType.STRING),
                        SMLColumn(unique_name="Scenario_Key", data_type=DataType.STRING),
                        SMLColumn(unique_name="Product_Key", data_type=DataType.STRING),
                        SMLColumn(unique_name="YearPeriod", data_type=DataType.STRING),
                    ],
                ),
                SMLDataset(
                    unique_name="BU",
                    columns=[
                        SMLColumn(unique_name="BU_Key", data_type=DataType.STRING),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="REL_FACT_BU_KEY__BU_BU_KEY",
                    from_dataset="Fact",
                    from_columns=["BU_Key"],
                    to_dataset="BU",
                    to_columns=["BU_Key"],
                    cardinality="many-to-one",
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Revenue Plus BU",
                    dataset="Fact",
                    sql_expression="SUM(f.Revenue) + SUM(d1.BU_Key)",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, source: source,
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert stmts
        assert "f.Revenue" not in stmts[0]
        assert "d1.BU_Key" not in stmts[0]
        assert "`Revenue`" in stmts[0]
        assert "`BU_BU_Key`" in stmts[0]

    def test_combined_sql_mode_skips_when_source_schema_is_missing_required_columns(self):
        """Combined SQL views should fail closed when required source columns are absent."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="sql_view",
                source_table_mapping={"Fact": "main.public.fact"},
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="Fact",
                    sql_expression='SUM(fact."REVENUE")',
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, source: source,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"bu_key"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert len(stmts) == 1
        assert created == 1
        assert skipped == 0

    def test_combined_sql_mode_does_not_emit_python_none_literals(self):
        """Combined SQL should never render unresolved measures as Python None literals."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="sql_view",
                enable_low_confidence_drafts=True,
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(
                    unique_name="Project Measures",
                    columns=[],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Today",
                    dataset="Project Measures",
                    expression="TODAY()",
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="Corporate IOH",
                    dataset="Project Measures",
                    expression="VAR _today = [Today] RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < _today)",
                    aggregation=AggregationType.NONE,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Project_Measures`",
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert created == 1
        assert len(stmts) == 1
        assert " None AS `Corporate_IOH`" not in stmts[0]
        assert "CAST(NULL AS DOUBLE) AS `Corporate_IOH`" in stmts[0]

    def test_combined_sql_mode_renders_project_measures_scalar_sum_and_refresh_max(self):
        """Project Measures should compile with scalar-subquery SUMs and local MAX refresh measures."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="sql_view",
                enable_cross_table_joins=True,
                enable_low_confidence_drafts=True,
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
                SMLDataset(
                    unique_name="Inventory Fact",
                    columns=[
                        SMLColumn(unique_name="Source Value Total Stock", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="WAC Value Total Stock", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="Corporate DSI Last Refreshed",
                    columns=[
                        SMLColumn(unique_name="GL_Refresh_Datetime", data_type=DataType.DATE),
                    ],
                ),
                SMLDataset(
                    unique_name="Inventory Fact Last Refreshed",
                    columns=[
                        SMLColumn(unique_name="GL_Refresh_Datetime", data_type=DataType.DATE),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Today",
                    dataset="Project Measures",
                    expression="TODAY()",
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="Source Value Total Stock",
                    dataset="Project Measures",
                    expression="SUM('Inventory Fact'[Source Value Total Stock])",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    expression='CONCATENATE("Last Refreshed: ", MAX(\'Corporate DSI Last Refreshed\'[GL_Refresh_Datetime]))',
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="Inventory Fact Last Refreshed",
                    dataset="Project Measures",
                    expression='CONCATENATE("Last Refreshed: ", MAX(\'Inventory Fact Last Refreshed\'[GL_Refresh_Datetime]))',
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="WAC Value Total Stock",
                    dataset="Project Measures",
                    expression="SUM('Inventory Fact'[WAC Value Total Stock])",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, expected: expected,
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override="sql_view",
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "(SELECT SUM(`source_value_total_stock`) FROM `main`.`public`.`Inventory_Fact`)" in stmts[0]
        assert "(SELECT SUM(`wac_value_total_stock`) FROM `main`.`public`.`Inventory_Fact`)" in stmts[0]
        assert "CAST(NULL AS DOUBLE) AS `gl_refresh_datetime`" in stmts[0]

    def test_combined_sql_mode_downgrades_unresolved_quoted_columns_to_draft(self):
        """Combined SQL should not emit unresolved quoted columns from external table refs."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="sql_view",
                enable_low_confidence_drafts=True,
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(
                    unique_name="Project Measures",
                    columns=[],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    expression="CONCATENATE('Last Refreshed: ', MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))",
                    aggregation=AggregationType.NONE,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Project_Measures`",
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"_semabridge_placeholder"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert created == 1
        assert len(stmts) == 1
        assert "MAX(`GL_Refresh_Datetime`)" not in stmts[0]
        assert "CAST(NULL AS DOUBLE) AS `Corporate_DSI_Last_Refreshed`" in stmts[0]

    def test_combined_mode_emits_synthetic_view_for_dataset_without_metrics_when_enabled(self):
        """Combined mode should attempt all datasets when emit_metric_views_for_all_datasets is enabled."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="sql_view",
                emit_metric_views_for_all_datasets=True,
            )
        )
        model = SMLModel(
            unique_name="CoverageModel",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[SMLColumn(unique_name="amount", data_type=DataType.DECIMAL)],
                ),
                SMLDataset(
                    unique_name="DimOnly",
                    columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Amount",
                    dataset="Fact",
                    source_column="amount",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, expected: expected,
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert created == 2
        assert len(stmts) == 2
        assert any("mv_CoverageModel_DimOnly_measures" in stmt for stmt in stmts)
        assert any("COUNT(*) AS `total_rows`" in stmt for stmt in stmts)

    def test_salesforce_style_source_alias_view_is_generated(self):
        """Databricks shim views should expose Salesforce field names over snake_case tables."""
        dataset = SMLDataset(
            unique_name="REP_SFDC_SBQQ__QUOTE__C",
            columns=[
                SMLColumn(unique_name="SBQQ__Opportunity2__c", data_type=DataType.STRING),
                SMLColumn(unique_name="Customer_Project__c", data_type=DataType.STRING),
                SMLColumn(unique_name="Intake_Form__c", data_type=DataType.STRING),
                SMLColumn(unique_name="Building_Code__c", data_type=DataType.STRING),
                SMLColumn(unique_name="SBQQ__Account__c", data_type=DataType.STRING),
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        with patch.object(publisher, "_check_source_table_exists", return_value=True):
            stmts = publisher._build_source_alias_view_statements(
                dataset,
                "`main`.`public`.`REP_SFDC_SBQQ__QUOTE__C`",
            )

        assert len(stmts) == 1
        sql = stmts[0]
        assert "CREATE OR REPLACE VIEW `main`.`public`.`REP_SFDC_SBQQ__QUOTE__C`" in sql
        assert "`sbqq_opportunity2_c` AS `SBQQ__Opportunity2__c`" in sql
        assert "`customer_project_c` AS `Customer_Project__c`" in sql
        assert "`intake_form_c` AS `Intake_Form__c`" in sql
        assert "`building_code_c` AS `Building_Code__c`" in sql
        assert "`sbqq_account_c` AS `SBQQ__Account__c`" in sql

    def test_shell_table_ddl_deduplicates_sanitized_column_names(self):
        """Auto-init table DDL should stay valid when source columns sanitize to the same name."""
        dataset = SMLDataset(
            unique_name="fact_ops",
            columns=[
                SMLColumn(unique_name="daily delivery ld rate", data_type=DataType.DECIMAL),
                SMLColumn(unique_name="daily_delivery_ld_rate", data_type=DataType.DECIMAL),
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts = publisher._build_shell_table_statements(dataset, "main.public.fact_ops")

        assert len(stmts) == 2
        assert "`daily_delivery_ld_rate` DECIMAL(38, 10)" in stmts[1]
        assert "`daily_delivery_ld_rate_col` DECIMAL(38, 10)" in stmts[1]

    def test_metric_view_yaml_deduplicates_dimension_projection_names(self):
        """Metric view YAML should use unique projected names for colliding dataset columns."""
        dataset = SMLDataset(
            unique_name="fact_ops",
            columns=[
                SMLColumn(unique_name="daily delivery ld rate", data_type=DataType.DECIMAL),
                SMLColumn(unique_name="daily_delivery_ld_rate", data_type=DataType.DECIMAL),
            ],
        )
        model = SMLModel(unique_name="ClientData", datasets=[dataset], metrics=[])
        publisher = DatabricksPublisher(_cfg())

        with patch.object(publisher, "_get_source_table_columns", return_value=set()):
            bindings = publisher._build_metric_view_column_bindings(
                dataset,
                "`main`.`public`.`fact_ops`",
                model,
            )
            yaml_text = publisher._generate_metric_view_yaml(
                model,
                dataset,
                "`main`.`public`.`fact_ops`",
                [
                    ResolvedMeasure(
                        name="total_rows",
                        sql_expression="COUNT(*)",
                        translation_type=TRANSLATION_TYPE_SQL_NATIVE,
                        confidence=CONFIDENCE_HIGH,
                    )
                ],
                bindings,
            )

        assert "CAST(NULL AS DECIMAL(38, 10)) AS `daily_delivery_ld_rate`" in yaml_text
        assert "CAST(NULL AS DECIMAL(38, 10)) AS `daily_delivery_ld_rate_dim`" in yaml_text
        assert '  - name: "daily_delivery_ld_rate"' in yaml_text
        assert '  - name: "daily_delivery_ld_rate_dim"' in yaml_text

    def test_metric_view_yaml_escapes_special_characters(self):
        """Metric-view YAML should remain parseable with quote-heavy names/labels."""
        dataset = SMLDataset(
            unique_name="Inventory",
            label='Inventory "Primary"',
            columns=[
                SMLColumn(unique_name="fiscalMonth", data_type=DataType.STRING),
            ],
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model Project",
            label='Inventory "Semantic" Model',
            datasets=[dataset],
            metrics=[],
        )
        publisher = DatabricksPublisher(_cfg())

        with patch.object(publisher, "_get_source_table_columns", return_value={"fiscalmonth"}):
            bindings = publisher._build_metric_view_column_bindings(
                dataset,
                "`main`.`public`.`inventory`",
                model,
            )
            yaml_text = publisher._generate_metric_view_yaml(
                model,
                dataset,
                "`main`.`public`.`inventory`",
                [
                    ResolvedMeasure(
                        name='Corporate DSI "Monthly"',
                        sql_expression="SUM(CASE WHEN `fiscalMonth` < '2026-04' THEN `fiscalMonth` ELSE '0' END)",
                        translation_type=TRANSLATION_TYPE_DAX_TRANSLATED,
                        confidence=CONFIDENCE_MEDIUM,
                    )
                ],
                bindings,
            )

        parsed = yaml.safe_load(yaml_text)
        assert parsed["version"] == 1.1
        assert "Semabridge:" in parsed["comment"]
        assert parsed["measures"][0]["name"] == 'Corporate DSI "Monthly"'
        assert "SUM(CASE WHEN" in parsed["measures"][0]["expr"]

    def test_metric_view_yaml_handles_multiline_original_dax_warning(self):
        """Low-confidence draft warnings should not leak raw multiline DAX into YAML."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_low_confidence_drafts=True)
        )
        dataset = SMLDataset(
            unique_name="Project Measures",
            columns=[
                SMLColumn(unique_name="FISCAL_YR_PERIOD", data_type=DataType.INTEGER),
            ],
        )
        model = SMLModel(unique_name="FabricModel", datasets=[dataset], metrics=[])
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(publisher, "_get_source_table_columns", return_value={"fiscal_yr_period"}):
            bindings = publisher._build_metric_view_column_bindings(
                dataset,
                "`main`.`public`.`Project_Measures`",
                model,
            )
            yaml_text = publisher._generate_metric_view_yaml(
                model,
                dataset,
                "`main`.`public`.`Project_Measures`",
                [
                    ResolvedMeasure(
                        name="Corporate_IOH",
                        sql_expression="CAST(NULL AS DOUBLE)",
                        translation_type=TRANSLATION_TYPE_DAX_SKIPPED,
                        confidence=CONFIDENCE_LOW,
                        original_dax=(
                            "Var _today = [Today]\n"
                            "Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)\n"
                            "RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)"
                        ),
                        warnings=["DAX translation deferred to Tier-5 batch (Tier 4)"],
                    )
                ],
                bindings,
            )

        parsed = yaml.safe_load(yaml_text)
        assert parsed["measures"][0]["name"] == "Corporate_IOH"
        assert "\nVar fiscalMonth" not in yaml_text

    def test_metric_view_unresolved_quoted_column_downgrades_to_draft(self):
        """Unknown quoted identifiers should not be emitted as deployable metric expressions."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                enable_low_confidence_drafts=True,
                enable_cross_table_joins=True,
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model Project",
            datasets=[
                SMLDataset(
                    unique_name="Project Measures",
                    columns=[],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    expression="CONCATENATE(\"Last Refreshed: \", MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))",
                    aggregation=AggregationType.NONE,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Project_Measures`",
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"other_column"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert len(stmts) == 1
        assert "CAST(NULL AS DOUBLE)" in stmts[0]
        assert "MAX(`GL_Refresh_Datetime`)" not in stmts[0]

    def test_metric_view_yaml_uses_empty_dimensions_list_when_no_bindings(self):
        """Metric-view YAML should emit dimensions as [] instead of null when no bindings exist."""
        dataset = SMLDataset(unique_name="Project Measures", columns=[])
        model = SMLModel(unique_name="FabricModel", datasets=[dataset], metrics=[])
        publisher = DatabricksPublisher(_cfg())

        yaml_text = publisher._generate_metric_view_yaml(
            model,
            dataset,
            "`main`.`public`.`Project_Measures`",
            [
                ResolvedMeasure(
                    name="Today",
                    sql_expression="current_date()",
                    translation_type=TRANSLATION_TYPE_DAX_TRANSLATED,
                    confidence=CONFIDENCE_HIGH,
                )
            ],
            [],
        )

        parsed = yaml.safe_load(yaml_text)
        assert parsed["dimensions"] == []
        assert parsed["measures"][0]["name"] == "Today"

    def test_metric_view_rewrite_uses_physical_columns_when_bindings_missing(self):
        """When semantic bindings are missing, physical source columns should keep simple measures deployable."""
        dataset = SMLDataset(unique_name="Project Measures", columns=[])
        publisher = DatabricksPublisher(_cfg())

        with patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"source_value_total_stock"},
        ):
            rewritten = publisher._rewrite_metric_view_measure_expression(
                "SUM(`Source_Value_Total_Stock`)",
                dataset,
                "`main`.`public`.`Project_Measures`",
                [],
            )

        assert rewritten == "SUM(`Source_Value_Total_Stock`)"

    def test_metric_view_rewrites_same_dataset_qualified_sql_and_projects_hidden_columns(self):
        """Metric views should project hidden measure columns and rewrite same-table SQL refs."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                source_table_mapping={"Fact": "main.public.fact"},
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER, is_key=True, is_hidden=True),
                        SMLColumn(unique_name="Product Key", data_type=DataType.INTEGER, is_key=True, is_hidden=True),
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL, is_hidden=True),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="Fact",
                    sql_expression='SUM(fact."REVENUE")',
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="# of Customers",
                    dataset="Fact",
                    sql_expression='COUNT(DISTINCT fact."CUSTOMER_KEY")',
                    aggregation=AggregationType.COUNT_DISTINCT,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, source: source,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"customer_key", "fact_product_key", "revenue"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "`customer_key` AS `Customer_Key`" in stmts[0]
        assert "`fact_product_key` AS `Product_Key`" in stmts[0]
        assert "`revenue` AS `Revenue`" in stmts[0]
        assert 'expr: SUM(`Revenue`)' in stmts[0]
        assert 'expr: COUNT(DISTINCT `Customer_Key`)' in stmts[0]
        assert 'fact."REVENUE"' not in stmts[0]

    def test_metric_view_skips_quoted_cross_table_sql_expression(self):
        """Metric views should skip quoted SQL that still references another dataset."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                source_table_mapping={"Fact": "main.public.fact"},
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL, is_hidden=True),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Revenue Budget",
                    dataset="Fact",
                    sql_expression='SUM(CASE WHEN scenario."SCENARIO" = \'Budget\' THEN fact."REVENUE" ELSE 0 END)',
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, source: source,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"revenue"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert len(stmts) == 1
        assert created == 1
        assert skipped == 0
        assert any(d.get("reason") == DEPLOY_REASON_CROSS_TABLE for d in details)


class TestAggregationTypes:
    """Verify all aggregation types generate correct SQL."""

    @pytest.mark.parametrize(
        "agg_type,expected_sql",
        [
            (AggregationType.SUM, "SUM(`AMT`)"),
            (AggregationType.COUNT, "COUNT(`AMT`)"),
            (AggregationType.COUNT_DISTINCT, "COUNT(DISTINCT `AMT`)"),
            (AggregationType.AVG, "AVG(`AMT`)"),
            (AggregationType.MIN, "MIN(`AMT`)"),
            (AggregationType.MAX, "MAX(`AMT`)"),
        ],
    )
    def test_aggregation_type_generates_correct_sql(self, agg_type, expected_sql):
        """Each aggregation type maps to the correct SQL function."""
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Facts",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="metric_1",
                    dataset="Facts",
                    source_column="AMT",
                    aggregation=agg_type,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert expected_sql in stmts[0]


class TestEmptyEdgeCases:
    """Verify edge cases with empty models."""

    def test_no_metrics_produces_databricks_only_fallback_metric_view(self):
        """Databricks generates a native fallback metric view when SML has zero metrics."""
        model = SMLModel(
            unique_name="empty",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="X", data_type=DataType.STRING)],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        assert len(stmts) == 1
        assert "CREATE OR REPLACE VIEW" in stmts[0]
        assert "WITH METRICS LANGUAGE YAML" in stmts[0]
        assert "COUNT(*)" in stmts[0]
        assert "total_rows" in stmts[0]

    def test_generate_sql_statements_injects_fallback_metric_for_databricks(self):
        """Databricks SQL generation injects a fallback metric when SML has none."""
        model = SMLModel(
            unique_name="empty",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="X", data_type=DataType.STRING)],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert "CREATE OR REPLACE VIEW" in sql
        assert "WITH METRICS LANGUAGE YAML" in sql
        assert "total_rows" in sql
        assert "COUNT(*)" in sql

    def test_zero_metrics_fallback_emits_metric_view_with_row_count(self):
        """Zero-metrics fallback should still emit a native metric view with Row Count."""
        model = SMLModel(
            unique_name="empty",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="X", data_type=DataType.STRING),
                        SMLColumn(unique_name="Y", data_type=DataType.INTEGER),
                    ],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert "CREATE OR REPLACE VIEW" in sql
        assert "WITH METRICS LANGUAGE YAML" in sql
        assert "COUNT(*)" in sql

    def test_generate_sql_statements_does_not_mutate_canonical_sml(self):
        """Fallback metric injection is Databricks-local and must not mutate canonical SML."""
        model = SMLModel(
            unique_name="empty",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="X", data_type=DataType.STRING)],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        _ = publisher.generate_sql_statements(model)

        assert len(model.metrics) == 0

    def test_source_resolution_prefers_dataset_database_schema(self):
        """Explicit Fabric dataset database/schema should win over behavior defaults."""
        model = SMLModel(
            unique_name="empty",
            datasets=[
                SMLDataset(
                    unique_name="DEVICE_INVENTORY",
                    source_database="lakehouse",
                    source_schema="public",
                    source_table="DEVICE_INVENTORY",
                    columns=[SMLColumn(unique_name="X", data_type=DataType.STRING)],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts = publisher.generate_sql_statements(model)
        sql = "\n".join(stmts)

        assert "`lakehouse`.`public`.`device_inventory`" in sql

    def test_metadata_table_can_be_disabled_for_metric_view_only(self):
        """When create_metadata_table=false, no CREATE/DROP/INSERT metadata SQL is emitted."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                create_metadata_table=False,
                measure_view_type="metric_view",
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts = publisher.generate_sql_statements(model)
        sql = "\n".join(stmts)

        assert "CREATE TABLE" not in sql
        assert "DROP TABLE IF EXISTS" not in sql
        assert "INSERT INTO" not in sql
        assert "WITH METRICS LANGUAGE YAML" in sql

    def test_batch_insert_count_matches_objects(self):
        """All dimensions + measures appear in metadata INSERT rows."""
        model = SMLModel(
            unique_name="batch_model",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="region", data_type=DataType.STRING),
                        SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(unique_name="revenue", dataset="Sales"),
                SMLMetric(unique_name="margin", dataset="Sales"),
            ],
        )

        statements = DatabricksPublisher(_cfg()).generate_sql_statements(model)
        insert_statements = [s for s in statements if s.startswith("INSERT INTO `main`.`public`.`batch_model`")]

        # One batched INSERT for all rows
        assert len(insert_statements) == 1
        # 2 dimensions + 2 measures = 4 rows → 4 current_timestamp() calls
        assert insert_statements[0].count("current_timestamp()") == 4


class TestDaxToSqlTranslation:
    """Verify simple DAX-to-SQL translation for Databricks views."""

    def _make_metric_model(self, dax_expression: str) -> SMLModel:
        return SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="AMOUNT", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="REGION", data_type=DataType.STRING),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="metric_1",
                    dataset="Sales",
                    expression=dax_expression,
                )
            ],
        )

    @pytest.mark.parametrize(
        "dax,expected_sql",
        [
            ("SUM('Sales'[AMOUNT])", "SUM(`AMOUNT`)"),
            ("COUNT('Sales'[AMOUNT])", "COUNT(`AMOUNT`)"),
            ("AVERAGE('Sales'[AMOUNT])", "AVG(`AMOUNT`)"),
            ("MIN('Sales'[AMOUNT])", "MIN(`AMOUNT`)"),
            ("MAX('Sales'[AMOUNT])", "MAX(`AMOUNT`)"),
            ("DISTINCTCOUNT('Sales'[AMOUNT])", "COUNT(DISTINCT `AMOUNT`)"),
            ("COUNTROWS('Sales')", "COUNT(*)"),
            ("COUNTBLANK('Sales'[AMOUNT])", "COUNT_IF(`AMOUNT` IS NULL)"),
            # Without table qualifier
            ("SUM([AMOUNT])", "SUM(`AMOUNT`)"),
            ("COUNT([AMOUNT])", "COUNT(`AMOUNT`)"),
        ],
    )
    def test_simple_dax_patterns_translate(self, dax, expected_sql):
        """Each simple DAX pattern maps to correct Databricks SQL."""
        model = self._make_metric_model(dax)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(model)

        assert created == 1, f"Expected view creation for DAX: {dax}"
        assert expected_sql in stmts[0], f"Expected '{expected_sql}' in SQL for DAX: {dax}"

    @pytest.mark.parametrize(
        "complex_dax",
        [
            "CALCULATE(SUM('Sales'[AMOUNT]), ALL('Date'))",
            "IF(ISBLANK([Revenue]), 0, [Revenue])",
            "SUMX('Sales', 'Sales'[Price] * 'Sales'[Qty])",
            "DIVIDE([Revenue], [Units], 0)",
        ],
    )
    def test_complex_dax_not_translated(self, complex_dax):
        """Complex DAX patterns are NOT translated — measure is skipped."""
        model = self._make_metric_model(complex_dax)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0, f"Should not create view for complex DAX: {complex_dax}"
        assert skipped == 1

    @pytest.mark.parametrize(
        "invalid_dax",
        [
            "SUM('Sales'[Revenue] + 10)",       # Arithmetic inside aggregation
            "SUM('Sales'[Revenue]) / 100",       # Post-aggregation arithmetic
            "SUM('Sales'[Revenue]) + SUM('Sales'[Cost])",  # Multi-aggregation
            "",                                  # Empty expression
            "   ",                               # Whitespace only
        ],
    )
    def test_invalid_dax_edge_cases_rejected(self, invalid_dax):
        """Edge case DAX patterns must NOT produce views (risk of broken SQL)."""
        model = self._make_metric_model(invalid_dax)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0, f"Should reject invalid DAX: '{invalid_dax}'"


class TestMetricViewGeneration:
    """Tests for native Databricks Metric View (WITH METRICS LANGUAGE YAML)."""

    def test_metric_view_yaml_contains_version(self):
        """Generated YAML includes version 1.1 per Databricks spec."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "version: 1.1" in stmts[0]

    def test_metric_view_has_with_metrics_syntax(self):
        """Generated SQL uses WITH METRICS LANGUAGE YAML syntax."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "WITH METRICS LANGUAGE YAML" in stmts[0]
        assert "$$" in stmts[0]

    def test_metric_view_contains_dimensions(self):
        """Generated YAML includes dataset dimensions."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "dimensions:" in stmts[0]
        assert "REVENUE" in stmts[0]

    def test_metric_view_contains_measures(self):
        """Generated YAML includes resolved measures."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "measures:" in stmts[0]
        assert "SUM" in stmts[0]

    def test_metric_view_source_inline_sql_when_no_mapping(self):
        """Without source_table_mapping, source is inline SQL query."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        # Without mapping, source uses inline SQL with CAST(NULL AS type)
        assert "source: |" in stmts[0]
        assert "SELECT" in stmts[0]
        assert "CAST(NULL AS" in stmts[0]

    def test_metric_view_source_table_mapping(self):
        """Explicit source_table_mapping overrides default."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                source_table_mapping={"Sales": "analytics.raw.sales_data"},
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, _, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert "source: analytics.raw.sales_data" in stmts[0]

    def test_metric_view_source_catalog_schema_no_mapping(self):
        """Without explicit mapping, source_catalog/schema are NOT used for metric view source."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                source_catalog="my_catalog",
                source_schema="my_schema",
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, _, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        # Without explicit mapping, inline SQL is used (not catalog/schema)
        assert "source: |" in stmts[0]
        assert "SELECT" in stmts[0]

    def test_metric_view_combined_mode_emits_single_joined_view(self):
        """Combined metric-view mode should emit one model-level view with joins."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
            )
        )
        model = SMLModel(
            unique_name="device_model",
            datasets=[
                SMLDataset(
                    unique_name="device_inventory",
                    columns=[
                        SMLColumn(unique_name="inventory_count", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                    ],
                ),
                SMLDataset(
                    unique_name="device_date",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="month", data_type=DataType.INTEGER),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Inventory_Count",
                    dataset="device_inventory",
                    source_column="inventory_count",
                    aggregation=AggregationType.SUM,
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="device_inventory_to_date",
                    from_dataset="device_inventory",
                    from_columns=["date_id"],
                    to_dataset="device_date",
                    to_columns=["date_id"],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, _ = publisher.generate_measure_view_statements(
            model,
            view_type_override="metric_view",
        )

        assert created == 1
        assert skipped == 0
        assert "WITH METRICS LANGUAGE YAML" in stmts[0]
        assert "LEFT JOIN" in stmts[0]
        assert "Inventory_Count" in stmts[0]
        assert "year" in stmts[0]
        assert "month" in stmts[0]

    def test_metric_view_per_dataset_mode_still_emits_separate_views(self):
        """Per-measure/per-dataset metric-view mode should keep legacy multi-view behavior."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="per_measure",
            )
        )
        model = SMLModel(
            unique_name="device_model",
            datasets=[
                SMLDataset(
                    unique_name="device_inventory",
                    columns=[
                        SMLColumn(unique_name="inventory_count", data_type=DataType.INTEGER),
                    ],
                ),
                SMLDataset(
                    unique_name="device_date",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Inventory_Count",
                    dataset="device_inventory",
                    source_column="inventory_count",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Date_Rows",
                    dataset="device_date",
                    expression="COUNTROWS('device_date')",
                    aggregation=AggregationType.COUNT,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, _ = publisher.generate_measure_view_statements(
            model,
            view_type_override="metric_view",
        )

        assert created == 2
        assert skipped == 0
        assert len(stmts) == 2

    def test_combined_metric_view_publish_with_mocked_table_existence_contains_join_sql(self):
        """Mock Databricks execution so publish validates info_schema and emits JOIN view SQL."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=False,
            )
        )
        model = SMLModel(
            unique_name="device_model",
            datasets=[
                SMLDataset(
                    unique_name="device_inventory",
                    columns=[
                        SMLColumn(unique_name="inventory_count", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                    ],
                ),
                SMLDataset(
                    unique_name="device_date",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Inventory_Count",
                    dataset="device_inventory",
                    source_column="inventory_count",
                    aggregation=AggregationType.SUM,
                )
            ],
            relationships=[
                SMLRelationship(
                    unique_name="rel_inventory_date",
                    from_dataset="device_inventory",
                    from_columns=["date_id"],
                    to_dataset="device_date",
                    to_columns=["date_id"],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []

        def _mock_execute(statements):
            executed_sql.extend(statements)
            sql = statements[0].upper()
            if "INFORMATION_SCHEMA.TABLES" in sql and "SELECT COUNT(1) AS CNT" in sql:
                return [{"result": {"data_array": [["1"]]}}]
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
            publisher.publish(model)

        assert any("INFORMATION_SCHEMA.TABLES" in s.upper() for s in executed_sql)
        create_view_sql = next(s for s in executed_sql if "WITH METRICS LANGUAGE YAML" in s)
        assert "LEFT JOIN" in create_view_sql
        assert "INVENTORY_COUNT" in create_view_sql.upper()

    def test_combined_metric_view_auto_discovers_source_schema_when_default_missing(self):
        """When default schema misses table, publisher discovers existing schema and still creates view."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=False,
            )
        )
        model = SMLModel(
            unique_name="device_model",
            datasets=[
                SMLDataset(
                    unique_name="device_inventory",
                    columns=[
                        SMLColumn(unique_name="inventory_count", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                    ],
                ),
                SMLDataset(
                    unique_name="device_date",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Inventory_Count",
                    dataset="device_inventory",
                    source_column="inventory_count",
                    aggregation=AggregationType.SUM,
                )
            ],
            relationships=[
                SMLRelationship(
                    unique_name="rel_inventory_date",
                    from_dataset="device_inventory",
                    from_columns=["date_id"],
                    to_dataset="device_date",
                    to_columns=["date_id"],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []

        def _mock_execute(statements):
            executed_sql.extend(statements)
            sql = statements[0].upper()
            # First existence checks for expected schema return missing.
            if "SELECT COUNT(1) AS CNT" in sql and "DEVICE_INVENTORY" in sql:
                return [{"result": {"data_array": [["0"]]}}]
            if "SELECT LOWER(TABLE_SCHEMA) AS TABLE_SCHEMA" in sql and "DEVICE_INVENTORY" in sql:
                return [{"result": {"data_array": [["raw"]]}}]
            # Date table exists where expected.
            if "SELECT COUNT(1) AS CNT" in sql and "DEVICE_DATE" in sql:
                return [{"result": {"data_array": [["1"]]}}]
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
            publisher.publish(model)

        create_view_sql = next(s for s in executed_sql if "WITH METRICS LANGUAGE YAML" in s)
        assert "`main`.`raw`.`device_inventory`" in create_view_sql.lower()
        assert "LEFT JOIN" in create_view_sql

    def test_dataset_name_fallback_resolves_when_heuristic_source_name_missing(self):
        """If source_table-derived name misses, fallback to dataset unique_name path."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
            )
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        ds = SMLDataset(unique_name="Table", source_table="DEVICE_INVENTORY", columns=[])

        # First call (DEVICE_INVENTORY) fails, second call (Table) succeeds.
        with patch.object(
            publisher,
            "_resolve_existing_source_table",
            side_effect=[None, "`main`.`public`.`table`"],
        ):
            resolved = publisher._resolve_existing_source_for_dataset(
                ds,
                "`main`.`public`.`device_inventory`",
            )

        assert resolved == "`main`.`public`.`table`"

    def test_combined_metric_view_skips_when_prerequisite_table_missing(self):
        """If information_schema pre-check reports missing source table, skip view gracefully."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
            )
        )
        model = SMLModel(
            unique_name="device_model",
            datasets=[
                SMLDataset(
                    unique_name="device_inventory",
                    columns=[
                        SMLColumn(unique_name="inventory_count", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                    ],
                ),
                SMLDataset(
                    unique_name="device_date",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Inventory_Count",
                    dataset="device_inventory",
                    source_column="inventory_count",
                    aggregation=AggregationType.SUM,
                )
            ],
            relationships=[
                SMLRelationship(
                    unique_name="rel_inventory_date",
                    from_dataset="device_inventory",
                    from_columns=["date_id"],
                    to_dataset="device_date",
                    to_columns=["date_id"],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "execute_statements",
            return_value=[{"result": {"data_array": [["0"]]}}],
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override="metric_view",
            )

        assert created == 0
        assert skipped >= 1
        assert stmts == []
        


    def test_metric_view_routes_cross_table_models_to_sql_generation(self):
        """Cross-table measures should route Databricks metric-view mode to join-capable SQL views."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                enable_cross_table_joins=True,
                enable_cross_table_sql_fallback=True,
            )
        )
        model = SMLModel(
            unique_name="CrossTableModel",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                    ],
                ),
                SMLDataset(
                    unique_name="Dim",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="calendar_day", data_type=DataType.DATE),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Cross_Table_Total",
                    dataset="Fact",
                    expression="CALCULATE(SUM('Fact'[amount]), Dim[date_id] < TODAY())",
                    aggregation=AggregationType.SUM,
                )
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_to_dim",
                    from_dataset="Fact",
                    from_columns=["date_id"],
                    to_dataset="Dim",
                    to_columns=["date_id"],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        def _mock_columns(source_table: str):
            lowered = source_table.lower()
            if "fact" in lowered:
                return {"amount", "date_id"}
            if "dim" in lowered:
                return {"date_id", "calendar_day"}
            return set()

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, expected: expected,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            side_effect=_mock_columns,
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override="metric_view",
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "WITH METRICS LANGUAGE YAML" not in stmts[0]
        assert "CREATE OR REPLACE VIEW" in stmts[0]
        assert "LEFT JOIN" in stmts[0]

    def test_simple_table_qualified_sum_is_classified_as_cross_table(self):
        """Canonical SUM('Table'[Column]) should be eligible for cross-table fallback routing."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_cross_table_joins=True)
        )
        model = SMLModel(
            unique_name="SimpleAggModel",
            datasets=[
                SMLDataset(
                    unique_name="Inventory Fact",
                    columns=[
                        SMLColumn(unique_name="Source Value Total Stock", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Source_Value_Total_Stock",
                    dataset="Inventory Fact",
                    expression="SUM('Inventory Fact'[Source Value Total Stock])",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        assert publisher._is_cross_table_measure(model.metrics[0]) is True
    def test_nested_measure_references_resolve_recursively(self):
        """Nested measure refs should expand to SQL instead of collapsing to NULL drafts."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(measure_view_type="metric_view")
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="COGS", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="Fact",
                    source_column="Revenue",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Total COGS",
                    dataset="Fact",
                    source_column="COGS",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Gross Margin",
                    dataset="Fact",
                    expression="[Total Revenue] - [Total COGS]",
                ),
                SMLMetric(
                    unique_name="GM_Pct",
                    dataset="Fact",
                    expression="DIVIDE([Gross Margin], [Total Revenue])",
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`fact`",
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"revenue", "cogs"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "CAST(NULL AS DOUBLE)" not in stmts[0]
        assert "Gross_Margin" in stmts[0]
        assert "SUM(`Revenue`)" in stmts[0]
        assert "SUM(`COGS`)" in stmts[0]
        assert "NULLIF" in stmts[0]
        assert "DIVIDE(" not in stmts[0]

    def test_combined_metric_view_ignores_invalid_relationship_columns(self):
        """Combined view should not emit JOIN predicates for relationship columns missing from datasets."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
            )
        )
        model = SMLModel(
            unique_name="client_data",
            datasets=[
                SMLDataset(
                    unique_name="fact_ops",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="daily_delivery_ld_rate", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="dim_date",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Daily delivery LD Rate (%)",
                    dataset="fact_ops",
                    source_column="daily_delivery_ld_rate",
                    aggregation=AggregationType.SUM,
                )
            ],
            relationships=[
                # Invalid: from column SBQQ_Opportunity2_c does not exist in fact_ops dataset.
                SMLRelationship(
                    unique_name="invalid_fact_join",
                    from_dataset="fact_ops",
                    from_columns=["SBQQ_Opportunity2_c"],
                    to_dataset="dim_date",
                    to_columns=["date_id"],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=[
                "`main`.`public`.`fact_ops`",
                "`main`.`public`.`dim_date`",
            ],
        ):
            stmts, created, skipped, _ = publisher.generate_measure_view_statements(
                model,
                view_type_override="metric_view",
            )

        assert created == 1
        assert skipped == 0
        assert "SBQQ_Opportunity2_c" not in stmts[0]
        assert "LEFT JOIN" not in stmts[0]

    def test_metric_view_view_type_none_returns_empty(self):
        """measure_view_type='none' skips all view generation."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, _ = publisher.generate_measure_view_statements(
            model, view_type_override="none"
        )

        assert created == 0
        assert len(stmts) == 0

    def test_publish_auto_initializes_missing_tables_before_combined_metric_view(self):
        """Publish pre-flight should create missing shell tables before combined metric-view deployment."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
            )
        )
        model = SMLModel(
            unique_name="Device",
            datasets=[
                SMLDataset(
                    unique_name="device_inventory",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="inventory_count", data_type=DataType.INTEGER),
                    ],
                ),
                SMLDataset(
                    unique_name="device_date",
                    columns=[
                        SMLColumn(unique_name="date_id", data_type=DataType.STRING),
                        SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Inventory_Count",
                    dataset="device_inventory",
                    source_column="inventory_count",
                    aggregation=AggregationType.SUM,
                )
            ],
            relationships=[
                SMLRelationship(
                    unique_name="rel_inventory_date",
                    from_dataset="device_inventory",
                    from_columns=["date_id"],
                    to_dataset="device_date",
                    to_columns=["date_id"],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []

        def _mock_execute(statements: list[str]):
            executed_sql.extend(statements)
            return [{"status": {"state": "SUCCEEDED"}} for _ in statements]

        source_resolution_sequence = [
            None,
            None,
            "`main`.`public`.`device_inventory`",
            "`main`.`public`.`device_date`",
        ]

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"):
            with patch.object(
                publisher,
                "_resolve_existing_source_for_dataset",
                side_effect=source_resolution_sequence,
            ):
                with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
                    publisher.publish(model)

        assert any("CREATE TABLE IF NOT EXISTS `main`.`public`.`device_inventory`" in sql for sql in executed_sql)
        assert any("CREATE TABLE IF NOT EXISTS `main`.`public`.`device_date`" in sql for sql in executed_sql)
        assert any("WITH METRICS LANGUAGE YAML" in sql for sql in executed_sql)

    def test_sql_view_fallback_still_works(self):
        """view_type_override='sql_view' uses legacy SQL view generation."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="sql_view"
        )

        assert created >= 1
        # SQL views use plain SELECT, not YAML
        assert "WITH METRICS" not in stmts[0]
        assert "SELECT" in stmts[0]

    def test_publish_falls_back_to_sql_view_when_metric_view_deploy_fails(self):
        """If native metric views fail at deploy time, publish should try SQL views."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=False,
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []

        def _mock_execute(statements: list[str]):
            sql = statements[0]
            executed_sql.append(sql)
            if "WITH METRICS LANGUAGE YAML" in sql:
                raise DatabricksPublishError("metric views not supported on this warehouse")
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
            publisher.publish(model)

        assert any("WITH METRICS LANGUAGE YAML" in sql for sql in executed_sql)
        assert any(
            "CREATE OR REPLACE VIEW" in sql and "WITH METRICS LANGUAGE YAML" not in sql
            for sql in executed_sql
        )

    def test_publish_fails_fast_when_databricks_source_schema_is_incomplete(self):
        """Publish should abort in strict mode when source columns are clearly missing."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                on_missing_source="fail",
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="BU_Key", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="Fact",
                    source_column="Revenue",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_auto_initialize_missing_tables",
            return_value=None,
        ), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`fact`",
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"bu_key"},
        ), patch.object(
            publisher,
            "execute_statements",
        ) as execute_mock:
            with pytest.raises(DatabricksPublishError, match="Schema Mismatch"):
                publisher.publish(model)

        execute_mock.assert_not_called()

    def test_publish_continues_on_databricks_source_schema_mismatch_by_default(self):
        """Default Databricks source validation should warn and continue."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="BU_Key", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="Fact",
                    source_column="Revenue",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_auto_initialize_missing_tables",
            return_value=None,
        ), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`fact`",
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"bu_key"},
        ), patch.object(
            publisher,
            "execute_statements",
            return_value=[],
        ) as execute_mock:
            result = publisher.publish(model)

        assert result == "databricks://main/public/Customer Profitability"
        assert execute_mock.called

    def test_confidence_high_for_native_sql(self):
        """SQL_NATIVE and AGGREGATION_BUILT get HIGH confidence."""
        publisher = DatabricksPublisher(_cfg())
        assert publisher._assess_confidence(TRANSLATION_TYPE_SQL_NATIVE) == CONFIDENCE_HIGH
        assert publisher._assess_confidence(TRANSLATION_TYPE_AGGREGATION_BUILT) == CONFIDENCE_HIGH

    def test_confidence_medium_for_dax_translated(self):
        """DAX_TRANSLATED gets MEDIUM confidence."""
        publisher = DatabricksPublisher(_cfg())
        assert publisher._assess_confidence(TRANSLATION_TYPE_DAX_TRANSLATED) == CONFIDENCE_MEDIUM

    def test_confidence_none_for_dax_skipped(self):
        """DAX_SKIPPED gets NONE confidence."""
        publisher = DatabricksPublisher(_cfg())
        assert publisher._assess_confidence(TRANSLATION_TYPE_DAX_SKIPPED) == CONFIDENCE_NONE

    def test_metric_view_groups_measures_by_dataset(self):
        """Metric views group all measures per dataset into one view."""
        model = SMLModel(
            unique_name="multi",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="AMOUNT", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="QTY", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="total_amount",
                    dataset="Sales",
                    source_column="AMOUNT",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="total_qty",
                    dataset="Sales",
                    source_column="QTY",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        # Should be ONE metric view with both measures
        assert created == 1
        assert "total_amount" in stmts[0]
        assert "total_qty" in stmts[0]


class TestMaterializedMetricReadyViews:
    """Tests for Databricks materialized metric-ready view mode."""

    def test_materialized_view_has_materialized_syntax_and_tblproperties(self):
        model = _sales_model(with_source_column=True, with_group_by=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override=VIEW_TYPE_MATERIALIZED
        )

        assert created == 1
        assert "CREATE OR REPLACE MATERIALIZED VIEW" in stmts[0]
        assert "TBLPROPERTIES" in stmts[0]
        assert "'semantic_model' = 'true'" in stmts[0]
        assert "'metric_view' = 'true'" in stmts[0]
        assert "'bi.dimensions' = 'REGION'" in stmts[0]
        assert "'bi.measures' = 'Total_Revenue'" in stmts[0]

    def test_materialized_combined_view_lists_multiple_measures(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(measure_view_mode="combined")
        )
        model = SMLModel(
            unique_name="MyModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="REGION", data_type=DataType.STRING),
                        SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="UNITS", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="Sales",
                    source_column="REVENUE",
                    aggregation=AggregationType.SUM,
                    group_by_dimensions=["REGION"],
                ),
                SMLMetric(
                    unique_name="Total_Units",
                    dataset="Sales",
                    source_column="UNITS",
                    aggregation=AggregationType.SUM,
                    group_by_dimensions=["REGION"],
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, _ = publisher.generate_measure_view_statements(
            model, view_type_override=VIEW_TYPE_MATERIALIZED
        )

        assert created == 1
        assert skipped == 0
        assert "CREATE OR REPLACE MATERIALIZED VIEW" in stmts[0]
        assert "'bi.measures' = 'Total_Revenue,Total_Units'" in stmts[0]
