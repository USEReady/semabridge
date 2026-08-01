"""Unit tests for semabridge.dax_translation.tier5.config."""
import os

import pytest

from semabridge.dax_translation.tier5.config import Tier5Config, ProviderSettings


def test_default_config_matches_documented_shape():
    config = Tier5Config.default()
    assert config.provider_order == ["openai", "gemini", "groq", "featherless"]
    assert config.min_confidence == 0.55
    assert set(config.providers.keys()) == {"openai", "gemini", "groq", "featherless"}
    assert "anthropic" not in config.providers  # deferred per Step 1 scope

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
