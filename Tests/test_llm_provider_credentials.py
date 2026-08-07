"""Unit tests for repository/llm_provider_credentials.py -- the
Settings-page storage layer for Tier 5 LLM provider API keys/models.

Uses a fake, in-memory CredentialManager (no real database) to prove:
 - the API key is genuinely encrypted before being handed to
   CredentialManager.save_credentials() (not just masked, unlike the
   plaintext-at-rest convention CredentialManager itself uses for its
   fabric/snowflake/databricks rows) -- and decrypts back to the original
 - the Settings-then-.env precedence (get_effective_api_key,
   apply_settings_overrides, get_all_provider_status) behaves correctly
"""
from __future__ import annotations

from semabridge.auth.encryption import decrypt_token
from semabridge.dax_translation.tier5.config import Tier5Config
from semabridge.repository import llm_provider_credentials as creds


class _FakeCredentialManager:
    """In-memory stand-in for CredentialManager, keyed by (service, user_id)
    -- mirrors just enough of the real signature for this module's calls.
    Class-level store so every instance shares state, like the real
    CredentialManager shares one database."""

    _store: dict = {}

    def __init__(self, *a, **k):
        pass

    def save_credentials(self, service, credentials, user_id=0):
        bucket = self._store.setdefault((service, user_id), {})
        bucket.update({k: str(v) for k, v in credentials.items()})
        return len(credentials)

    def get_credentials(self, service, mask_secrets=True, user_id=0, include_env_fallback=True):
        return dict(self._store.get((service, user_id), {}))

    def delete_credentials(self, service, user_id=0):
        return len(self._store.pop((service, user_id), {}))

    @classmethod
    def reset(cls):
        cls._store = {}


def _patch_manager(monkeypatch):
    _FakeCredentialManager.reset()
    monkeypatch.setattr(
        "semabridge.repository.llm_provider_credentials.CredentialManager",
        _FakeCredentialManager,
    )


def _delenv_all_providers(monkeypatch, config: Tier5Config) -> None:
    for settings in config.providers.values():
        monkeypatch.delenv(settings.enabled_env, raising=False)


def test_save_api_key_encrypts_before_storing(monkeypatch):
    """The stored value must be Fernet ciphertext, not the raw key --
    proving this module (not CredentialManager itself) applies the
    encryption, at the boundary."""
    _patch_manager(monkeypatch)

    creds.save_api_key("openai", "sk-my-real-secret-key")

    stored = _FakeCredentialManager._store[("llm_openai", 0)]
    assert stored["api_key"] != "sk-my-real-secret-key"
    assert decrypt_token(stored["api_key"]) == "sk-my-real-secret-key"


def test_get_decrypted_api_key_round_trips(monkeypatch):
    _patch_manager(monkeypatch)

    creds.save_api_key("anthropic", "sk-ant-abc123")

    assert creds.get_decrypted_api_key("anthropic") == "sk-ant-abc123"


def test_get_decrypted_api_key_returns_none_when_nothing_stored(monkeypatch):
    _patch_manager(monkeypatch)
    assert creds.get_decrypted_api_key("openai") is None


def test_save_and_get_model(monkeypatch):
    _patch_manager(monkeypatch)

    creds.save_model("groq", "llama-3.3-70b-versatile")

    assert creds.get_saved_model("groq") == "llama-3.3-70b-versatile"


def test_delete_api_key_removes_the_row(monkeypatch):
    _patch_manager(monkeypatch)
    creds.save_api_key("groq", "gsk-abc")
    creds.save_model("groq", "some-model")

    removed = creds.delete_api_key("groq")

    assert removed == 2  # both api_key and model rows removed
    assert creds.get_decrypted_api_key("groq") is None
    assert creds.get_saved_model("groq") is None


def test_get_effective_api_key_prefers_settings_over_env(monkeypatch):
    _patch_manager(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    creds.save_api_key("openai", "settings-key")

    assert creds.get_effective_api_key("openai", "OPENAI_API_KEY") == "settings-key"


def test_get_effective_api_key_falls_back_to_env_when_no_settings_key(monkeypatch):
    _patch_manager(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    assert creds.get_effective_api_key("openai", "OPENAI_API_KEY") == "env-key"


def test_get_effective_api_key_returns_none_when_configured_nowhere(monkeypatch):
    _patch_manager(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert creds.get_effective_api_key("openai", "OPENAI_API_KEY") is None


def test_apply_settings_overrides_populates_api_key_and_model(monkeypatch):
    _patch_manager(monkeypatch)
    creds.save_api_key("groq", "gsk-real-key")
    creds.save_model("groq", "llama-3.3-70b-versatile")

    config = Tier5Config.default()
    creds.apply_settings_overrides(config)

    assert config.providers["groq"].api_key == "gsk-real-key"
    assert config.providers["groq"].model == "llama-3.3-70b-versatile"


def test_apply_settings_overrides_leaves_unconfigured_providers_untouched(monkeypatch):
    """Only a provider with a Settings-stored key gets touched -- every
    other provider must keep its .env-only hardcoded default exactly as
    before (no Settings config = no behavior change)."""
    _patch_manager(monkeypatch)
    creds.save_api_key("groq", "gsk-real-key")

    config = Tier5Config.default()
    original_openai_model = config.providers["openai"].model
    creds.apply_settings_overrides(config)

    assert config.providers["openai"].api_key is None
    assert config.providers["openai"].model == original_openai_model


def test_get_all_provider_status_reports_settings_source(monkeypatch):
    _patch_manager(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds.save_api_key("openai", "sk-abcdef1234")

    config = Tier5Config.default()
    statuses = {s["provider"]: s for s in creds.get_all_provider_status(config)}

    assert statuses["openai"]["source"] == "settings"
    assert statuses["openai"]["configured"] is True
    assert statuses["openai"]["hint"] == "••••1234"


def test_get_all_provider_status_reports_env_source(monkeypatch):
    _patch_manager(monkeypatch)
    config = Tier5Config.default()
    _delenv_all_providers(monkeypatch, config)
    monkeypatch.setenv("GROQ_API_KEY", "env-only-key")

    statuses = {s["provider"]: s for s in creds.get_all_provider_status(config)}

    assert statuses["groq"]["source"] == "env"
    assert statuses["groq"]["configured"] is True
    assert statuses["groq"]["hint"] is None  # only Settings-sourced keys get a hint


def test_get_all_provider_status_reports_none_source(monkeypatch):
    _patch_manager(monkeypatch)
    config = Tier5Config.default()
    _delenv_all_providers(monkeypatch, config)

    statuses = {s["provider"]: s for s in creds.get_all_provider_status(config)}

    assert statuses["anthropic"]["source"] == "none"
    assert statuses["anthropic"]["configured"] is False
    assert statuses["anthropic"]["hint"] is None
