"""Tests for DatabricksPublisher — metadata table + measure views."""

import re
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from semabridge.connectors.databricks_publisher import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
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
    ERROR_CLASS_SQL_SEMANTIC,
    SQL_FALLBACK_FAILED,
    SQL_FALLBACK_IN_PROGRESS,
    SQL_FALLBACK_SUCCESS,
    DatabricksPublishError,
    DatabricksPublisher,
    ResolvedMeasure,
)
from semabridge.converter.common_dax_translator import CommonDAXTranslationResult
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
    Cardinality,
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

def test_databricks_behavior_defaults_keep_compatibility_and_new_flags():
    behavior = DatabricksBehavior()

    assert behavior.emit_metric_views_for_all_datasets is False
    assert behavior.emit_distinct_pk_metric_for_dimension_datasets is True
    assert behavior.strict_graph_coverage_validation is False
    assert behavior.enable_destructive_sync_operations is False
    assert behavior.enable_auto_join_key_bridge is False
    assert behavior.multi_fact_split_mode == "off"
    assert behavior.dummy_measure_anchor_confidence_threshold == pytest.approx(0.8)
    assert behavior.review_required_blocks_deployment is False
    assert behavior.strict_cycle_fail_mode is False
    assert behavior.allow_sql_fallback_on_metric_view_failure is False
    assert behavior.statement_wait_timeout_seconds == 30
    assert behavior.statement_poll_interval_seconds == pytest.approx(3.0)


def test_databricks_statement_tuning_helpers_use_behavior_values():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            statement_wait_timeout_seconds=12,
            statement_poll_interval_seconds=1.25,
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    assert publisher._statement_wait_timeout() == "12s"
    assert publisher._statement_poll_interval_seconds() == pytest.approx(1.25)


def test_databricks_behavior_rejects_invalid_multi_fact_split_mode():
    with pytest.raises(ValidationError):
        DatabricksBehavior(multi_fact_split_mode="invalid")


def test_publish_initializes_semantic_router_and_persists_after_graph_output() -> None:
    model = SMLModel(
        unique_name="RouterModel",
        datasets=[
            SMLDataset(
                unique_name="Fact",
                columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)],
            ),
            SMLDataset(
                unique_name="Dim",
                columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="fact_to_dim",
                from_dataset="Fact",
                from_columns=["dim_id"],
                to_dataset="Dim",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ],
    )
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            semantic_router_enabled=True,
            measure_view_type="sql_view",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    with patch.object(publisher, "_determine_view_type", return_value=VIEW_TYPE_SQL), patch.object(
        publisher,
        "_select_measure_view_mode_for_quota",
        return_value="per_measure",
    ), patch.object(publisher, "_auto_initialize_missing_tables", return_value=None), patch.object(
        publisher,
        "_auto_bridge_relationship_join_keys",
        return_value=None,
    ), patch.object(publisher, "_validate_source_table_schema", return_value=None), patch.object(
        publisher,
        "generate_sql_statements",
        return_value=[],
    ), patch.object(publisher, "execute_statements", return_value=[]), patch.object(
        publisher,
        "_persist_semantic_router_decisions",
        return_value=None,
    ) as persist_mock:
        uri = publisher.publish(model)

    assert uri.startswith("databricks://")
    assert publisher._semantic_graph is not None
    assert publisher._table_categories.get("Fact", {}).get("category") == "FACT"
    persist_mock.assert_called_once()


def test_publish_skips_semantic_router_when_model_is_overridden() -> None:
    model = SMLModel(
        unique_name="LegacyModel",
        datasets=[
            SMLDataset(
                unique_name="Fact",
                columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)],
            ),
            SMLDataset(
                unique_name="Dim",
                columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="fact_to_dim",
                from_dataset="Fact",
                from_columns=["dim_id"],
                to_dataset="Dim",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ],
    )
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            semantic_router_enabled=True,
            semantic_router_override_models=["LegacyModel"],
            measure_view_type="sql_view",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    with patch.object(publisher, "_determine_view_type", return_value=VIEW_TYPE_SQL), patch.object(
        publisher,
        "_select_measure_view_mode_for_quota",
        return_value="per_measure",
    ), patch.object(publisher, "_auto_initialize_missing_tables", return_value=None), patch.object(
        publisher,
        "_auto_bridge_relationship_join_keys",
        return_value=None,
    ), patch.object(publisher, "_validate_source_table_schema", return_value=None), patch.object(
        publisher,
        "generate_sql_statements",
        return_value=[],
    ), patch.object(publisher, "execute_statements", return_value=[]), patch.object(
        publisher,
        "_persist_semantic_router_decisions",
        return_value=None,
    ) as persist_mock:
        publisher.publish(model)

    assert publisher._semantic_graph is None
    assert publisher._table_categories == {}
    persist_mock.assert_not_called()


def test_generate_measure_view_statements_semantic_router_per_fact_splits_metric_views() -> None:
    model = SMLModel(
        unique_name="RouterModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="Returns",
                columns=[
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="Customer",
                columns=[
                    SMLColumn(unique_name="id", data_type=DataType.INTEGER),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="sales_to_customer",
                from_dataset="Sales",
                from_columns=["customer_id"],
                to_dataset="Customer",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            SMLRelationship(
                unique_name="returns_to_customer",
                from_dataset="Returns",
                from_columns=["customer_id"],
                to_dataset="Customer",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Sales Amount",
                dataset="Sales",
                source_column="amount",
                aggregation=AggregationType.SUM,
            ),
            SMLMetric(
                unique_name="Returns Amount",
                dataset="Returns",
                source_column="amount",
                aggregation=AggregationType.SUM,
            ),
        ],
    )
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            semantic_router_enabled=True,
            measure_view_type="metric_view",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    with patch.object(publisher, "_resolve_existing_source_for_dataset", return_value=None):
        publisher._initialize_semantic_router(model)
        statements, created, skipped_count, _ = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_METRIC,
        )

    assert created == 2
    assert skipped_count == 0
    assert len(statements) == 2
    assert set(publisher._per_fact_metric_views.keys()) == {"Sales", "Returns"}

    sales_yaml = publisher._per_fact_metric_views["Sales"].lower()
    returns_yaml = publisher._per_fact_metric_views["Returns"].lower()
    assert 'name: "sales_amount"' in sales_yaml
    assert 'name: "returns_amount"' not in sales_yaml
    assert 'name: "returns_amount"' in returns_yaml
    assert 'name: "sales_amount"' not in returns_yaml

    all_sql = "\n".join(statements).lower()
    assert "routermodel_fact_sales_metric_view" in all_sql
    assert "routermodel_fact_returns_metric_view" in all_sql


def test_generate_measure_view_statements_semantic_router_per_fact_skips_unanchored_measure() -> None:
    model = SMLModel(
        unique_name="AnchorModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="Customer",
                columns=[
                    SMLColumn(unique_name="id", data_type=DataType.INTEGER),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="sales_to_customer",
                from_dataset="Sales",
                from_columns=["customer_id"],
                to_dataset="Customer",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Sales Amount",
                dataset="Sales",
                source_column="amount",
                aggregation=AggregationType.SUM,
            ),
            SMLMetric(
                unique_name="Unanchored Metric",
                dataset="Sales",
                expression="SUM([UnknownTable].[value])",
            ),
        ],
    )
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            semantic_router_enabled=True,
            measure_view_type="metric_view",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    with patch.object(publisher, "_resolve_existing_source_for_dataset", return_value=None):
        publisher._initialize_semantic_router(model)
        _, created, skipped_count, skipped_details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_METRIC,
        )

    assert created == 1
    assert skipped_count >= 1
    assert any("unanchored" in str(item.get("name", "")).lower() for item in skipped_details)

    sales_yaml = publisher._per_fact_metric_views["Sales"].lower()
    assert 'name: "sales_amount"' in sales_yaml
    assert 'name: "unanchored_metric"' not in sales_yaml


