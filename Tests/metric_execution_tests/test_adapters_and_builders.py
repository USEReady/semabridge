"""Tests for adapters (Fabric, Power BI, Mock) and SQL builders."""
import pytest
from semabridge.ai.metric_execution.adapters.mock_source_adapter import MockSourceAdapter
from semabridge.ai.metric_execution.adapters.registry import get_source_adapter
from semabridge.ai.metric_execution.sql_builders import get_target_sql_builder


def test_mock_adapter_loads_snapshot_fixture():
    adapter = MockSourceAdapter({"project_id": "test", "fixtures_dir": "Tests/fixtures/baselines"})
    res = adapter.execute_metric("Total Sales")
    assert res.success is True
    assert res.platform == "MOCK"
    assert res.raw_value == 1542000.5


def test_mock_adapter_missing_fixture_is_not_comparable():
    # Acceptance Criterion 4: Never fabricates values
    adapter = MockSourceAdapter({"project_id": "test", "fixtures_dir": "Tests/fixtures/baselines"})
    res = adapter.execute_metric("NonExistentMetric")
    assert res.success is False
    assert res.is_not_comparable is True
    assert "No persisted baseline fixture found" in res.error


def test_snowflake_sql_builder():
    builder = get_target_sql_builder("SNOWFLAKE")
    query = builder.build_metric_query("DB.SCHEMA.SEM_VIEW", "Total_Sales")
    assert query == "SELECT * FROM SEMANTIC_VIEW(DB.SCHEMA.SEM_VIEW METRICS Total_Sales);"

    sliced = builder.build_metric_query("DB.SCHEMA.SEM_VIEW", "Total_Sales", {"YEAR": 2024})
    assert "WHERE YEAR = 2024" in sliced


def test_databricks_sql_builder():
    builder = get_target_sql_builder("DATABRICKS")
    query = builder.build_metric_query("catalog.schema.metric_view", "total_sales")
    assert query == "SELECT MEASURE(`total_sales`) FROM `catalog`.`schema`.`metric_view`;"

    sliced = builder.build_metric_query("catalog.schema.metric_view", "total_sales", {"year": 2024})
    assert "WHERE `year` = 2024" in sliced
