"""Tests for MetricComparisonService execution orchestration."""
import pytest
from unittest.mock import MagicMock, patch
from semabridge.ai.metric_execution.service import MetricComparisonService


@pytest.mark.asyncio
async def test_run_comparison_mock_mode_pass():
    service = MetricComparisonService()

    # Mock the target execution to return matching value for 'Total Sales' (1542000.5)
    with patch.object(
        service,
        "_execute_target",
        return_value={
            "success": True,
            "platform": "SNOWFLAKE",
            "query_text": "SELECT * FROM SEMANTIC_VIEW(V METRICS Total_Sales);",
            "raw_value": 1542000.5000,
            "execution_ms": 15.0,
            "error": None,
        },
    ):
        result = await service.run_comparison_async(
            project_id="test_proj",
            metric_name="Total Sales",
            source_platform="mock",
            target_platform="SNOWFLAKE",
            target_view="DB.SCHEMA.SEM_VIEW",
            source_config={"fixtures_dir": "Tests/fixtures/baselines"},
            target_config={},
            tolerance_pct=0.01,
            mock_mode=True,
        )

        assert result["verdict"] == "PASS"
        assert result["passed"] is True
        assert result["source"]["normalized_value"] == "1542000.5000"
        assert result["target"]["normalized_value"] == "1542000.5000"


@pytest.mark.asyncio
async def test_run_comparison_missing_fixture_not_comparable():
    service = MetricComparisonService()
    result = await service.run_comparison_async(
        project_id="test_proj",
        metric_name="UnknownMeasure",
        source_platform="mock",
        target_platform="DATABRICKS",
        target_view="cat.sch.view",
        source_config={"fixtures_dir": "Tests/fixtures/baselines"},
        target_config={},
        mock_mode=True,
    )
    assert result["verdict"] == "NOT_COMPARABLE"
    assert result["passed"] is False
