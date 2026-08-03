"""Regression tests for auth-vs-transient error classification and the
per-run "unavailable provider" behavior it drives.

Covers the live-deploy bug: an invalid/expired provider key (confirmed
with Groq) was retried fresh for every metric/batch instead of being
detected once and skipped for the rest of the run. See
Tests/dax_translation/tier5/test_service.py for the Tier5Service-level
cache tests; this file covers the adapter-level classification and the
Groq/OpenAI/Featherless internal-fallback short-circuit behavior.
"""
import sys
from types import SimpleNamespace

from semabridge.dax_translation.tier5.adapters.base import ProviderAuthError, is_auth_error
from semabridge.dax_translation.tier5.adapters.groq_adapter import GroqAdapter
from semabridge.dax_translation.tier5.adapters.openai_adapter import OpenAIAdapter
from semabridge.dax_translation.tier5.adapters.featherless_adapter import FeatherlessAdapter
from semabridge.dax_translation.tier5.config import ProviderSettings


class _MockAuthError(Exception):
    """Stands in for openai.AuthenticationError / groq.AuthenticationError
    — both expose a `status_code` attribute alongside the message."""

    def __init__(self, message="Incorrect API key provided"):
        super().__init__(message)
        self.status_code = 401


def test_is_auth_error_detects_status_code_401():
    assert is_auth_error(_MockAuthError()) is True


def test_is_auth_error_detects_authentication_error_type_name():
    class AuthenticationError(Exception):
        pass

    assert is_auth_error(AuthenticationError("bad credentials")) is True


def test_is_auth_error_detects_message_markers():
    assert is_auth_error(RuntimeError("400 API key not valid. Please pass a valid API key.")) is True
    assert is_auth_error(RuntimeError("expired_api_key: your key has expired")) is True


def test_is_auth_error_does_not_flag_rate_limit_or_timeout():
    assert is_auth_error(RuntimeError("429 rate limit exceeded, please slow down")) is False
    assert is_auth_error(TimeoutError("request timed out after 30s")) is False


def test_is_auth_error_does_not_flag_generic_failure():
    assert is_auth_error(RuntimeError("connection reset by peer")) is False


def test_groq_adapter_auth_failure_on_chatgroq_skips_direct_client_variant(monkeypatch):
    """The 5th investigation point: when ChatGroq fails with a clear auth
    error, the direct-client variant would fail identically (same
    api_key) — it must not be attempted."""
    monkeypatch.setenv("GROQ_API_KEY", "sk-invalid")

    class MockChatGroq:
        def __init__(self, *args, **kwargs):
            pass

        def invoke(self, *_args, **_kwargs):
            raise _MockAuthError("invalid_api_key")

    direct_client_called = []

    class MockOpenAI:
        def __init__(self, *args, **kwargs):
            direct_client_called.append(True)

    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=MockChatGroq))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=MockOpenAI))

    adapter = GroqAdapter(ProviderSettings(enabled_env="GROQ_API_KEY"))

    try:
        adapter.translate("prompt", "system")
        assert False, "expected ProviderAuthError"
    except ProviderAuthError as exc:
        assert exc.provider_name == "groq"

    assert direct_client_called == []  # never constructed — no wasted round trip


def test_groq_adapter_non_auth_chatgroq_failure_still_tries_direct_client(monkeypatch):
    """A genuinely different (non-auth) ChatGroq failure must not
    suppress the direct-client fallback — that's a real, separate code
    path worth trying once per call, not a guaranteed-repeat failure."""
    monkeypatch.setenv("GROQ_API_KEY", "sk-valid")

    class MockChatGroq:
        def __init__(self, *args, **kwargs):
            pass

        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("connection reset by peer")

    mock_choices = [SimpleNamespace(message=SimpleNamespace(content="SUM(sometable.SOMECOLUMN)"))]
    mock_response = SimpleNamespace(choices=mock_choices)

    class MockOpenAI:
        def __init__(self, *args, **kwargs):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(*args, **kwargs):
                    return mock_response

    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=MockChatGroq))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=MockOpenAI))

    adapter = GroqAdapter(ProviderSettings(enabled_env="GROQ_API_KEY"))
    result = adapter.translate("prompt", "system")

    assert result is not None
    assert result.text == "SUM(sometable.SOMECOLUMN)"


def test_openai_adapter_auth_failure_skips_remaining_retries(monkeypatch):
    """A bad key fails identically on every retry attempt — the adapter
    must raise ProviderAuthError on the first failure rather than
    burning max_retries worth of round trips (and backoff sleeps) on a
    guaranteed-repeat failure."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-invalid")

    call_count = []

    class MockOpenAI:
        def __init__(self, *args, **kwargs):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(*args, **kwargs):
                    call_count.append(True)
                    raise _MockAuthError("Incorrect API key provided")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=MockOpenAI))

    adapter = OpenAIAdapter(ProviderSettings(enabled_env="OPENAI_API_KEY", max_retries=3))

    try:
        adapter.translate("prompt", "system")
        assert False, "expected ProviderAuthError"
    except ProviderAuthError:
        pass

    assert len(call_count) == 1  # no retries burned on a guaranteed-repeat failure


def test_featherless_adapter_auth_failure_skips_remaining_models(monkeypatch):
    """Same api_key is used for every model in the failover list — an
    auth failure on the first model must not be retried against the
    other four."""
    monkeypatch.setenv("FEATHERLESS_API_KEY", "sk-invalid")

    attempted_models = []

    class MockChatOpenAI:
        def __init__(self, *args, model=None, **kwargs):
            attempted_models.append(model)

        def invoke(self, *_args, **_kwargs):
            raise _MockAuthError("invalid_api_key")

    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=MockChatOpenAI))

    adapter = FeatherlessAdapter(ProviderSettings(
        enabled_env="FEATHERLESS_API_KEY",
        models=["model-a", "model-b", "model-c"],
    ))

    try:
        adapter.translate("prompt", "system")
        assert False, "expected ProviderAuthError"
    except ProviderAuthError:
        pass

    assert attempted_models == ["model-a"]  # stopped after the first auth failure
