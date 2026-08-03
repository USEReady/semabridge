import json
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
    """Verify that when OPENAI_API_KEY is configured, Databricks fallback tries OpenAI and returns the translated SQL.

    Updated for the DAX translation consolidation's Step 4 cutover:
    _try_llm_metric_fallback_expression now calls DaxTranslationService's
    Tier5Service, which (a) uses the central config's model default
    (gpt-4o-mini, not the old hardcoded gpt-4o), and (b) runs the response
    through the shared normalize/validate pipeline for the first time —
    Pipeline C never had semantic validation before this migration. The
    normalized SQL is still correct, executable Databricks SQL (uppercased
    column name, backtick quoting dropped since not required for this
    identifier) — just no longer a raw, unvalidated echo of the LLM's
    response. See Step 4's report for the full rationale.
    """
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

    # Normalized (uppercased, unquoted-since-not-needed) but still valid,
    # correct Databricks SQL referencing the same real column.
    assert sql == "SUM(sales.REVENUE)"
    # OpenAI succeeds on the first attempt, so Tier5Service returns
    # immediately without trying any further configured provider.
    mock_client.chat.completions.create.assert_called_once()

    # Inspect arguments to verify prompt contains Databricks rules and the
    # centrally-configured model (gpt-4o-mini), not the old hardcoded gpt-4o.
    kwargs = mock_client.chat.completions.create.call_args[1]
    assert kwargs["model"] == "gpt-4o-mini"
    messages = kwargs["messages"]
    assert "Databricks SQL" in messages[1]["content"] or "Databricks SQL" in messages[0]["content"]


def test_databricks_openai_fallback_rejected_forbidden_sql(monkeypatch):
    """Verify that if OpenAI returns forbidden SQL tokens, it rejects it and doesn't crash.

    Updated for Step 4: Pipeline C previously hardcoded OpenAI->Gemini only.
    It now goes through Tier5Service's centrally-configured provider order
    (openai, gemini, groq, featherless) — this environment has a GROQ_API_KEY
    name present, so Groq is also tried after OpenAI's response is rejected,
    via its own direct OpenAI-compatible-client leg (which also imports
    `openai.OpenAI`, so the same mock intercepts it too — hence 2 calls, not
    1). The functional outcome this test actually cares about — a forbidden
    SELECT statement is rejected and the function returns None without
    crashing — is unchanged and still verified below.
    """
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

    sql = publisher._try_llm_metric_fallback_expression(metric, dataset, model)

    # The forbidden SELECT statement must be rejected — no valid translation
    # is returned, and the call must not crash.
    assert sql is None
    # OpenAI (the first configured provider) was tried at least once, with
    # the rejected response.
    assert mock_client.chat.completions.create.call_count >= 1
    first_call_kwargs = mock_client.chat.completions.create.call_args_list[0][1]
    assert first_call_kwargs["model"] == "gpt-4o-mini"


def test_sml_loader_openai_batch_translation(monkeypatch):
    """Verify DAXTranslator.batch_translate_tier5 successfully translates
    multiple metrics via OpenAI.

    Updated again for the DAX translation consolidation's batching-restore
    task: batch_translate_tier5 now calls Tier5Service.translate_batch(),
    which sends every remaining candidate as ONE JSON-map prompt/response
    per provider call instead of one call per metric — see
    dax_translator.py's batch_translate_tier5 docstring. This test now
    asserts the restored one-call contract (call_count == 1 regardless of
    metric count) and mocks a single JSON response mapping the batch's
    synthetic per-position keys (m0, m1, ...) to SQL, matching
    tier5/prompt.py's build_batch_prompt/parse_batch_payload contract.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "mock-openai-key")

    batch_response = json.dumps({"m0": "SUM(`sales`.`revenue`)", "m1": "AVG(`sales`.`cost`)"})
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=batch_response))]
    )

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
        # ensuring these reach the Tier 5 LLM path.
        metrics_list = [
            ("MetricA", "RANKX(ALL('Sales'), [Total Revenue])", "sales", "sales"),
            ("MetricB", "TOPN(10, VALUES('Sales'[Region]), [Total Cost], DESC)", "sales", "sales"),
        ]

        # dax_translator.py's batch_translate_tier5 always requests
        # dialect="snowflake" internally regardless of the real target, so
        # this mocked backtick-quoted response must resolve against real
        # schema data — item 18's validator fix (backtick presence, not
        # declared dialect, now triggers quote normalization before
        # validation) means an empty lookup here would now genuinely fail
        # alias resolution instead of silently passing unexamined.
        dataset_col_lookup = {"sales": {"revenue", "cost"}}
        dataset_aliases = {"sales": "sales"}

        results = translator.batch_translate_tier5(
            metrics_list,
            dataset_col_lookup=dataset_col_lookup,
            dataset_aliases=dataset_aliases,
        )

        # batch_translate_tier5 always declares dialect="snowflake" for this
        # shim, so the mocked backtick-quoted response is genuinely
        # validated/normalized to Snowflake-style quoting (item 18's fix —
        # backtick presence, not declared dialect, now triggers that
        # normalization) rather than passing through untouched, which only
        # ever happened before because non-Databricks-dialect backtick SQL
        # was invisible to validation entirely.
        assert "MetricA" in results
        assert results["MetricA"] is not None
        assert results["MetricA"].is_success is True
        assert results["MetricA"].sql == "SUM(sales.revenue)"

        assert "MetricB" in results
        assert results["MetricB"] is not None
        assert results["MetricB"].is_success is True
        assert results["MetricB"].sql == "AVG(sales.cost)"

        # The whole point of restoring batching: 2 metrics needing Tier 5
        # cost exactly 1 API call, not 2.
        assert mock_client.chat.completions.create.call_count == 1


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
