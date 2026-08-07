"""Unit tests for the Tier 5 provider adapters — synthetic data only, no
real network calls. Each adapter's is_available()/no-key behavior is
proven directly; one mocked happy path per adapter proves response
parsing and markdown-fence stripping without hitting any real API.
"""
from types import SimpleNamespace

import pytest

from semabridge.dax_translation.tier5.config import ProviderSettings, Tier5Config
from semabridge.dax_translation.tier5.adapters.base import strip_markdown_fences
from semabridge.dax_translation.tier5.adapters.openai_adapter import OpenAIAdapter
from semabridge.dax_translation.tier5.adapters.gemini_adapter import GeminiAdapter, score_confidence
from semabridge.dax_translation.tier5.adapters.groq_adapter import GroqAdapter
from semabridge.dax_translation.tier5.adapters.featherless_adapter import FeatherlessAdapter
from semabridge.dax_translation.tier5.adapters.anthropic_adapter import (
    AnthropicAdapter,
    select_cheapest_known_model,
)
from semabridge.dax_translation.tier5.service import Tier5Service
from semabridge.dax_translation.types import TranslationRequest

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


# --- Anthropic ----------------------------------------------------------

def _fake_anthropic_response(text, stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )


def test_anthropic_adapter_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY"))
    assert adapter.is_available() is False
    assert adapter.translate(_PROMPT, _SYSTEM) is None


def test_anthropic_adapter_available_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY"))
    assert adapter.is_available() is True


