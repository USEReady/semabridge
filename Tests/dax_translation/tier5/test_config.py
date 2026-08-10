"""Unit tests for semabridge.dax_translation.tier5.config."""
import os

import pytest

from semabridge.dax_translation.tier5.config import Tier5Config, ProviderSettings


def test_default_config_matches_documented_shape():
    config = Tier5Config.default()
    assert config.provider_order == ["openai", "gemini", "groq", "featherless", "anthropic"]
    assert config.min_confidence == 0.55
    assert set(config.providers.keys()) == {"openai", "gemini", "groq", "featherless", "anthropic"}

    openai = config.providers["openai"]
    assert openai.enabled_env == "OPENAI_API_KEY"
    assert openai.model == "gpt-4o-mini"
    assert openai.timeout_seconds == 30
    assert openai.max_retries == 2

    featherless = config.providers["featherless"]
    assert featherless.models == [
        "deepseek-ai/DeepSeek-V4-Pro",
        "Qwen/Qwen3.6-27B",
        "mistralai/Mistral-7B-Instruct-v0.3",
        "meta-llama/Llama-3.2-3B-Instruct",
    ]

    anthropic = config.providers["anthropic"]
    assert anthropic.enabled_env == "ANTHROPIC_API_KEY"
    assert anthropic.model is None  # resolved via live discovery, not a hardcoded default (see adapters/anthropic_adapter.py)
    assert anthropic.timeout_seconds == 30
    assert anthropic.max_retries == 2


def test_provider_settings_is_enabled_reflects_env_var(monkeypatch):
    settings = ProviderSettings(enabled_env="SOME_FAKE_TEST_API_KEY")
    monkeypatch.delenv("SOME_FAKE_TEST_API_KEY", raising=False)
    assert settings.is_enabled() is False

    monkeypatch.setenv("SOME_FAKE_TEST_API_KEY", "fake-value")
    assert settings.is_enabled() is True


def test_enabled_provider_order_filters_to_configured_env_vars(monkeypatch):
    config = Tier5Config.default()
    for name, settings in config.providers.items():
        monkeypatch.delenv(settings.enabled_env, raising=False)

    assert config.enabled_provider_order() == []

    monkeypatch.setenv("GROQ_API_KEY", "fake-value")
    assert config.enabled_provider_order() == ["groq"]

    monkeypatch.setenv("OPENAI_API_KEY", "fake-value")
    # order preserved from provider_order, not insertion order
    assert config.enabled_provider_order() == ["openai", "groq"]


def test_enabled_provider_order_prioritizes_settings_configured_over_env_only(monkeypatch):
    """The Part 2 fix: a provider configured via Settings (api_key set on
    its ProviderSettings, mirroring what apply_settings_overrides() does)
    must be tried before a provider that's only enabled via .env — even
    though the .env-only provider (openai) sits earlier in the fixed
    provider_order than the Settings-configured one (anthropic)."""
    config = Tier5Config.default()
    for name, settings in config.providers.items():
        monkeypatch.delenv(settings.enabled_env, raising=False)

    # openai: .env-only, earlier in provider_order.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stale-dotenv-placeholder")
    # anthropic: Settings-configured (api_key set directly, as
    # apply_settings_overrides() would do after a real Settings save),
    # later in provider_order.
    config.providers["anthropic"].api_key = "sk-ant-from-settings"

    assert config.provider_order.index("openai") < config.provider_order.index("anthropic"), (
        "test setup assumption: openai must be earlier than anthropic in the fixed provider_order"
    )
    assert config.enabled_provider_order() == ["anthropic", "openai"], (
        "Settings-configured anthropic must be tried before .env-only openai, "
        "despite openai being earlier in provider_order"
    )


def test_enabled_provider_order_breaks_ties_within_same_source_using_provider_order(monkeypatch):
    """provider_order still matters — but only as a tie-breaker within a
    single source tier, not across tiers."""
    config = Tier5Config.default()
    for name, settings in config.providers.items():
        monkeypatch.delenv(settings.enabled_env, raising=False)

    # Two .env-only providers: order between them must still follow
    # provider_order (groq before featherless).
    monkeypatch.setenv("FEATHERLESS_API_KEY", "fake-value")
    monkeypatch.setenv("GROQ_API_KEY", "fake-value")
    # Two Settings-configured providers: order between them must also
    # still follow provider_order (gemini before anthropic).
    config.providers["anthropic"].api_key = "sk-ant-from-settings"
    config.providers["gemini"].api_key = "gemini-key-from-settings"

    assert config.enabled_provider_order() == ["gemini", "anthropic", "groq", "featherless"]


def test_enabled_provider_order_settings_key_alone_is_enough_without_env_var(monkeypatch):
    """A Settings-configured provider must be tried even when it has no
    .env fallback at all (the common case: a user saves a provider via
    Settings that was never in .env in the first place)."""
    config = Tier5Config.default()
    for name, settings in config.providers.items():
        monkeypatch.delenv(settings.enabled_env, raising=False)

    config.providers["anthropic"].api_key = "sk-ant-from-settings"

    assert config.enabled_provider_order() == ["anthropic"]


def test_from_dict_builds_config_from_documented_yaml_shape():
    data = {
        "dax_translation": {
            "tier5": {
                "provider_order": ["openai", "gemini"],
                "min_confidence": 0.6,
                "providers": {
                    "openai": {"enabled_env": "OPENAI_API_KEY", "model": "gpt-4o-mini"},
                    "gemini": {"enabled_env": "GEMINI_API_KEY", "model": "gemini-1.5-flash"},
                },
            },
            "skills_dir_env": "SEMABRIDGE_LLM_SKILLS_DIR",
        }
    }
    config = Tier5Config.from_dict(data)
    assert config.provider_order == ["openai", "gemini"]
    assert config.min_confidence == 0.6
    assert config.providers["openai"].model == "gpt-4o-mini"


def test_load_falls_back_to_default_when_path_missing():
    config = Tier5Config.load(path="/nonexistent/path/dax_translation.yaml")
    assert config.provider_order == Tier5Config.default().provider_order


def test_load_without_path_returns_default():
    config = Tier5Config.load()
    assert config.provider_order == Tier5Config.default().provider_order
