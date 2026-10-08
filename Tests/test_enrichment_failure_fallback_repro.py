"""
Reproduction test for enrichment failure fallback behavior (Step 0 Controlled Pair).
Tests Case A (static source_table_mapping removed) and Case B (static source_table_mapping present).
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder, resolve_source_table_mapping
from semabridge.connectors.ddl_builder import AliasRegistry
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship


def _build_mock_model_with_date():
    date_ds = SMLDataset(
        unique_name="Date",
        source_table="Dates",
        columns=[
            SMLColumn(unique_name="Date", is_key=True),
            SMLColumn(unique_name="FiscalYrPeriod"),
        ]
    )
    sales_ds = SMLDataset(
        unique_name="SalesFact",
        source_table="SalesFact",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="ID", is_key=True),
            SMLColumn(unique_name="OrderDate"),
            SMLColumn(unique_name="SalesAmount"),
        ]
    )
    rel = SMLRelationship(
        unique_name="Rel_Sales_Date",
        from_dataset="SalesFact",
        from_columns=["OrderDate"],
        to_dataset="Date",
        to_columns=["Date"],
        is_active=True
    )
    metric = SMLMetric(
        unique_name="Total_Sales_YTD",
        dataset="SalesFact",
        sql_expression="SUM(CASE WHEN SalesFact.OrderDate >= DATE_TRUNC('YEAR', MAX_DATE) THEN SalesFact.SalesAmount END)"
    )
    return SMLModel(
        unique_name="TestModel",
        label="TestModel",
        datasets=[date_ds, sales_ds],
        relationships=[rel],
        metrics=[metric]
    )


def run_test_for_case(static_mapping_present: bool):
    cfg = SnowflakeConfig(
        account="TEST_ACC", user="TEST_USER", password="TEST_PASSWORD",
        warehouse="TEST_WH", database="SEMABRIDGE", schema_name="SEMABRIDGE_WORKSPACE",
        role="TEST_ROLE"
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.auto_create_enriched_view = True
    behavior.snowflake.use_enriched_view_for_metrics = True
    if static_mapping_present:
        behavior.snowflake.source_table_mapping = {"SalesFact": "SEMABRIDGE.PUBLIC.SALESFACT_ENRICHED"}
    else:
        behavior.snowflake.source_table_mapping = {}

    emitter = SnowflakeEmitter(config=cfg, behavior=behavior)
    model = _build_mock_model_with_date()

    executed_queries = []
    mock_cursor = MagicMock()
    def mock_execute(sql, *args, **kwargs):
        executed_queries.append(str(sql))
        if "CREATE OR REPLACE VIEW" in str(sql).upper():
            raise Exception("SQL compilation error: DATE_TRUNC NULL argument invalid")
        return None
    
    mock_cursor.execute.side_effect = mock_execute
    mock_cursor.fetchone.return_value = (None,)

    # Step 1.5: enrichment
    enriched_res = emitter._create_enriched_view(model, mock_cursor, fact_table="SalesFact")
    
    # Build TABLES clause
    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=emitter._id,
        schema_manager=emitter.schema_manager,
        config=cfg,
        behavior=behavior,
        live_schema_metadata={"SALESFACT": {"ID", "ORDERDATE", "SALESAMOUNT"}},
        cursor=mock_cursor,
        enriched_view_mapping=emitter._enriched_view_mapping
    )
    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=model,
        registry=AliasRegistry(),
        metric_counts_by_dataset={"SalesFact": 1},
        related_datasets={"Date"}
    )
    tables_ddl = "\n".join(tables_lines)
    return enriched_res, tables_ddl, executed_queries


def test_step0_control_pair():
    # Case A: Static source_table_mapping removed
    res_a, ddl_a, queries_a = run_test_for_case(static_mapping_present=False)
    print("\n--- CASE A (No static mapping) ---")
    print(f"Enriched View Result: {res_a}")
    print(f"Executed Anchor Queries: {queries_a}")
    print(f"Emitted TABLES DDL:\n{ddl_a}")

    # Case B: Static source_table_mapping present
    res_b, ddl_b, queries_b = run_test_for_case(static_mapping_present=True)
    print("\n--- CASE B (Static mapping present) ---")
    print(f"Enriched View Result: {res_b}")
    print(f"Executed Anchor Queries: {queries_b}")
    print(f"Emitted TABLES DDL:\n{ddl_b}")

    # Verify Case A references base table SALESFACT
    assert "SALESFACT_ENRICHED" not in ddl_a, f"Case A failed: DDL emitted enriched view unexpectedly: {ddl_a}"
    assert '"SALESFACT"' in ddl_a or 'SALESFACT AS' in ddl_a, f"Case A failed: DDL did not reference base table: {ddl_a}"

    # Verify Case B also references base table SALESFACT now (static mapping overridden by runtime failure)
    assert "SALESFACT_ENRICHED" not in ddl_b, f"Case B failed: Static mapping was not overridden by runtime failure: {ddl_b}"
    assert '"SALESFACT"' in ddl_b or 'SALESFACT AS' in ddl_b, f"Case B failed: DDL did not reference base table: {ddl_b}"
