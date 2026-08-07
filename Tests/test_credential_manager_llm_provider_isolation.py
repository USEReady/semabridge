"""Regression tests: Tier 5 LLM provider credentials (the "llm_*" service
entries added to CredentialManager._ENV_MAP for
repository/llm_provider_credentials.py) must never leak into
CredentialManager's os.environ-mutating or fabric/snowflake/databricks
connection-status methods, both of which iterate _ENV_MAP.keys()
unconditionally.

Caught by running the broader (non-tier5-scoped) test suite during this
feature's development: Tests/test_account_create.py's FastAPI app startup
calls CredentialManager().inject_all(), which -- before this fix -- would
push the Fernet-ciphertext stored for an llm_* service into os.environ,
silently corrupting the real provider API key (e.g. GEMINI_API_KEY) on
the next app start after an admin saves one via Settings.

Uses CredentialManager's own url_override parameter (an isolated
in-memory SQLite database, explicitly documented as "for tests") rather
than mocking -- these tests exercise the real _ENV_MAP dict and the real
save/inject/status method bodies.
"""
from __future__ import annotations

import os

from semabridge.repository.credential_manager import CredentialManager


def _isolated_manager() -> CredentialManager:
    return CredentialManager(url_override="sqlite:///:memory:")


def test_inject_all_never_touches_llm_provider_services(monkeypatch):
    """The actual regression: a Settings-configured (ciphertext-shaped)
    llm_* credential must never be written into os.environ by
    inject_all()."""
    monkeypatch.setenv("OPENAI_API_KEY", "the-real-untouched-value")
    manager = _isolated_manager()
    manager.save_credentials("llm_openai", {"api_key": "not-a-real-key-ciphertext-stand-in"})

    results = manager.inject_all()

    assert os.environ["OPENAI_API_KEY"] == "the-real-untouched-value"
    assert "llm_openai" not in results


def test_inject_all_still_injects_the_original_three_services(monkeypatch):
    """The fix must not regress the pre-existing behavior for
    fabric/snowflake/databricks -- only llm_* is newly excluded. (Not
    asserting an exact injected-count here: this test environment's real
    .env may already have other SNOWFLAKE_* vars set, which
    get_credentials()'s env-fallback step legitimately pulls in too --
    the property under test is that snowflake keeps working at all, and
    that our specific saved value lands correctly.)"""
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    manager = _isolated_manager()
    manager.save_credentials("snowflake", {"account": "myaccount"})

    results = manager.inject_all()

    assert os.environ["SNOWFLAKE_ACCOUNT"] == "myaccount"
    assert results.get("snowflake", 0) >= 1
    assert "fabric" in results and "databricks" in results  # every non-llm_ service still processed


def test_get_connection_status_excludes_llm_provider_services():
    """get_connection_status() feeds directly into an existing HTTP
    response (GET connections status) that callers/frontend expect to
    contain exactly {fabric, snowflake, databricks} -- llm_* providers
    have their own dedicated status endpoint and must not appear here."""
    manager = _isolated_manager()
    manager.save_credentials("llm_gemini", {"api_key": "ciphertext-stand-in"})

    status = manager.get_connection_status()

    assert set(status.keys()) == {"fabric", "snowflake", "databricks"}
    assert "llm_gemini" not in status
