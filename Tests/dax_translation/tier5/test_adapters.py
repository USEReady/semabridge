"""Unit tests for the Tier 5 provider adapters — synthetic data only, no
real network calls. Each adapter's is_available()/no-key behavior is
proven directly; one mocked happy path per adapter proves response
parsing and markdown-fence stripping without hitting any real API.
"""
from types import SimpleNamespace

import pytest

from semabridge.dax_translation.tier5.config import ProviderSettings
from semabridge.dax_translation.tier5.adapters.base import strip_markdown_fences
from semabridge.dax_translation.tier5.adapters.openai_adapter import OpenAIAdapter
from semabridge.dax_translation.tier5.adapters.gemini_adapter import GeminiAdapter, score_confidence
from semabridge.dax_translation.tier5.adapters.groq_adapter import GroqAdapter
from semabridge.dax_translation.tier5.adapters.featherless_adapter import FeatherlessAdapter

_PROMPT = "Dialect: Snowflake Semantic View METRICS clause\nDAX:\nSUM('SomeTable'[SomeColumn])"
_SYSTEM = "You translate Power BI DAX measures to Snowflake Semantic View metric SQL."


def test_strip_markdown_fences():
    assert strip_markdown_fences("```sql\nSUM(x)\n```") == "SUM(x)"
    assert strip_markdown_fences(None) == ""
    assert strip_markdown_fences("") == ""


# --- OpenAI -----------------------------------------------------------------

def test_openai_adapter_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = OpenAIAdapter(ProviderSettings(enabled_env="OPENAI_API_KEY", model="gpt-4o-mini"))
    assert adapter.is_available() is False
    assert adapter.translate(_PROMPT, _SYSTEM) is None


def test_openai_adapter_available_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    adapter = OpenAIAdapter(ProviderSettings(enabled_env="OPENAI_API_KEY", model="gpt-4o-mini"))
    assert adapter.is_available() is True


def test_openai_adapter_happy_path_parses_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

    fake_message = SimpleNamespace(content="```sql\nSUM(sometable.\"SOMECOLUMN\")\n```")
    fake_choice = SimpleNamespace(message=fake_message)
    fake_response = SimpleNamespace(choices=[fake_choice])

    class FakeClient:
        def __init__(self, *a, **k):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kw: fake_response)
            )

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    adapter = OpenAIAdapter(ProviderSettings(enabled_env="OPENAI_API_KEY", model="gpt-4o-mini", max_retries=1))
    result = adapter.translate(_PROMPT, _SYSTEM)
    assert result is not None
    assert result.text == 'SUM(sometable."SOMECOLUMN")'


# --- Gemini -------------------------------------------------------------

def test_gemini_adapter_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    adapter = GeminiAdapter(ProviderSettings(enabled_env="GEMINI_API_KEY"))
    assert adapter.is_available() is False
    assert adapter.translate(_PROMPT, _SYSTEM) is None