def test_get_contextual_metric_ids_for_fact_filters_unreachable_tables() -> None:
    model = SMLModel(
        unique_name="RouterModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[SMLColumn(unique_name="amount", data_type=DataType.DECIMAL)],
            ),
            SMLDataset(
                unique_name="Customer",
                columns=[SMLColumn(unique_name="name", data_type=DataType.STRING)],
            ),
            SMLDataset(
                unique_name="Inventory",
                columns=[SMLColumn(unique_name="amount", data_type=DataType.DECIMAL)],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Sales Amount",
                dataset="Sales",
                source_column="amount",
                aggregation=AggregationType.SUM,
            ),
            SMLMetric(
                unique_name="Customer Name Count",
                dataset="Customer",
                source_column="name",
                aggregation=AggregationType.COUNT,
            ),
            SMLMetric(
                unique_name="Inventory Amount",
                dataset="Inventory",
                source_column="amount",
                aggregation=AggregationType.SUM,
            ),
        ],
    )
    publisher = DatabricksPublisher(_cfg())
    metric_name_index = publisher._build_metric_name_index(model)
    metric_dataset_by_id, _ = publisher._build_effective_metric_dataset_maps(model)

    contextual_metric_ids, unanchored_metrics = publisher._get_contextual_metric_ids_for_fact(
        "Sales",
        {"Customer"},
        model,
        {
            "Sales Amount": {"Sales"},
            "Customer Name Count": {"Sales"},
            "Inventory Amount": {"Inventory"},
        },
        metric_dataset_by_id,
        metric_name_index,
    )

    assert id(model.metrics[0]) in contextual_metric_ids
    assert id(model.metrics[1]) in contextual_metric_ids
    assert id(model.metrics[2]) not in contextual_metric_ids
    assert not unanchored_metrics


def test_emit_review_diagnostics_includes_reason_counts_and_skip_lists() -> None:
    publisher = DatabricksPublisher(_cfg())

    summary = publisher._emit_review_diagnostics(
        [
            {"name": "Metric A", "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED},
            {"name": "Metric B", "reason": DEPLOY_REASON_DAX_NOT_SUPPORTED},
            {"name": "Join C", "reason": DEPLOY_REASON_VALIDATION_FAILED, "item_type": "join"},
        ]
    )

    assert summary["reason_counts"][DEPLOY_REASON_DAX_NOT_SUPPORTED] == 2
    assert summary["reason_counts"][DEPLOY_REASON_VALIDATION_FAILED] == 1
    assert summary["skipped_measure_names"] == ["Metric A", "Metric B"]
    assert summary["skipped_join_names"] == ["Join C"]


def test_generate_sql_statements_populates_routing_summary_for_semantic_router() -> None:
    model = SMLModel(
        unique_name="RouterSummaryModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="Customer",
                columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="sales_to_customer",
                from_dataset="Sales",
                from_columns=["customer_id"],
                to_dataset="Customer",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Sales Amount",
                dataset="Sales",
                source_column="amount",
                aggregation=AggregationType.SUM,
            )
        ],
    )
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            semantic_router_enabled=True,
            measure_view_type="metric_view",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    with patch.object(publisher, "_resolve_existing_source_for_dataset", return_value=None):
        _ = publisher.generate_sql_statements(model, view_type_override=VIEW_TYPE_METRIC)

    publish_summary = publisher.get_last_publish_summary()
    routing_summary = publish_summary["routing_summary"]

    assert routing_summary["source_table_count"] == 2
    assert routing_summary["fact_table_count"] == 1
    assert routing_summary["dimension_table_count"] == 1
    assert routing_summary["bridge_table_count"] == 0
    assert routing_summary["generated_artifact_count"] == 1
    assert routing_summary["review_required_count"] == 0