def test_anthropic_adapter_explicit_model_skips_discovery(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    discovery_calls = []

    class FakeModels:
        def list(self):
            discovery_calls.append(True)
            return []

    class FakeMessages:
        def create(self, **kwargs):
            return _fake_anthropic_response('```sql\nSUM(sometable."SOMECOLUMN")\n```')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()
            self.models = FakeModels()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY", model="claude-haiku-4-5"))
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    assert result.text == 'SUM(sometable."SOMECOLUMN")'
    assert discovery_calls == []  # explicit model configured — discovery never runs


def test_anthropic_adapter_discovers_and_picks_cheapest_known_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    captured_models = []

    class FakeModels:
        def list(self):
            # Three real, differently-priced models — the adapter must
            # pick claude-haiku-4-5, the cheapest one in the catalog, not
            # the first one returned or the most capable.
            return [
                SimpleNamespace(id="claude-opus-5"),
                SimpleNamespace(id="claude-sonnet-4-6"),
                SimpleNamespace(id="claude-haiku-4-5"),
            ]

    class FakeMessages:
        def create(self, **kwargs):
            captured_models.append(kwargs.get("model"))
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()
            self.models = FakeModels()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY"))
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    assert captured_models == ["claude-haiku-4-5"]


def test_anthropic_adapter_caches_discovered_model_across_calls(monkeypatch):
    """Regression: discovery must run once per adapter instance, not once
    per translate() call — Tier5Service reuses one adapter instance for
    every metric in a run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    discovery_calls = []

    class FakeModels:
        def list(self):
            discovery_calls.append(True)
            return [SimpleNamespace(id="claude-haiku-4-5")]

    class FakeMessages:
        def create(self, **kwargs):
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()
            self.models = FakeModels()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY"))
    adapter.translate(_PROMPT, _SYSTEM)
    adapter.translate(_PROMPT, _SYSTEM)

    assert len(discovery_calls) == 1


def test_anthropic_adapter_falls_back_to_default_model_when_no_cataloged_model_is_available(monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    captured_models = []

    class FakeModels:
        def list(self):
            return [SimpleNamespace(id="claude-mystery-model")]  # not in the catalog

    class FakeMessages:
        def create(self, **kwargs):
            captured_models.append(kwargs.get("model"))
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()
            self.models = FakeModels()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY"))
    with caplog.at_level("WARNING"):
        adapter.translate(_PROMPT, _SYSTEM)

    assert captured_models == ["claude-haiku-4-5"]  # _FALLBACK_DEFAULT_MODEL
    # the staleness signal must be a visible WARNING-level log, not a
    # comment nobody will ever read at runtime
    assert any("claude-mystery-model" in record.message for record in caplog.records)


def test_anthropic_adapter_warns_on_partial_catalog_miss_even_though_a_known_model_was_picked(monkeypatch, caplog):
    """The staleness warning must fire even when a cataloged model WAS
    available and selected — an unrecognized model alongside it can still
    mean the catalog is missing a newer/cheaper option, and that must not
    go silent just because translation succeeded."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    captured_models = []

    class FakeModels:
        def list(self):
            return [
                SimpleNamespace(id="claude-haiku-4-5"),       # known
                SimpleNamespace(id="claude-haiku-6-preview"),  # not in the catalog yet
            ]

    class FakeMessages:
        def create(self, **kwargs):
            captured_models.append(kwargs.get("model"))
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()
            self.models = FakeModels()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY"))
    with caplog.at_level("WARNING"):
        adapter.translate(_PROMPT, _SYSTEM)

    assert captured_models == ["claude-haiku-4-5"]  # still picks the known cheap model
    assert any("claude-haiku-6-preview" in record.message for record in caplog.records)


def test_anthropic_adapter_falls_back_to_default_model_when_discovery_call_itself_fails(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    captured_models = []

    class FakeModels:
        def list(self):
            raise RuntimeError("connection reset by peer")

    class FakeMessages:
        def create(self, **kwargs):
            captured_models.append(kwargs.get("model"))
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()
            self.models = FakeModels()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY"))
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    assert captured_models == ["claude-haiku-4-5"]


def test_select_cheapest_known_model_prefers_input_price_over_generation():
    assert select_cheapest_known_model(["claude-opus-5", "claude-haiku-4-5"]) == "claude-haiku-4-5"


def test_select_cheapest_known_model_returns_none_for_unknown_ids():
    assert select_cheapest_known_model(["totally-unknown-model"]) is None


def test_anthropic_adapter_disables_thinking_for_models_that_think_by_default(monkeypatch):
    """claude-opus-5 and claude-sonnet-5 run adaptive thinking when the
    `thinking` param is omitted — if left on, reasoning tokens could
    consume the whole max_tokens budget before any answer text is
    produced. This adapter must explicitly disable it for those models."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    captured_kwargs = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY", model="claude-opus-5"))
    adapter.translate(_PROMPT, _SYSTEM)

    assert captured_kwargs.get("thinking") == {"type": "disabled"}


def test_anthropic_adapter_omits_thinking_param_for_models_without_it(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    captured_kwargs = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY", model="claude-haiku-4-5"))
    adapter.translate(_PROMPT, _SYSTEM)

    assert "thinking" not in captured_kwargs


def test_anthropic_adapter_never_sends_temperature_or_top_p(monkeypatch):
    """Deliberate: several current-generation Anthropic models 400 on a
    non-default temperature/top_p, and which model is in play is decided
    dynamically by discovery — so this adapter must never send either."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    captured_kwargs = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY", model="claude-haiku-4-5"))
    adapter.translate(_PROMPT, _SYSTEM)

    assert "temperature" not in captured_kwargs
    assert "top_p" not in captured_kwargs


def test_anthropic_adapter_treats_refusal_stop_reason_as_no_answer(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(content=[], stop_reason="refusal")

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(
        ProviderSettings(enabled_env="ANTHROPIC_API_KEY", model="claude-haiku-4-5", max_retries=1)
    )
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is None


def test_anthropic_adapter_extracts_text_block_after_a_thinking_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="thinking", thinking="reasoning about the DAX..."),
                    SimpleNamespace(type="text", text='SUM(sometable."SOMECOLUMN")'),
                ],
                stop_reason="end_turn",
            )

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY", model="claude-opus-5"))
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    assert result.text == 'SUM(sometable."SOMECOLUMN")'