def test_gemini_adapter_happy_path_parses_response_and_scores_confidence(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    fake_service = SimpleNamespace(
        is_available=True,
        call=lambda prompt, fallback_fn=None: "```sql\nSUM(sometable.\"SOMECOLUMN\")\n```",
    )
    monkeypatch.setattr(
        "semabridge.converter.gemini_api_service.get_gemini_service",
        lambda: fake_service,
    )

    adapter = GeminiAdapter(ProviderSettings(enabled_env="GEMINI_API_KEY"))
    result = adapter.translate(_PROMPT, _SYSTEM)
    assert result is not None
    assert result.text == 'SUM(sometable."SOMECOLUMN")'
    assert 0.0 <= result.confidence <= 1.0


def test_score_confidence_penalizes_select_shapes():
    good = score_confidence('SUM(sometable."SOMECOLUMN")', "SUM('SomeTable'[SomeColumn])")
    bad = score_confidence('SELECT * FROM sometable', "SUM('SomeTable'[SomeColumn])")
    assert good > bad


# --- Groq -----------------------------------------------------------------

def test_groq_adapter_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    adapter = GroqAdapter(ProviderSettings(enabled_env="GROQ_API_KEY", model="llama-3.3-70b-versatile"))
    assert adapter.is_available() is False
    assert adapter.translate(_PROMPT, _SYSTEM) is None


def test_groq_adapter_happy_path_via_chatgroq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    class FakeChatGroq:
        def __init__(self, *a, **k):
            pass

        def invoke(self, messages):
            return SimpleNamespace(content='SUM(sometable."SOMECOLUMN")')

    monkeypatch.setattr("langchain_groq.ChatGroq", FakeChatGroq)

    adapter = GroqAdapter(ProviderSettings(enabled_env="GROQ_API_KEY", model="llama-3.3-70b-versatile"))
    result = adapter.translate(_PROMPT, _SYSTEM)
    assert result is not None
    assert result.text == 'SUM(sometable."SOMECOLUMN")'


# --- Featherless ------------------------------------------------------------

def test_featherless_adapter_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("FEATHERLESS_API_KEY", raising=False)
    adapter = FeatherlessAdapter(ProviderSettings(enabled_env="FEATHERLESS_API_KEY", models=["fake/model-a"]))
    assert adapter.is_available() is False
    assert adapter.translate(_PROMPT, _SYSTEM) is None


def test_featherless_adapter_happy_path_via_chatopenai(monkeypatch):
    monkeypatch.setenv("FEATHERLESS_API_KEY", "fake-key")

    class FakeChatOpenAI:
        def __init__(self, *a, **k):
            pass

        def invoke(self, messages):
            return SimpleNamespace(content='SUM(sometable."SOMECOLUMN")')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    adapter = FeatherlessAdapter(
        ProviderSettings(enabled_env="FEATHERLESS_API_KEY", models=["deepseek-ai/DeepSeek-V4-Pro"])
    )
    result = adapter.translate(_PROMPT, _SYSTEM)
    assert result is not None
    assert result.text == 'SUM(sometable."SOMECOLUMN")'


def test_featherless_adapter_reads_configured_timeout(monkeypatch):
    """Regression: the adapter used to hardcode timeout=30 unconditionally,
    never reading ProviderSettings.timeout_seconds even though the field
    already existed and every sibling adapter (openai, groq) already read
    it — editing Tier5Config had zero effect for Featherless specifically."""
    monkeypatch.setenv("FEATHERLESS_API_KEY", "fake-key")
    captured_kwargs = {}

    class FakeChatOpenAI:
        def __init__(self, *a, **k):
            captured_kwargs.update(k)

        def invoke(self, messages):
            return SimpleNamespace(content='SUM(sometable."SOMECOLUMN")')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    adapter = FeatherlessAdapter(
        ProviderSettings(
            enabled_env="FEATHERLESS_API_KEY",
            models=["deepseek-ai/DeepSeek-V4-Pro"],
            timeout_seconds=7,
        )
    )
    adapter.translate(_PROMPT, _SYSTEM)
    assert captured_kwargs["timeout"] == 7.0


def test_featherless_adapter_defaults_timeout_to_30_when_unconfigured(monkeypatch):
    """Zero behavior change for today's default config, which never sets
    timeout_seconds for Featherless: float(None or 30) == 30.0, the exact
    value the old hardcoded literal produced."""
    monkeypatch.setenv("FEATHERLESS_API_KEY", "fake-key")
    captured_kwargs = {}

    class FakeChatOpenAI:
        def __init__(self, *a, **k):
            captured_kwargs.update(k)

        def invoke(self, messages):
            return SimpleNamespace(content='SUM(sometable."SOMECOLUMN")')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    adapter = FeatherlessAdapter(
        ProviderSettings(enabled_env="FEATHERLESS_API_KEY", models=["deepseek-ai/DeepSeek-V4-Pro"])
    )
    adapter.translate(_PROMPT, _SYSTEM)
    assert captured_kwargs["timeout"] == 30.0


def test_featherless_adapter_tries_next_model_on_failure(monkeypatch):
    monkeypatch.setenv("FEATHERLESS_API_KEY", "fake-key")

    calls = []

    class FlakyThenGoodChatOpenAI:
        def __init__(self, *a, model=None, **k):
            self._model = model

        def invoke(self, messages):
            calls.append(self._model)
            if self._model == "fake/model-a":
                raise RuntimeError("simulated provider failure")
            return SimpleNamespace(content='SUM(sometable."SOMECOLUMN")')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FlakyThenGoodChatOpenAI)

    adapter = FeatherlessAdapter(
        ProviderSettings(enabled_env="FEATHERLESS_API_KEY", models=["fake/model-a", "fake/model-b"])
    )
    result = adapter.translate(_PROMPT, _SYSTEM)
    assert result is not None
    assert calls == ["fake/model-a", "fake/model-b"]


_FIVE_MODEL_LIST = [
    "fake/model-1",
    "fake/model-2",
    "fake/model-3",
    "fake/model-4",
    "fake/model-5",
]


def test_featherless_adapter_walks_the_full_five_model_failover_chain(monkeypatch):
    """The real salvaged failover list (featherless_translator.py:80-86) has
    5 models. The two tests above only exercise 1 and 2 of them — this
    proves the loop actually walks every entry in order, not just the
    first couple, by failing the first 4 and succeeding only on the 5th."""
    monkeypatch.setenv("FEATHERLESS_API_KEY", "fake-key")

    calls = []

    class FailUntilLastChatOpenAI:
        def __init__(self, *a, model=None, **k):
            self._model = model

        def invoke(self, messages):
            calls.append(self._model)
            if self._model != _FIVE_MODEL_LIST[-1]:
                raise RuntimeError(f"simulated failure for {self._model}")
            return SimpleNamespace(content='SUM(sometable."SOMECOLUMN")')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FailUntilLastChatOpenAI)

    adapter = FeatherlessAdapter(
        ProviderSettings(enabled_env="FEATHERLESS_API_KEY", models=list(_FIVE_MODEL_LIST))
    )
    result = adapter.translate(_PROMPT, _SYSTEM)
    assert result is not None
    assert result.text == 'SUM(sometable."SOMECOLUMN")'
    assert calls == _FIVE_MODEL_LIST  # every model tried, in order, none skipped


def test_featherless_adapter_returns_none_when_every_model_in_the_chain_fails(monkeypatch):
    monkeypatch.setenv("FEATHERLESS_API_KEY", "fake-key")

    calls = []

    class AlwaysFailsChatOpenAI:
        def __init__(self, *a, model=None, **k):
            self._model = model

        def invoke(self, messages):
            calls.append(self._model)
            raise RuntimeError(f"simulated failure for {self._model}")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", AlwaysFailsChatOpenAI)

    adapter = FeatherlessAdapter(
        ProviderSettings(enabled_env="FEATHERLESS_API_KEY", models=list(_FIVE_MODEL_LIST))
    )
    result = adapter.translate(_PROMPT, _SYSTEM)
    assert result is None
    assert calls == _FIVE_MODEL_LIST  # every model was tried before giving up


def test_featherless_adapter_default_config_model_list_length_is_covered_by_the_chain_test():
    """Sanity check that the real config default isn't longer than what the
    chain test above exercises (would silently under-cover a future list
    extension)."""
    from semabridge.dax_translation.tier5.config import Tier5Config

    real_models = Tier5Config.default().providers["featherless"].models
    assert len(real_models) <= len(_FIVE_MODEL_LIST)
