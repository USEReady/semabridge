import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, DataType

def _cfg():
    from semabridge.core.settings import DatabricksConfig
    return DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )

def test_databricks_openai_fallback_success(monkeypatch):
    """Verify that when OPENAI_API_KEY is configured, Databricks fallback tries OpenAI and returns the translated SQL."""
    monkeypatch.setenv("OPENAI_API_KEY", "mock-openai-key")
    
    # Mock OpenAI client
    mock_choices = [SimpleNamespace(message=SimpleNamespace(content="SUM(`sales`.`revenue`)"))]
    mock_response = SimpleNamespace(choices=mock_choices)
    
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    
    class MockOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        chat = mock_client.chat

    # Patch sys.modules to mock 'openai' package
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=MockOpenAI))

    publisher = DatabricksPublisher(_cfg())
    
    model = SMLModel(
        unique_name="TestModel",
        datasets=[
            SMLDataset(
                unique_name="sales",
                columns=[SMLColumn(unique_name="revenue", data_type=DataType.DECIMAL)]
            )
        ],
        metrics=[]
    )
    metric = SMLMetric(unique_name="TotalRevenue", expression="SUM([sales].[revenue])", dataset="sales")
    dataset = model.datasets[0]

    # Invoke fallback
    sql = publisher._try_llm_metric_fallback_expression(metric, dataset, model)

    assert sql == "SUM(`sales`.`revenue`)"
    mock_client.chat.completions.create.assert_called_once()
    
    # Inspect arguments to verify prompt contains Databricks rules
    kwargs = mock_client.chat.completions.create.call_args[1]
    assert kwargs["model"] == "gpt-4o"
    messages = kwargs["messages"]
    assert "Databricks SQL" in messages[1]["content"] or "Databricks SQL" in messages[0]["content"]


def test_databricks_openai_fallback_rejected_forbidden_sql(monkeypatch):
    """Verify that if OpenAI returns forbidden SQL tokens, it rejects it and doesn't crash."""
    monkeypatch.setenv("OPENAI_API_KEY", "mock-openai-key")
    
    # Mock OpenAI client returning forbidden SELECT statement
    mock_choices = [SimpleNamespace(message=SimpleNamespace(content="SELECT SUM(`sales`.`revenue`) FROM `sales`"))]
    mock_response = SimpleNamespace(choices=mock_choices)
    
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    
    class MockOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        chat = mock_client.chat

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=MockOpenAI))

    publisher = DatabricksPublisher(_cfg())
    
    model = SMLModel(
        unique_name="TestModel",
        datasets=[
            SMLDataset(
                unique_name="sales",
                columns=[SMLColumn(unique_name="revenue", data_type=DataType.DECIMAL)]
            )
        ],
        metrics=[]
    )
    metric = SMLMetric(unique_name="TotalRevenue", expression="SUM([sales].[revenue])", dataset="sales")
    dataset = model.datasets[0]

    # Patch Gemini to return None or assert it falls back to Gemini
    with patch("semabridge.converter.gemini_dax_translator.get_gemini_translator") as mock_gemini_get:
        mock_translator = MagicMock()
        mock_translator.use_gemini = True
        mock_translator.api_key = None  # Force it to return None in Gemini
        mock_gemini_get.return_value = mock_translator
        
        sql = publisher._try_llm_metric_fallback_expression(metric, dataset, model)
        
        # It should reject the SELECT statement and fall back to Gemini, which returns None due to no API key
        assert sql is None
        mock_client.chat.completions.create.assert_called_once()
        mock_gemini_get.assert_called_once()


def test_sml_loader_openai_batch_translation(monkeypatch):
    """Verify SML loader batch translation calls OpenAI and successfully parses JSON response."""
    monkeypatch.setenv("OPENAI_API_KEY", "mock-openai-key")
    
    # Mock JSON response payload
    mock_payload = '{"MetricA": "SUM(`sales`.`revenue`)", "MetricB": "AVG(`sales`.`cost`)"}'
    mock_choices = [SimpleNamespace(message=SimpleNamespace(content=mock_payload))]
    mock_response = SimpleNamespace(choices=mock_choices)
    
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    
    class MockOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        chat = mock_client.chat

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=MockOpenAI))
    
    # Also mock Gemini so it doesn't interfere
    with patch("semabridge.converter.gemini_dax_translator.get_gemini_translator") as mock_gemini_get:
        mock_gemini_translator = MagicMock()
        mock_gemini_translator.api_key = None  # Ensure Gemini fallback is disabled
        mock_gemini_get.return_value = mock_gemini_translator

        from semabridge.converter.dax_translator import DAXTranslator

        translator = DAXTranslator()
        # Use RANKX / TOPN — truly opaque expressions the deterministic engine cannot handle,
        # ensuring these reach the OpenAI Priority-0 batch path.
        metrics_list = [
            ("MetricA", "RANKX(ALL('Sales'), [Total Revenue])", "sales", "sales"),
            ("MetricB", "TOPN(10, VALUES('Sales'[Region]), [Total Cost], DESC)", "sales", "sales"),
        ]

        results = translator.batch_translate_tier5(metrics_list)

        assert "MetricA" in results
        assert results["MetricA"] is not None
        assert results["MetricA"].is_success is True
        assert results["MetricA"].sql == "SUM(`sales`.`revenue`)"

        assert "MetricB" in results
        assert results["MetricB"] is not None
        assert results["MetricB"].is_success is True
        assert results["MetricB"].sql == "AVG(`sales`.`cost`)"

        mock_client.chat.completions.create.assert_called_once()


def test_translator_bare_metric_qualification():
    """Verify that bare metric references are correctly qualified with their owning dataset's alias."""
    from semabridge.connectors.translator import MetricExpressionTranslator
    from semabridge.utils.identifiers import IdentifierSanitizer

    ids = IdentifierSanitizer(force_uppercase=True)
    translator = MetricExpressionTranslator(identifier_sanitizer=ids)

    metric_sql = 'CASE WHEN "SENTIMENT_GAP" < 15 THEN 1 WHEN "SENTIMENT_GAP" > 25 THEN 3 ELSE 2 END'
    dataset_col_lookup = {"Sentiment": {"SCORE"}, "SalesFact": {"UNITS", "REVENUE"}}
    dataset_aliases = {"Sentiment": "SENTIMENT", "SalesFact": "SALESFACT"}
    metric_names = {"SENTIMENT_GAP"}
    metric_to_alias = {"SENTIMENT_GAP": "SENTIMENT"}

    # Qualify
    expr = translator._normalize_metric_column_references(
        metric_sql=metric_sql,
        metric_name="INDICATOR05",
        dataset_col_lookup=dataset_col_lookup,
        dataset_aliases=dataset_aliases,
        metric_names=metric_names,
        preferred_table_alias="SALESFACT",
        metric_to_alias=metric_to_alias
    )

    assert 'SENTIMENT."SENTIMENT_GAP"' in expr
    assert 'SALESFACT' not in expr or 'SALESFACT."SENTIMENT_GAP"' not in expr