def test_anthropic_adapter_concatenates_multiple_text_blocks_instead_of_truncating(monkeypatch):
    """Direct regression for the shared-response-shape risk: if Claude
    ever splits one answer across more than one text-type content block,
    reading only content[0] would silently truncate a real (possibly
    long/complex) translation. _extract_text must join every text block,
    not just the first."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text='SUM(sometable."SOMECOLUMN") + '),
                    SimpleNamespace(type="text", text='SUM(sometable."OTHERCOLUMN")'),
                ],
                stop_reason="end_turn",
            )

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    adapter = AnthropicAdapter(ProviderSettings(enabled_env="ANTHROPIC_API_KEY", model="claude-haiku-4-5"))
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    # both blocks present, in order — nothing dropped by only reading content[0]
    assert result.text == 'SUM(sometable."SOMECOLUMN") + SUM(sometable."OTHERCOLUMN")'


def _anthropic_service_config():
    return Tier5Config(
        provider_order=["anthropic"],
        min_confidence=0.55,
        providers={"anthropic": ProviderSettings(enabled_env="ANTHROPIC_API_KEY", model="claude-haiku-4-5")},
    )


def _anthropic_service_request():
    return TranslationRequest(
        dax="SUM('SomeTable'[SomeColumn])",
        dataset_name="SomeTable",
        table_alias="sometable",
        dataset_col_lookup={"SomeTable": {"SOMECOLUMN"}},
        dataset_aliases={"SomeTable": "sometable"},
        metric_name="Metric_A",
    )


def test_anthropic_adapter_output_flows_through_the_shared_validation_pipeline_unmodified(monkeypatch):
    """No special-casing anywhere: Anthropic's raw response goes through
    the exact same fix_common_llm_issues / _validate_metric_column_references
    functions every other Tier 5 provider's output does, via the real
    Tier5Service (not a fake adapter) — proving there's no Anthropic-
    specific branch in tier5/service.py."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    class FakeMessages:
        def create(self, **kwargs):
            return _fake_anthropic_response('SUM(sometable."SOMECOLUMN")')

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    result = Tier5Service(_anthropic_service_config()).translate(_anthropic_service_request())

    assert result is not None
    assert result.provider == "anthropic"
    # normalized by the shared validator, same as every other provider (see
    # test_service.py's test_accepts_first_valid_candidate)
    assert result.sql == "SUM(sometable.SOMECOLUMN)"