def test_auto_bridge_relationship_join_keys_adds_and_backfills_missing_column():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            enable_auto_join_key_bridge=True,
            source_column_mapping={
                "Customer": {
                    "Customer": "customer_key",
                }
            },
        )
    )
    model = SMLModel(
        unique_name="Customer Profitability",
        datasets=[
            SMLDataset(
                unique_name="Fact",
                columns=[SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER)],
            ),
            SMLDataset(
                unique_name="Customer",
                columns=[SMLColumn(unique_name="Customer", data_type=DataType.STRING)],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="REL_FACT_CUSTOMER_KEY__CUSTOMER_CUSTOMER",
                from_dataset="Fact",
                from_columns=["Customer Key"],
                to_dataset="Customer",
                to_columns=["Customer"],
            )
        ],
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    calls: list[str] = []
    column_state = {
        "`main`.`public`.`fact`": {"customer_key"},
        "`main`.`public`.`customer`": {"customer", "name"},
    }

    def _source_for_dataset(dataset: SMLDataset, expected: str) -> str:
        _ = expected
        return f"`main`.`public`.`{dataset.unique_name.lower()}`"

    def _get_columns(source_fq: str) -> set[str]:
        return set(column_state.get(source_fq, set()))

    def _exec(statements: list[str], concurrent: bool = False) -> list[dict[str, object]]:
        _ = concurrent
        for stmt in statements:
            calls.append(stmt)
            lowered = stmt.lower()
            if "alter table `main`.`public`.`customer`" in lowered and "`customer_key`" in lowered:
                column_state["`main`.`public`.`customer`"].add("customer_key")
        return []

    with patch.object(publisher, "_resolve_existing_source_for_dataset", side_effect=_source_for_dataset), patch.object(
        publisher,
        "_get_source_table_columns",
        side_effect=_get_columns,
    ), patch.object(
        publisher,
        "execute_statements",
        side_effect=_exec,
    ):
        publisher._auto_bridge_relationship_join_keys(model)

    assert any("ALTER TABLE `main`.`public`.`customer` ADD COLUMNS (`customer_key` STRING" in sql for sql in calls)
    assert any("UPDATE `main`.`public`.`customer` SET `customer_key`" in sql for sql in calls)

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
    def test_sql_view_inlines_required_columns_for_schemaless_dataset(self):
        """SQL fallback should inline NULL source columns when the dataset has no modeled columns."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(measure_view_type="sql_view")
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    sql_expression="concat('Last Refreshed: ', cast(max(gl_refresh_datetime) as string))",
                    aggregation=AggregationType.NONE,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"_semabridge_placeholder"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "max(gl_refresh_datetime)" in stmts[0].lower()

    def test_aggregation_source_column_creates_view(self):
        """aggregation + source_column builds a view when no sql_expression."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        assert "sum(sale_revenue)" in stmts[0] or "SUM(`REVENUE`)" in stmts[0]

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
        assert "SUM(`REVENUE`)" in stmts[0] or "sum" in stmts[0].lower()
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
        assert "sum(sale_revenue)" in stmts[0].lower()

    def test_metric_view_renders_project_measures_sum_and_refresh_max(self):
        """Metric-view generation should draft cross-table Project Measures SUMs instead of dropping them."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                metric_view_only_sum_translation=True,
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
        project_measures_stmt = next(
            stmt for stmt in stmts if "mv_Inventory_Semantic_Model_Project_Measures" in stmt
        )
        lowered = project_measures_stmt.lower()
        assert 'name: "source_value_total_stock"' in lowered
        assert 'name: "wac_value_total_stock"' in lowered
        assert 'expr: "any_value(0)"' in lowered

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
        assert details[0]["reason"] == DEPLOY_REASON_DAX_NOT_SUPPORTED

    def test_complex_dax_uses_common_llm_when_enabled(self):
        """Complex DAX uses the common LLM translator when explicitly enabled."""
        model = _sales_model(dax_only=True)
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                enable_llm_dax_translation=True,
                llm_dax_provider_order=["gemini", "groq"],
                measure_view_type=VIEW_TYPE_METRIC,
            )
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch(
            "semabridge.converter.common_dax_translator.CommonDAXTranslator.translate",
            return_value=CommonDAXTranslationResult(
                sql="SUM(CASE WHEN `sales`.`region` = 'North' THEN `sales`.`revenue` ELSE 0 END)",
                is_valid=True,
                provider="groq",
                model="llama-3.3-70b-versatile",
                attempted_providers=["gemini", "groq"],
            ),
        ) as translate:
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert details == []
        assert translate.called
        assert "case when" in stmts[0].lower()

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
        assert skipped == 1
        assert details
        assert details[0]["reason"] == DEPLOY_REASON_VALIDATION_FAILED

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
        assert skipped == 1
        assert details
        assert details[0]["reason"] == DEPLOY_REASON_VALIDATION_FAILED

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

    def test_sql_native_divide_leakage_is_translated_for_databricks(self):
        """DAX DIVIDE persisted in sql_expression should not leak into Databricks SQL/YAML."""
        model = SMLModel(
            unique_name="CustomerProfitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Profit", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Profit Margin",
                    dataset="Fact",
                    expression="DIVIDE(SUM('Fact'[Profit]), SUM('Fact'[Revenue]), 0)",
                    sql_expression="DIVIDE(SUM('Fact'[Profit]), SUM('Fact'[Revenue]), 0)",
                    aggregation=AggregationType.NONE,
                )
            ],
        )
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_measure_views=True,
            )
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)
        sql = "\n".join(stmts)

        assert created == 1
        assert skipped == 0
        assert details == []
        assert "DIVIDE(" not in sql.upper()
        assert "coalesce(" in sql.lower()
        assert "nullif(" in sql.lower()
        assert "sum(fact_profit)" in sql.lower()
        assert "sum(fact_revenue)" in sql.lower()

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
        assert "SUM(`revenue`) AS `Total_Revenue`" in stmts[0]
        assert "SUM(`units`) AS `Total_Units`" in stmts[0]
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

    def test_combined_sql_mode_uses_stable_alias_for_scenario_dataset(self):
        """Combined SQL views should not emit bare `scenario` aliases that Databricks can misread."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_mode="combined",
                measure_view_type="sql_view",
                enable_cross_table_joins=True,
            )
        )
        model = SMLModel(
            unique_name="Customer Profitability",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="scenario_key", data_type=DataType.STRING),
                    ],
                ),
                SMLDataset(
                    unique_name="Scenario",
                    columns=[
                        SMLColumn(unique_name="scenario_key", data_type=DataType.STRING),
                        SMLColumn(unique_name="scenario_name", data_type=DataType.STRING),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_to_scenario",
                    from_dataset="Fact",
                    from_columns=["scenario_key"],
                    to_dataset="Scenario",
                    to_columns=["scenario_key"],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Revenue_Budget",
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
            side_effect=lambda source_table: {"revenue", "scenario_key", "scenario_name"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "scenario_join" in stmts[0]
        assert "scenario." not in stmts[0]

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
        assert "`customer_id` AS `fact_customer_key`" in stmts[0]

    def test_metric_view_prefixes_columns_with_table_alias(self):
        """Metric-view technical names should prefix customer columns with CUST_."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                source_table_mapping={"customer": "main.public.customer"},
            )
        )
        model = SMLModel(
            unique_name="Customer Analytics",
            datasets=[
                SMLDataset(
                    unique_name="customer",
                    columns=[
                        SMLColumn(unique_name="id", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="cus", data_type=DataType.STRING),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Customer Count",
                    dataset="customer",
                    source_column="id",
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
            return_value={"id", "cus"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "`id` AS `cust_id`" in stmts[0]
        assert "`cus` AS `cust_cus`" in stmts[0]
        assert 'expr: "count(cust_id)"' in stmts[0]

    def test_metric_view_measures_without_collision_no_prefix(self):
        """Metric-view measures should prefix with table alias for consistency (e.g., cust_total_revenue)."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                source_table_mapping={"customer": "main.public.customer"},
            )
        )
        model = SMLModel(
            unique_name="Customer Analytics",
            datasets=[
                SMLDataset(
                    unique_name="customer",
                    columns=[
                        SMLColumn(unique_name="id", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="revenue", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    dataset="customer",
                    source_column="revenue",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Customer Count",
                    dataset="customer",
                    source_column="id",
                    aggregation=AggregationType.COUNT,
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
            return_value={"id", "revenue"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        # Measures should be created without collision-based prefixes
        assert "- name: \"Total_Revenue\"" in stmts[0]
        assert "- name: \"Customer_Count\"" in stmts[0]
        # Dimensions should have the cust_ prefix from table alias
        assert "`id` AS `cust_id`" in stmts[0]
        assert "`revenue` AS `cust_revenue`" in stmts[0]

    def test_metric_view_measure_collision_with_dimension(self):
        """Metric-view measures that collide with dimension names should use _m suffix."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                source_table_mapping={"product": "main.public.product"},
            )
        )
        model = SMLModel(
            unique_name="Product Analytics",
            datasets=[
                SMLDataset(
                    unique_name="product",
                    columns=[
                        SMLColumn(unique_name="revenue", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="revenue",  # Same name as dimension
                    dataset="product",
                    source_column="revenue",
                    aggregation=AggregationType.SUM,
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
            return_value={"revenue"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        # Dimension name: prod_revenue
        # Measure name collision: prod_revenue (collides), so becomes prod_revenue_m
        assert "`revenue` AS `prod_revenue`" in stmts[0]  # Dimension
        assert "- name: \"prod_revenue_m\"" in stmts[0]  # Measure with _m suffix

    def test_metric_view_empty_dataset_with_measures_creates_dimensions_from_source(self):
        """Metric views should emit dimensions even when dataset.columns is empty if source columns exist."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                source_table_mapping={"Project Measures": "semabridge.public.Project_Measures"},
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(
                    unique_name="Project Measures",
                    columns=[],  # Empty! But source table has columns
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="GL Refresh DateTime",
                    dataset="Project Measures",
                    sql_expression="max(gl_refresh_datetime)",
                    aggregation=AggregationType.NONE,
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
            return_value={"gl_refresh_datetime"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        # YAML should have dimensions from extracted column and measures 
        assert "dimensions:" in stmts[0]
        assert "- name: \"proj_gl_refresh_datetime\"" in stmts[0]  # Dimension from extracted column
        # Source should have the column
        assert "CAST(NULL AS TIMESTAMP) AS `gl_refresh_datetime`" in stmts[0]
        # Measure should be prefixed AND collide with dimension (gets _m suffix)
        assert "- name: \"proj_gl_refresh_datetime_m\"" in stmts[0]

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

        assert created == 0
        assert skipped == 1
        assert stmts == []
        assert any(d.get("reason") == DEPLOY_REASON_CROSS_TABLE for d in details)

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
        assert "0 AS `Corporate_IOH`" in stmts[0]

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
        assert "first((SELECT SUM(`source_value_total_stock`) FROM `main`.`public`.`Inventory_Fact`))" in stmts[0]
        assert "first((SELECT SUM(`wac_value_total_stock`) FROM `main`.`public`.`Inventory_Fact`))" in stmts[0]
        assert "CAST(NULL AS TIMESTAMP) AS `gl_refresh_datetime`" in stmts[0]

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
        assert "CAST(NULL AS TIMESTAMP) AS `gl_refresh_datetime`" in stmts[0]

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
                    columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER, is_key=True)],
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
        assert any("COUNT(DISTINCT `id`) AS `distinct_id_count`" in stmt for stmt in stmts)

    def test_per_model_metric_view_includes_all_model_measures_and_dimensions(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="metric_view",
                emit_metric_views_for_all_datasets=True,
                emit_distinct_pk_metric_for_dimension_datasets=True,
                enable_metric_view_joins=True,
                enable_cross_table_joins=True,
            )
        )
        model = SMLModel(
            unique_name="CoverageModel",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="customer_key", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="Customer",
                    columns=[
                        SMLColumn(unique_name="customer_key", data_type=DataType.INTEGER, is_key=True),
                        SMLColumn(unique_name="region", data_type=DataType.STRING),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_to_customer",
                    from_dataset="Fact",
                    from_columns=["customer_key"],
                    to_dataset="Customer",
                    to_columns=["customer_key"],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Amount",
                    dataset="Fact",
                    source_column="amount",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Customer Count",
                    dataset="Customer",
                    source_column="customer_key",
                    aggregation=AggregationType.COUNT_DISTINCT,
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
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        lowered = stmts[0].lower()
        assert "total_amount" in lowered
        assert "customer_count" in lowered
        assert "customer.region" in lowered

    def test_per_model_metric_view_deploys_unresolved_measure_as_null(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="metric_view",
                enable_metric_view_joins=True,
                enable_cross_table_joins=True,
                enable_simple_dax_translation=False,
                enable_low_confidence_drafts=True,
            )
        )
        model = SMLModel(
            unique_name="NullDraftModel",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Good Measure",
                    dataset="Fact",
                    source_column="amount",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Bad Dax Measure",
                    dataset="Fact",
                    expression="SUM([unknown_col])",
                    aggregation=AggregationType.NONE,
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
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        lowered = stmts[0].lower()
        assert 'name: "good_measure"' in lowered
        assert 'name: "bad_dax_measure"' in lowered

    def test_metric_view_joins_resolve_spaced_dataset_source_names(self):
        """Join source should resolve to Databricks-safe relation names, not raw labels with spaces."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                enable_metric_view_joins=True,
                enable_cross_table_joins=True,
                source_catalog="semabridge",
                source_schema="public",
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(
                    unique_name="Inventory Fact",
                    columns=[
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="plant_bu_mapping_curr_skey", data_type=DataType.INTEGER),
                    ],
                ),
                SMLDataset(
                    unique_name="Plant BU Mapping",
                    columns=[
                        SMLColumn(unique_name="plant_bu_mapping_curr_skey", data_type=DataType.INTEGER, is_key=True),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_to_plant_bu",
                    from_dataset="Inventory Fact",
                    from_columns=["plant_bu_mapping_curr_skey"],
                    to_dataset="Plant BU Mapping",
                    to_columns=["plant_bu_mapping_curr_skey"],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Amount",
                    dataset="Inventory Fact",
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
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"amount", "plant_bu_mapping_curr_skey"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert 'source: "semabridge.public.plant_bu_mapping"' in stmts[0].lower()
        assert 'source: "Plant BU Mapping"' not in stmts[0]
        assert 'cast(null as bigint) as `plant_bu_mapping_curr_skey`' in stmts[0].lower()

    def test_metric_view_joins_normalize_using_keys_with_spaces(self):
        """USING join keys with spaces should be normalized to SQL-safe identifiers."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                enable_metric_view_joins=True,
                enable_cross_table_joins=True,
                source_catalog="semabridge",
                source_schema="public",
            )
        )
        model = SMLModel(
            unique_name="BusinessUnitModel",
            datasets=[
                SMLDataset(
                    unique_name="Inventory Fact",
                    columns=[
                        SMLColumn(unique_name="Business Unit", data_type=DataType.STRING),
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="Business Units",
                    columns=[
                        SMLColumn(unique_name="Business Unit", data_type=DataType.STRING, is_key=True),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_to_business_units",
                    from_dataset="Inventory Fact",
                    from_columns=["Business Unit"],
                    to_dataset="Business Units",
                    to_columns=["Business Unit"],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Amount",
                    dataset="Inventory Fact",
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
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"amount", "business_unit"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert 'using:' in stmts[0].lower()
        assert '"business_unit"' in stmts[0].lower()
        assert '"business unit"' not in stmts[0].lower()


def test_find_dataset_by_metric_view_alias_matches_compact_alias_form():
    """Joined-dimension lookup should match aliases that remove underscores/spaces."""
    model = SMLModel(
        unique_name="AliasModel",
        datasets=[
            SMLDataset(
                unique_name="Plant_BU_Mapping",
                columns=[
                    SMLColumn(unique_name="plant_bu_mapping_curr_skey", data_type=DataType.INTEGER),
                ],
            ),
        ],
        metrics=[],
    )
    publisher = DatabricksPublisher(_cfg())

    # JoinTreeBuilder aliases can compact names (e.g., Plant_BU_Mapping -> plantbumapping).
    dataset = publisher._find_dataset_by_metric_view_alias(model, "plantbumapping")

    assert dataset is not None
    assert dataset.unique_name == "Plant_BU_Mapping"

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

        assert "CAST(NULL AS DECIMAL(38, 10)) AS `fact_daily_delivery_ld_rate`" in yaml_text
        assert "CAST(NULL AS DECIMAL(38, 10)) AS `fact_daily_delivery_ld_rate_dim`" in yaml_text
        assert '  - name: "fact_daily_delivery_ld_rate"' in yaml_text
        assert '  - name: "fact_daily_delivery_ld_rate_dim"' in yaml_text

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
        assert parsed["measures"][0]["name"] == "Corporate_DSI_Monthly"
        assert "sum(case when" in parsed["measures"][0]["expr"]

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
        assert "CAST(NULL AS TIMESTAMP)" in stmts[0]
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
        assert "`customer_key` AS `fact_customer_key`" in stmts[0]
        assert "`fact_product_key` AS `fact_product_key`" in stmts[0]
        assert "`revenue` AS `fact_revenue`" in stmts[0]
        assert 'expr: "sum(fact_revenue)"' in stmts[0]
        assert 'expr: "count(distinct fact_customer_key)"' in stmts[0]
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

        assert len(stmts) == 0
        assert created == 0
        assert skipped == 1
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
        normalized_expected = expected_sql.lower().replace("`amt`", "fact_amt")
        assert normalized_expected in stmts[0].lower()


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
        assert "count(*)" in stmts[0]
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
        assert "count(*)" in sql

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
        assert "count(*)" in sql

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

        resolved = publisher._resolve_source_table(model.datasets[0])

        assert resolved == "`lakehouse`.`public`.`DEVICE_INVENTORY`"

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
        sql_lower = stmts[0].lower()
        expected_lower = expected_sql.lower()
        if "count(distinct" in expected_lower:
            assert "count(distinct" in sql_lower, f"Expected distinct-count translation for DAX: {dax}"
        elif "count_if(" in expected_lower:
            assert "count_if(" in sql_lower and " is null" in sql_lower, (
                f"Expected count-if-null translation for DAX: {dax}"
            )
        else:
            fn = expected_lower.split("(", 1)[0]
            assert f"{fn}(" in sql_lower, f"Expected '{expected_sql}' in SQL for DAX: {dax}"

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
        assert "sale_revenue" in stmts[0]

    def test_metric_view_contains_measures(self):
        """Generated YAML includes resolved measures."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "measures:" in stmts[0]
        assert "sum(" in stmts[0]

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

        assert 'source: "analytics.raw.sales_data"' in stmts[0]

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
        assert any("WITH METRICS LANGUAGE YAML" in stmt for stmt in stmts)
        assert any("inventory_count" in stmt.lower() for stmt in stmts)

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

        def _mock_execute(statements, *args, **kwargs):
            executed_sql.extend(statements)
            sql = statements[0].upper()
            if "INFORMATION_SCHEMA.TABLES" in sql and "SELECT COUNT(1) AS CNT" in sql:
                return [{"result": {"data_array": [["1"]]}}]
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
            publisher.publish(model)

        assert any("INFORMATION_SCHEMA.TABLES" in s.upper() for s in executed_sql)
        create_view_sql = next(s for s in executed_sql if "WITH METRICS LANGUAGE YAML" in s)
        assert "measures:" in create_view_sql
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

        def _mock_execute(statements, *args, **kwargs):
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
        assert "WITH METRICS LANGUAGE YAML" in create_view_sql
        assert "INVENTORY_COUNT" in create_view_sql.upper()

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
        """Missing source-table pre-check should still allow schema-first metric-view generation."""
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

        assert created == 1
        assert skipped == 0
        assert len(stmts) == 1
        assert any("mv_device_model_device_inventory" in stmt for stmt in stmts)
        


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
        assert "sum(fact_revenue)" in stmts[0].lower()
        assert "sum(fact_cogs)" in stmts[0].lower()
        assert "nullif" in stmts[0].lower()
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

        def _mock_execute(statements: list[str], *args, **kwargs):
            executed_sql.extend(statements)
            return [{"status": {"state": "SUCCEEDED"}} for _ in statements]

        def _mock_resolve_source(dataset, expected_source):
            ds = (dataset.unique_name or "").lower()
            if ds == "device_inventory":
                if not getattr(_mock_resolve_source, "inventory_seen", False):
                    _mock_resolve_source.inventory_seen = True
                    return None
                return "`main`.`public`.`device_inventory`"
            if ds == "device_date":
                if not getattr(_mock_resolve_source, "date_seen", False):
                    _mock_resolve_source.date_seen = True
                    return None
                return "`main`.`public`.`device_date`"
            return expected_source

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"):
            with patch.object(
                publisher,
                "_resolve_existing_source_for_dataset",
                side_effect=_mock_resolve_source,
            ):
                with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
                    publisher.publish(model)

        assert any("CREATE TABLE IF NOT EXISTS `main`.`public`.`device_inventory`" in sql for sql in executed_sql)
        assert any("CREATE TABLE IF NOT EXISTS `main`.`public`.`device_date`" in sql for sql in executed_sql)
        assert any("WITH METRICS LANGUAGE YAML" in sql for sql in executed_sql)

    def test_publish_per_model_metric_view_skips_shell_table_preflight(self):
        """Per-model metric-view publish should initialize missing source shells when needed."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                model_artifact_mode="per_model",
                create_metadata_table=False,
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

        def _mock_execute(statements: list[str], concurrent: bool = False):
            executed_sql.extend(statements)
            return [{"status": {"state": "SUCCEEDED"}} for _ in statements]

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value=None,
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        assert any("CREATE TABLE IF NOT EXISTS" in sql for sql in executed_sql)
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

    def test_publish_falls_back_to_sql_view_when_metric_view_deploy_fails_in_auto_mode(self):
        """If native metric views fail in auto mode, publish should try SQL views."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="auto",
                measure_view_mode="combined",
                create_metadata_table=False,
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []

        def _mock_execute(statements: list[str], concurrent: bool = False):
            sql = statements[0]
            executed_sql.append(sql)
            if "WITH METRICS LANGUAGE YAML" in sql:
                raise DatabricksPublishError("metric views not supported on this warehouse")
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        assert any("WITH METRICS LANGUAGE YAML" in sql for sql in executed_sql)
        assert any(
            "CREATE OR REPLACE VIEW" in sql and "WITH METRICS LANGUAGE YAML" not in sql
            for sql in executed_sql
        )

    def test_publish_does_not_fallback_to_sql_when_metric_view_explicit(self):
        """Explicit metric_view config should not silently downgrade to SQL views."""
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

        def _mock_execute(statements: list[str], concurrent: bool = False):
            sql = statements[0]
            executed_sql.append(sql)
            if "WITH METRICS LANGUAGE YAML" in sql:
                raise DatabricksPublishError("metric views not supported on this warehouse")
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        assert any("WITH METRICS LANGUAGE YAML" in sql for sql in executed_sql)
        assert not any(
            "CREATE OR REPLACE VIEW" in sql and "WITH METRICS LANGUAGE YAML" not in sql
            for sql in executed_sql
        )

    def test_publish_falls_back_to_sql_when_metric_view_explicit_and_override_enabled(self):
        """Explicit metric_view may downgrade to SQL views when opt-in override is enabled."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=False,
                allow_sql_fallback_on_metric_view_failure=True,
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []

        def _mock_execute(statements: list[str], concurrent: bool = False):
            _ = concurrent
            sql = statements[0]
            executed_sql.append(sql)
            if "WITH METRICS LANGUAGE YAML" in sql:
                raise DatabricksPublishError("metric views not supported on this warehouse")
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        assert any("WITH METRICS LANGUAGE YAML" in sql for sql in executed_sql)
        assert any(
            "CREATE OR REPLACE VIEW" in sql and "WITH METRICS LANGUAGE YAML" not in sql
            for sql in executed_sql
        )

    def test_publish_classifies_scalar_subquery_grouping_failure(self, caplog):
        """Metric-view semantic grouping failures should be classified as SQL semantic errors."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=False,
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        def _mock_execute(statements: list[str], concurrent: bool = False):
            _ = concurrent
            sql = statements[0]
            if "WITH METRICS LANGUAGE YAML" in sql:
                raise DatabricksPublishError(
                    "Databricks statement state=FAILED: [SCALAR_SUBQUERY_IS_IN_GROUP_BY_OR_AGGREGATE_FUNCTION]"
                )
            return [{"status": {"state": "SUCCEEDED"}}]

        with caplog.at_level("WARNING"), patch.object(
            publisher,
            "_determine_view_type",
            return_value="metric_view",
        ), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        assert any("class=SQL_SEMANTIC" in rec.getMessage() for rec in caplog.records)

        error_class, reason = publisher._classify_metric_view_deploy_error(
            "[SCALAR_SUBQUERY_IS_IN_GROUP_BY_OR_AGGREGATE_FUNCTION]"
        )
        assert error_class == ERROR_CLASS_SQL_SEMANTIC
        assert reason == "SCALAR_SUBQUERY_GROUPING"

    def test_publish_logs_sql_fallback_state_success(self, caplog):
        """Fallback should emit explicit IN_PROGRESS and SUCCESS terminal state logs."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="auto",
                measure_view_mode="combined",
                create_metadata_table=False,
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        def _mock_execute(statements: list[str], concurrent: bool = False):
            _ = concurrent
            sql = statements[0]
            if "WITH METRICS LANGUAGE YAML" in sql:
                raise DatabricksPublishError("metric deploy failed")
            return [{"status": {"state": "SUCCEEDED"}}]

        with caplog.at_level("INFO"), patch.object(
            publisher,
            "_determine_view_type",
            return_value="metric_view",
        ), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        messages = [rec.getMessage() for rec in caplog.records]
        assert any(f"state={SQL_FALLBACK_IN_PROGRESS}" in msg for msg in messages)
        assert any(f"state={SQL_FALLBACK_SUCCESS}" in msg for msg in messages)

    def test_publish_logs_sql_fallback_state_failed(self, caplog):
        """Fallback should emit FAILED terminal state when SQL fallback deployment still fails."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="auto",
                measure_view_mode="combined",
                create_metadata_table=False,
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        def _mock_execute(statements: list[str], concurrent: bool = False):
            _ = concurrent
            sql = statements[0]
            if "WITH METRICS LANGUAGE YAML" in sql:
                raise DatabricksPublishError("metric deploy failed")
            if sql.strip().startswith("CREATE OR REPLACE VIEW"):
                raise DatabricksPublishError("sql fallback failed")
            return [{"status": {"state": "SUCCEEDED"}}]

        with caplog.at_level("INFO"), patch.object(
            publisher,
            "_determine_view_type",
            return_value="metric_view",
        ), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        messages = [rec.getMessage() for rec in caplog.records]
        assert any(f"state={SQL_FALLBACK_IN_PROGRESS}" in msg for msg in messages)
        assert any(f"state={SQL_FALLBACK_FAILED}" in msg for msg in messages)

    def test_publish_conflict_cleanup_disabled_by_default(self):
        """Object-type conflicts should not auto-drop when destructive ops are disabled."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=False,
                enable_destructive_sync_operations=False,
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []

        def _mock_execute(statements: list[str], concurrent: bool = False):
            sql = statements[0]
            executed_sql.append(sql)
            if "WITH METRICS LANGUAGE YAML" in sql:
                raise DatabricksPublishError(
                    "Databricks statement state=FAILED: {'error_code':'BAD_REQUEST','message':'[EXPECT_VIEW_NOT_TABLE.NO_ALTERNATIVE]'}"
                )
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        assert any("WITH METRICS LANGUAGE YAML" in sql for sql in executed_sql)
        assert not any(sql.startswith("DROP VIEW IF EXISTS") for sql in executed_sql)
        assert not any(sql.startswith("DROP TABLE IF EXISTS") for sql in executed_sql)

    def test_publish_conflict_cleanup_runs_when_destructive_ops_enabled(self):
        """Object-type conflicts should auto-drop and retry only when explicitly enabled."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=False,
                enable_destructive_sync_operations=True,
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []
        state = {"cleaned": False}

        def _mock_execute(statements: list[str], concurrent: bool = False):
            sql = statements[0]
            executed_sql.append(sql)
            if sql.startswith("DROP VIEW IF EXISTS") or sql.startswith("DROP TABLE IF EXISTS"):
                state["cleaned"] = True
                return [{"status": {"state": "SUCCEEDED"}}]
            if "WITH METRICS LANGUAGE YAML" in sql and not state["cleaned"]:
                raise DatabricksPublishError(
                    "Databricks statement state=FAILED: {'error_code':'BAD_REQUEST','message':'[EXPECT_VIEW_NOT_TABLE.NO_ALTERNATIVE]'}"
                )
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        assert any(sql.startswith("DROP VIEW IF EXISTS") for sql in executed_sql)
        assert any(sql.startswith("DROP TABLE IF EXISTS") for sql in executed_sql)

    def test_publish_metric_view_unresolved_column_degrades_only_failing_measure(self):
        """UNRESOLVED_COLUMN in metric YAML should remove the failing measure and keep the view."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=False,
            )
        )
        model = SMLModel(
            unique_name="SalesModel",
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
                    source_column="REVENUE",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Broken_Refresh",
                    dataset="Sales",
                    sql_expression="MAX(missing_col)",
                    aggregation=AggregationType.MAX,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)
        executed_sql: list[str] = []

        def _mock_execute(statements: list[str], concurrent: bool = False):
            sql = statements[0]
            executed_sql.append(sql)
            if "WITH METRICS LANGUAGE YAML" in sql and "max(missing_col)" in sql.lower():
                raise DatabricksPublishError(
                    "Databricks statement state=FAILED: {'error_code':'BAD_REQUEST','message':'[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function parameter with name `missing_col` cannot be resolved.'}"
                )
            return [{"status": {"state": "SUCCEEDED"}}]

        with patch.object(publisher, "_determine_view_type", return_value="metric_view"), patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            return_value="`main`.`public`.`Sales`",
        ), patch.object(
            publisher,
            "execute_statements",
            side_effect=_mock_execute,
        ):
            publisher.publish(model)

        metric_deploy_sql = [s for s in executed_sql if "WITH METRICS LANGUAGE YAML" in s]
        assert len(metric_deploy_sql) >= 2
        assert any("max(missing_col)" in s.lower() for s in metric_deploy_sql)
        assert any("sum(" in s.lower() and "missing_col" not in s.lower() for s in metric_deploy_sql)
        assert not any(
            "CREATE OR REPLACE VIEW" in s and "WITH METRICS LANGUAGE YAML" not in s
            for s in executed_sql
        )

    def test_remove_failing_metric_measure_also_drops_unresolved_dimensions(self):
        """Graceful degradation should remove dimensions that reference unresolved identifiers."""
        publisher = DatabricksPublisher(_cfg())

        view_sql = (
            "CREATE OR REPLACE VIEW `main`.`public`.`mv_demo` "
            "WITH METRICS LANGUAGE YAML AS $$\n"
            "version: 1.1\n"
            "comment: \"demo\"\n"
            "source: \"main.public.customer\"\n"
            "dimensions:\n"
            "  - name: \"customer\"\n"
            "    expr: \"customer\"\n"
            "  - name: \"state\"\n"
            "    expr: \"state\"\n"
            "measures:\n"
            "  - name: \"row_count\"\n"
            "    expr: \"count(*)\"\n"
            "$$"
        )
        error_text = (
            "Databricks statement state=FAILED: {'error_code':'BAD_REQUEST','message':"
            "'[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function parameter "
            "with name `customer` cannot be resolved.'}"
        )

        degraded_sql = publisher._remove_failing_metric_measure(view_sql, error_text)

        assert degraded_sql
        assert "name: customer" not in degraded_sql
        assert "expr: customer" not in degraded_sql
        assert "name: state" in degraded_sql
        assert "expr: count(*)" in degraded_sql

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

        deploy_calls = [
            args[0][0]
            for args, _ in execute_mock.call_args_list
            if args and args[0]
        ]
        assert not any("CREATE OR REPLACE VIEW" in sql for sql in deploy_calls)

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
        """DAX_SKIPPED gets LOW confidence."""
        publisher = DatabricksPublisher(_cfg())
        assert publisher._assess_confidence(TRANSLATION_TYPE_DAX_SKIPPED) == CONFIDENCE_LOW

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
        assert "CREATE OR REPLACE VIEW" in stmts[0]
        assert "SUM(`revenue`)" in stmts[0]
        assert "GROUP BY `REGION`" in stmts[0]

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
        assert "CREATE OR REPLACE VIEW" in stmts[0]
        assert "SUM(`revenue`) AS `Total_Revenue`" in stmts[0]
        assert "SUM(`units`) AS `Total_Units`" in stmts[0]


class TestPerModelArtifactMode:
    """Tests for per-model Databricks artifact naming and generation."""

    def test_per_model_mode_generates_single_named_metadata_table_and_view(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                model_metadata_suffix="metadata",
                model_metric_view_suffix="metric_view",
                measure_view_type="sql_view",
            )
        )
        model = SMLModel(
            unique_name="SalesModel",
            datasets=[
                SMLDataset(
                    unique_name="Orders",
                    columns=[
                        SMLColumn(unique_name="o_totalprice", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="Orders",
                    source_column="o_totalprice",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts = publisher.generate_sql_statements(model, view_type_override=VIEW_TYPE_SQL)

        create_tables = [s for s in stmts if s.startswith("CREATE TABLE")]
        create_views = [s for s in stmts if s.startswith("CREATE OR REPLACE VIEW")]

        assert len(create_tables) == 1
        assert len(create_views) == 1
        assert "`main`.`public`.`SalesModel_metadata`" in create_tables[0]
        assert "`main`.`public`.`SalesModel_metric_view`" in create_views[0]

    def test_per_model_mode_includes_dimension_dataset_in_single_metric_view(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="metric_view",
                enable_metric_view_joins=True,
                enable_cross_table_joins=True,
                view_prefix="mv",
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
                    unique_name="Customer",
                    columns=[
                        SMLColumn(unique_name="Customer Key", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="Region", data_type=DataType.STRING),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_to_customer",
                    from_dataset="Fact",
                    from_columns=["amount"],
                    to_dataset="Customer",
                    to_columns=["Customer Key"],
                )
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
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert any("`main`.`public`.`CoverageModel_metric_view`" in stmt for stmt in stmts)
        assert "cust_customer_key" in stmts[0]
        assert "cust_region" in stmts[0]
        assert "Customer_dimensions" not in stmts[0]

    def test_per_model_mode_uses_fact_root_when_configured(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                model_fact_root="orders",
                measure_view_type="sql_view",
            )
        )
        model = SMLModel(
            unique_name="tpch",
            datasets=[
                SMLDataset(unique_name="orders", columns=[SMLColumn(unique_name="o_totalprice", data_type=DataType.DECIMAL)]),
                SMLDataset(unique_name="customer", columns=[SMLColumn(unique_name="c_custkey", data_type=DataType.INTEGER)]),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="orders",
                    source_column="o_totalprice",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "`main`.`public`.`tpch_metric_view`" in stmts[0]
        assert "FROM (SELECT 1 AS `_semabridge_anchor`) AS `semabridge_anchor`" in stmts[0]

    def test_per_model_mode_can_render_cross_table_metric_as_scalar_subquery(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="sql_view",
                enable_cross_table_joins=True,
            )
        )
        model = SMLModel(
            unique_name="tpch",
            datasets=[
                SMLDataset(unique_name="orders", columns=[SMLColumn(unique_name="o_custkey", data_type=DataType.INTEGER)]),
                SMLDataset(unique_name="customer", columns=[SMLColumn(unique_name="c_custkey", data_type=DataType.INTEGER)]),
                SMLDataset(unique_name="nation", columns=[SMLColumn(unique_name="n_nationkey", data_type=DataType.INTEGER)]),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="orders_to_customer",
                    from_dataset="orders",
                    from_columns=["o_custkey"],
                    to_dataset="customer",
                    to_columns=["c_custkey"],
                ),
                SMLRelationship(
                    unique_name="customer_to_nation",
                    from_dataset="customer",
                    from_columns=["c_custkey"],
                    to_dataset="nation",
                    to_columns=["n_nationkey"],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Nation_Count",
                    dataset="orders",
                    sql_expression='SUM(nation."n_nationkey")',
                    aggregation=AggregationType.COUNT_DISTINCT,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model,
            view_type_override=VIEW_TYPE_SQL,
        )

        assert created == 1
        assert "Nation_Count" in stmts[0]
        assert "main`.`public`.`nation".lower() in stmts[0].lower()

    def test_per_model_metric_view_emits_tpch_style_dimensions_yaml(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                model_fact_root="orders",
                measure_view_type="metric_view",
                enable_metric_view_joins=True,
                enable_cross_table_joins=True,
            )
        )
        model = SMLModel(
            unique_name="tpch",
            datasets=[
                SMLDataset(
                    unique_name="orders",
                    columns=[
                        SMLColumn(unique_name="o_orderkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="o_custkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="o_orderdate", data_type=DataType.DATE),
                        SMLColumn(unique_name="o_orderstatus", data_type=DataType.STRING),
                        SMLColumn(unique_name="o_orderpriority", data_type=DataType.STRING),
                        SMLColumn(unique_name="o_totalprice", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="customer",
                    columns=[
                        SMLColumn(unique_name="c_custkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="c_name", data_type=DataType.STRING),
                        SMLColumn(unique_name="c_mktsegment", data_type=DataType.STRING),
                        SMLColumn(unique_name="c_nationkey", data_type=DataType.INTEGER),
                    ],
                ),
                SMLDataset(
                    unique_name="nation",
                    columns=[
                        SMLColumn(unique_name="n_nationkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="n_name", data_type=DataType.STRING),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="orders_to_customer",
                    from_dataset="orders",
                    from_columns=["o_custkey"],
                    to_dataset="customer",
                    to_columns=["c_custkey"],
                ),
                SMLRelationship(
                    unique_name="customer_to_nation",
                    from_dataset="customer",
                    from_columns=["c_nationkey"],
                    to_dataset="nation",
                    to_columns=["n_nationkey"],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="total_revenue",
                    dataset="orders",
                    source_column="o_totalprice",
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
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "WITH METRICS LANGUAGE YAML" in stmts[0]
        assert "order_date" in stmts[0]
        assert "DATE_TRUNC('MONTH', o_orderdate)" in stmts[0]
        assert "customer.c_name" in stmts[0]
        assert "customer.nation.n_name" in stmts[0]
        assert "mv_tpch_customer_dimensions" not in stmts[0]
        assert "mv_tpch_nation_dimensions" not in stmts[0]

    def test_per_model_metric_view_aliases_hidden_keys_in_source_yaml(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                model_fact_root="Fact",
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
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL, is_hidden=True),
                    ],
                )
            ],
            metrics=[
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
            side_effect=lambda dataset, expected: expected,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"customer_key", "revenue"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "source: |" in stmts[0]
        assert "`customer_key` AS `fact_customer_key`" in stmts[0]
        assert "`revenue` AS `fact_revenue`" in stmts[0]
        assert 'expr: "count(distinct fact_customer_key)"' in stmts[0]

    def test_per_model_metric_view_schemaless_root_inlines_measure_columns(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                model_fact_root="Project Measures",
                measure_view_type="metric_view",
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    sql_expression="concat('Last Refreshed: ', cast(max(gl_refresh_datetime) as string))",
                    aggregation=AggregationType.NONE,
                )
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
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "CAST(NULL AS TIMESTAMP) AS `gl_refresh_datetime`" in stmts[0]

    def test_per_model_metric_view_auto_root_prefers_relational_table_over_measure_only_dataset(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="metric_view",
                enable_metric_view_joins=True,
                enable_cross_table_joins=True,
                enable_low_confidence_drafts=True,
            )
        )
        model = SMLModel(
            unique_name="FabricModel",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="customer_key", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="Customer",
                    columns=[
                        SMLColumn(unique_name="customer_key", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="region", data_type=DataType.STRING),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_to_customer",
                    from_dataset="Fact",
                    from_columns=["customer_key"],
                    to_dataset="Customer",
                    to_columns=["customer_key"],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Today",
                    dataset="Project Measures",
                    sql_expression="current_date()",
                    aggregation=AggregationType.NONE,
                ),
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    sql_expression="concat('Last Refreshed: ', cast(max(gl_refresh_datetime) as string))",
                    aggregation=AggregationType.NONE,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, expected: expected,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            side_effect=lambda source: {"gl_refresh_datetime"} if "Project_Measures" in source else {"customer_key", "amount", "region"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "source: |" in stmts[0]
        assert "FROM main.public.Fact" in stmts[0]
        assert "cust_region" in stmts[0]
        assert "expr: \"current_date()\"" in stmts[0]
        assert "(select current_date() from main.public.project_measures)" not in stmts[0]

    def test_per_model_metric_view_non_root_scalar_measure_emits_direct_expression(self):
        """Non-root scalar measures should not be wrapped in SELECT subqueries in metric-view YAML."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="metric_view",
                enable_metric_view_joins=True,
                enable_cross_table_joins=True,
            )
        )
        model = SMLModel(
            unique_name="ScalarModel",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Today",
                    dataset="Project Measures",
                    sql_expression="current_date()",
                    aggregation=AggregationType.NONE,
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
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "expr: \"current_date()\"" in stmts[0]
        assert "(select current_date()" not in stmts[0].lower()

    def test_per_model_sql_view_schemaless_root_inlines_measure_columns(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                model_fact_root="Project Measures",
                measure_view_type="sql_view",
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Corporate DSI Last Refreshed",
                    dataset="Project Measures",
                    sql_expression="concat('Last Refreshed: ', cast(max(gl_refresh_datetime) as string))",
                    aggregation=AggregationType.NONE,
                )
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
        assert skipped == 0
        assert not details
        assert "CAST(NULL AS TIMESTAMP) AS `gl_refresh_datetime`" in stmts[0]

    def test_per_model_sql_view_non_root_placeholder_table_inlines_missing_measure_columns(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                model_fact_root="Fact",
                measure_view_type="sql_view",
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="Project Measures",
                    columns=[
                        SMLColumn(unique_name="_semabridge_placeholder", data_type=DataType.STRING),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Source Value Total Stock",
                    dataset="Project Measures",
                    sql_expression="SUM(`source_value_total_stock`)",
                    aggregation=AggregationType.NONE,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, expected: expected,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            side_effect=lambda source: {"_semabridge_placeholder"}
            if str(source).strip().startswith("`")
            else set(),
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_SQL,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        lowered = stmts[0].lower()
        assert "cast(null as double) as `source_value_total_stock`" in lowered
        assert "sum(`source_value_total_stock`)" in lowered

    def test_per_model_metric_view_relocates_dummy_dataset_sum_to_fact_anchor(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="metric_view",
            )
        )
        model = SMLModel(
            unique_name="Inventory Semantic Model",
            datasets=[
                SMLDataset(unique_name="Project Measures", columns=[]),
                SMLDataset(
                    unique_name="Inventory Fact",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Revenue",
                    dataset="Project Measures",
                    expression="SUM('Inventory Fact'[Revenue])",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        with patch.object(
            publisher,
            "_resolve_existing_source_for_dataset",
            side_effect=lambda dataset, expected: expected,
        ), patch.object(
            publisher,
            "_get_source_table_columns",
            return_value={"revenue"},
        ):
            stmts, created, skipped, details = publisher.generate_measure_view_statements(
                model,
                view_type_override=VIEW_TYPE_METRIC,
            )

        assert created == 1
        assert skipped == 0
        assert not details
        assert "FROM main.public.Inventory_Fact" in stmts[0]
        assert "project_measures" not in stmts[0].lower()

    def test_identify_fact_datasets_prefers_many_side_and_aggregation_presence(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="metric_view",
            )
        )
        model = SMLModel(
            unique_name="FactDetectionModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="amount", data_type=DataType.DECIMAL)],
                ),
                SMLDataset(
                    unique_name="Customer",
                    columns=[SMLColumn(unique_name="customer_key", data_type=DataType.INTEGER)],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="sales_to_customer",
                    from_dataset="Sales",
                    from_columns=["customer_key"],
                    to_dataset="Customer",
                    to_columns=["customer_key"],
                    cardinality=Cardinality.MANY_TO_ONE,
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Sales",
                    dataset="Sales",
                    source_column="amount",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        facts = publisher._identify_fact_datasets(model)

        assert [dataset.unique_name for dataset in facts] == ["Sales"]

    def test_identify_fact_datasets_is_deterministic_for_multi_fact_models(self):
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                model_artifact_mode="per_model",
                measure_view_type="metric_view",
            )
        )
        model = SMLModel(
            unique_name="MultiFactModel",
            datasets=[
                SMLDataset(
                    unique_name="FactA",
                    columns=[SMLColumn(unique_name="a_amount", data_type=DataType.DECIMAL)],
                ),
                SMLDataset(
                    unique_name="FactB",
                    columns=[SMLColumn(unique_name="b_amount", data_type=DataType.DECIMAL)],
                ),
                SMLDataset(
                    unique_name="DimDate",
                    columns=[SMLColumn(unique_name="date_key", data_type=DataType.INTEGER)],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_a_to_date",
                    from_dataset="FactA",
                    from_columns=["date_key"],
                    to_dataset="DimDate",
                    to_columns=["date_key"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
                SMLRelationship(
                    unique_name="fact_b_to_date",
                    from_dataset="FactB",
                    from_columns=["date_key"],
                    to_dataset="DimDate",
                    to_columns=["date_key"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="A Revenue",
                    dataset="FactA",
                    source_column="a_amount",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="B Revenue",
                    dataset="FactB",
                    source_column="b_amount",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        facts_first_run = [dataset.unique_name for dataset in publisher._identify_fact_datasets(model)]
        facts_second_run = [dataset.unique_name for dataset in publisher._identify_fact_datasets(model)]

        assert facts_first_run == facts_second_run
        assert facts_first_run == ["FactA", "FactB"]
