from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from semabridge.converter.common_dax_translator import (
    CommonDAXTranslator,
    LLMProviderConfig,
    LLMProviderName,
    SQLDialect,
)


@dataclass
class FakeProvider:
    config: LLMProviderConfig
    response: str = ""
    error: Exception | None = None

    def generate(self, prompt: str, timeout_seconds: int) -> str:
        if self.error:
            raise self.error
        return self.response


def _test_dir() -> Path:
    path = Path("Scratch") / f"common-dax-test-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_common_dax_translator_falls_back_to_second_provider():
    test_dir = _test_dir()
    translator = CommonDAXTranslator(
        dialect=SQLDialect.DATABRICKS,
        cache_enabled=False,
        raw_response_dir=test_dir,
        providers=[
            FakeProvider(
                LLMProviderConfig(LLMProviderName.DEEPSEEK, "deepseek-test", "DEEPSEEK_API_KEY"),
                error=TimeoutError("slow"),
            ),
            FakeProvider(
                LLMProviderConfig(LLMProviderName.GROQ, "groq-test", "GROQ_API_KEY"),
                response="SUM(CASE WHEN `sales`.`region` = 'North' THEN `sales`.`revenue` ELSE 0 END)",
            ),
        ],
    )

    result = translator.translate(
        dax='CALCULATE(SUM(Sales[Revenue]), Sales[Region] = "North")',
        metric_name="North Revenue",
        dataset_name="Sales",
        table_alias="sales",
        schema_context={"sales": ["revenue", "region"]},
    )

    assert result.is_valid
    assert result.provider == "groq"
    assert result.attempted_providers == ["deepseek", "groq"]
    assert "CASE WHEN" in result.sql


def test_common_dax_translator_returns_placeholder_when_all_providers_fail():
    test_dir = _test_dir()
    translator = CommonDAXTranslator(
        dialect=SQLDialect.DATABRICKS,
        cache_enabled=False,
        raw_response_dir=test_dir,
        placeholder_sql="CAST(NULL AS DOUBLE)",
        providers=[
            FakeProvider(
                LLMProviderConfig(LLMProviderName.DEEPSEEK, "deepseek-test", "DEEPSEEK_API_KEY"),
                error=RuntimeError("unavailable"),
            )
        ],
    )

    result = translator.translate(
        dax="SOME_COMPLEX_DAX()",
        metric_name="Complex Metric",
        dataset_name="Sales",
        table_alias="sales",
        schema_context={"sales": ["revenue"]},
    )

    assert result.is_valid
    assert result.fallback_used
    assert result.sql == "CAST(NULL AS DOUBLE)"


def test_common_dax_translator_ignores_cached_placeholder():
    test_dir = _test_dir()
    cache_file = test_dir / "cache.json"
    cache_file.write_text(
        """
{
  "cached-key": {
    "sql": "CAST(NULL AS DOUBLE)",
    "is_valid": true,
    "provider": "",
    "model": "",
    "fallback_used": true,
    "timestamp": "2999-01-01T00:00:00"
  }
}
""".strip(),
        encoding="utf-8",
    )

    translator = CommonDAXTranslator(
        dialect=SQLDialect.DATABRICKS,
        cache_file=cache_file,
        providers=[],
    )

    assert translator.cache == {}


def test_common_dax_translator_repairs_invalid_provider_output():
    test_dir = _test_dir()
    provider = FakeProvider(
        LLMProviderConfig(LLMProviderName.GROQ, "groq-test", "GROQ_API_KEY"),
        response="SELECT SUM(revenue) FROM sales",
    )

    calls = {"count": 0}

    def generate(prompt: str, timeout_seconds: int) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            return "SELECT SUM(revenue) FROM sales"
        return "SUM(`sales`.`revenue`)"

    provider.generate = generate
    translator = CommonDAXTranslator(
        dialect=SQLDialect.DATABRICKS,
        cache_enabled=False,
        raw_response_dir=test_dir,
        providers=[provider],
    )

    result = translator.translate(
        dax="SUM(Sales[Revenue])",
        metric_name="Revenue",
        dataset_name="Sales",
        table_alias="sales",
        schema_context={"sales": ["revenue"]},
    )

    assert result.is_valid
    assert result.sql == "SUM(`sales`.`revenue`)"
    assert calls["count"] == 2


def test_common_dax_translator_default_provider_order_prefers_deepseek(monkeypatch):
    monkeypatch.delenv("DAX_LLM_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("USE_GEMINI", "true")

    translator = CommonDAXTranslator(
        dialect=SQLDialect.DATABRICKS,
        cache_enabled=False,
    )

    assert [provider.config.name.value for provider in translator.providers][:3] == [
        "deepseek",
        "gemini",
        "groq",
    ]