def test_anthropic_adapter_null_cast_placeholder_is_rejected_by_the_shared_null_sentinel_guard(monkeypatch):
    """Same null-sentinel guard (semabridge.utils.null_sentinel.is_null_cast_sql,
    applied inside tier5/service.py) rejects Anthropic's CAST(NULL AS ...)
    placeholder exactly like it does for every other provider."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    class FakeMessages:
        def create(self, **kwargs):
            return _fake_anthropic_response("CAST(NULL AS DOUBLE)")

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)

    result = Tier5Service(_anthropic_service_config()).translate(_anthropic_service_request())

    assert result is None


# --- Settings-page precedence (ProviderSettings.api_key over .env) --------

def test_provider_settings_is_enabled_true_from_api_key_alone(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ProviderSettings(enabled_env="OPENAI_API_KEY", api_key="settings-configured-key")
    assert settings.is_enabled() is True


def test_provider_settings_is_enabled_false_with_neither_settings_nor_env_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ProviderSettings(enabled_env="OPENAI_API_KEY")
    assert settings.is_enabled() is False


def test_provider_settings_repr_never_exposes_the_api_key():
    """A raw dataclass repr would otherwise print the decrypted key into
    any log line or traceback that stringifies this object -- field(repr=False)
    must keep it out."""
    settings = ProviderSettings(enabled_env="OPENAI_API_KEY", api_key="sk-super-secret-value")
    assert "sk-super-secret-value" not in repr(settings)


def test_openai_adapter_prefers_settings_api_key_over_env_value(monkeypatch):
    """Precedence proof: both a Settings key and a different .env value
    are present -- the Settings key must be the one actually used."""
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-should-not-be-used")
    captured_keys = []

    fake_message = SimpleNamespace(content="SUM(sometable.\"SOMECOLUMN\")")
    fake_choice = SimpleNamespace(message=fake_message)
    fake_response = SimpleNamespace(choices=[fake_choice])

    class FakeClient:
        def __init__(self, api_key=None, **k):
            captured_keys.append(api_key)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kw: fake_response)
            )

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    adapter = OpenAIAdapter(
        ProviderSettings(enabled_env="OPENAI_API_KEY", api_key="settings-key", max_retries=1)
    )
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    assert captured_keys == ["settings-key"]  # not "env-key-should-not-be-used"


def test_openai_adapter_falls_back_to_env_when_no_settings_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    captured_keys = []

    fake_message = SimpleNamespace(content="SUM(sometable.\"SOMECOLUMN\")")
    fake_choice = SimpleNamespace(message=fake_message)
    fake_response = SimpleNamespace(choices=[fake_choice])

    class FakeClient:
        def __init__(self, api_key=None, **k):
            captured_keys.append(api_key)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kw: fake_response)
            )

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    adapter = OpenAIAdapter(ProviderSettings(enabled_env="OPENAI_API_KEY", max_retries=1))
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    assert captured_keys == ["env-key"]


def test_gemini_adapter_prefers_settings_api_key_and_bypasses_the_shared_service(monkeypatch):
    """A Settings-configured key must bypass get_gemini_service() entirely
    (see gemini_adapter.py's translate() docstring for why: that
    singleton reads GEMINI_API_KEY at its own construction time and has
    no way to take a per-adapter key)."""
    monkeypatch.setenv("GEMINI_API_KEY", "env-key-should-not-be-used")

    shared_service_called = []
    monkeypatch.setattr(
        "semabridge.converter.gemini_api_service.get_gemini_service",
        lambda: shared_service_called.append(True),
    )

    captured_keys = []

    class FakeModel:
        def __init__(self, model_name):
            self.model_name = model_name

        def generate_content(self, prompt, stream=False):
            return SimpleNamespace(text='SUM(sometable."SOMECOLUMN")')

    def fake_configure(api_key=None):
        captured_keys.append(api_key)

    monkeypatch.setattr("google.generativeai.configure", fake_configure)
    monkeypatch.setattr("google.generativeai.GenerativeModel", FakeModel)

    adapter = GeminiAdapter(ProviderSettings(enabled_env="GEMINI_API_KEY", api_key="settings-key"))
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    assert result.text == 'SUM(sometable."SOMECOLUMN")'
    assert captured_keys == ["settings-key"]
    assert shared_service_called == []  # the rate-limited singleton was never touched


def test_featherless_adapter_prefers_settings_selected_model_over_failover_list(monkeypatch):
    """A Settings-selected model (singular) must be used exactly, with no
    failover list -- the hardcoded multi-model failover chain is only for
    the historical .env-only, no-model-chosen case."""
    monkeypatch.setenv("FEATHERLESS_API_KEY", "fake-key")
    attempted_models = []

    class FakeChatOpenAI:
        def __init__(self, *a, model=None, **k):
            attempted_models.append(model)

        def invoke(self, messages):
            return SimpleNamespace(content='SUM(sometable."SOMECOLUMN")')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    adapter = FeatherlessAdapter(
        ProviderSettings(
            enabled_env="FEATHERLESS_API_KEY",
            model="my-chosen/Model-7B",
            models=["deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen3.6-27B"],
        )
    )
    result = adapter.translate(_PROMPT, _SYSTEM)

    assert result is not None
    assert attempted_models == ["my-chosen/Model-7B"]  # not the failover list


def test_enabled_provider_order_includes_provider_configured_only_via_settings(monkeypatch):
    """The precedence rule end to end at the config layer: a provider with
    an api_key set (Settings) but no env var at all must still show up in
    enabled_provider_order() -- "configured somewhere" includes Settings,
    not just .env."""
    config = Tier5Config.default()
    for settings in config.providers.values():
        monkeypatch.delenv(settings.enabled_env, raising=False)

    config.providers["openai"].api_key = "settings-key"

    assert config.enabled_provider_order() == ["openai"]


def test_enabled_provider_order_excludes_provider_with_no_key_anywhere(monkeypatch):
    """The other half: a provider with neither a Settings key nor an env
    var must never be attempted at all."""
    config = Tier5Config.default()
    for settings in config.providers.values():
        monkeypatch.delenv(settings.enabled_env, raising=False)

    assert config.enabled_provider_order() == []


# --- Real model discovery (list_available_models) -------------------------

def test_openai_list_available_models_returns_raw_catalog(monkeypatch):
    from semabridge.dax_translation.tier5.adapters.openai_adapter import list_available_models

    class FakeModels:
        def list(self):
            return [SimpleNamespace(id="gpt-4o-mini"), SimpleNamespace(id="text-embedding-3-small")]

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    result = list_available_models("fake-key")

    # No filtering — OpenAI's API has no capability flag to filter on honestly.
    assert result.models == ["gpt-4o-mini", "text-embedding-3-small"]
    assert result.truncated is False
    assert result.total_available == 2


def test_gemini_list_available_models_filters_to_generate_content_capable(monkeypatch):
    from semabridge.dax_translation.tier5.adapters.gemini_adapter import list_available_models

    fake_models = [
        SimpleNamespace(name="models/gemini-1.5-flash", supported_generation_methods=["generateContent"]),
        SimpleNamespace(name="models/embedding-001", supported_generation_methods=["embedContent"]),
    ]
    monkeypatch.setattr("google.generativeai.configure", lambda api_key=None: None)
    monkeypatch.setattr("google.generativeai.list_models", lambda: fake_models)

    result = list_available_models("fake-key")

    assert result.models == ["gemini-1.5-flash"]  # embedding-001 filtered out
    assert result.total_available == 1


def test_groq_list_available_models_filters_inactive_models(monkeypatch):
    from semabridge.dax_translation.tier5.adapters.groq_adapter import list_available_models

    fake_data = [
        SimpleNamespace(id="llama-3.3-70b-versatile", active=True),
        SimpleNamespace(id="decommissioned-model", active=False),
    ]

    class FakeModels:
        def list(self):
            return SimpleNamespace(data=fake_data)

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr("groq.Groq", FakeClient)

    result = list_available_models("fake-key")

    assert result.models == ["llama-3.3-70b-versatile"]
    assert result.total_available == 1


def test_featherless_list_available_models_excludes_non_chat_markers(monkeypatch):
    from semabridge.dax_translation.tier5.adapters.featherless_adapter import list_available_models

    fake_ids = [
        "meta-llama/Llama-3.2-3B-Instruct",
        "BAAI/bge-large-en-embedding",
        "openai/whisper-large-v3",
        "stabilityai/stable-diffusion-xl",
        "mistralai/Mistral-7B-Instruct-v0.3",
    ]

    class FakeModels:
        def list(self):
            return [SimpleNamespace(id=i) for i in fake_ids]

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    result = list_available_models("fake-key")

    assert set(result.models) == {
        "meta-llama/Llama-3.2-3B-Instruct",
        "mistralai/Mistral-7B-Instruct-v0.3",
    }
    assert result.truncated is False
    assert result.total_available == 2


def test_featherless_list_available_models_caps_at_200_and_reports_truncation(monkeypatch):
    from semabridge.dax_translation.tier5.adapters.featherless_adapter import (
        _MAX_DISCOVERED_MODELS,
        list_available_models,
    )

    fake_ids = [f"org/chat-model-{i:04d}" for i in range(250)]  # all pass the chat filter

    class FakeModels:
        def list(self):
            return [SimpleNamespace(id=i) for i in fake_ids]

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    result = list_available_models("fake-key")

    assert len(result.models) == _MAX_DISCOVERED_MODELS
    assert result.truncated is True
    assert result.total_available == 250
    assert result.models == sorted(fake_ids)[:_MAX_DISCOVERED_MODELS]


def test_featherless_list_available_models_no_truncation_when_under_cap(monkeypatch):
    from semabridge.dax_translation.tier5.adapters.featherless_adapter import list_available_models

    fake_ids = [f"org/chat-model-{i}" for i in range(5)]

    class FakeModels:
        def list(self):
            return [SimpleNamespace(id=i) for i in fake_ids]

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    result = list_available_models("fake-key")

    assert result.truncated is False
    assert result.total_available == 5
    assert len(result.models) == 5
