"""Targeted tests for api/llm_provider_router.py -- the HTTP layer over
repository/llm_provider_credentials.py and each adapter's
list_available_models().

Uses FastAPI's dependency_overrides to bypass real authentication (the
standard FastAPI testing pattern) and monkeypatches the storage/discovery
functions this router calls -- no real database, no real network calls.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from semabridge.api.deps import get_current_user
from semabridge.api.main import app
from semabridge.dax_translation.tier5.adapters.base import ModelDiscoveryResult, ProviderAuthError


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_active=True)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_get_llm_provider_config_returns_all_five_providers(monkeypatch, client):
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.get_all_provider_status",
        lambda config: [
            {"provider": "openai", "source": "settings", "configured": True, "model": "gpt-4o-mini", "hint": "••••abcd"},
            {"provider": "gemini", "source": "env", "configured": True, "model": None, "hint": None},
            {"provider": "groq", "source": "none", "configured": False, "model": None, "hint": None},
            {"provider": "featherless", "source": "none", "configured": False, "model": None, "hint": None},
            {"provider": "anthropic", "source": "none", "configured": False, "model": None, "hint": None},
        ],
    )

    response = client.get("/api/settings/llm-providers")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 5
    assert {p["provider"] for p in body} == {"openai", "gemini", "groq", "featherless", "anthropic"}
    openai_status = next(p for p in body if p["provider"] == "openai")
    assert openai_status["source"] == "settings"
    assert openai_status["hint"] == "••••abcd"


def test_save_api_key_calls_storage_layer_and_returns_204(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.save_api_key",
        lambda provider, key: captured.update(provider=provider, key=key),
    )

    response = client.post("/api/settings/llm-providers/openai/api-key", json={"api_key": "sk-real-key"})

    assert response.status_code == 204
    assert captured == {"provider": "openai", "key": "sk-real-key"}


def test_save_api_key_rejects_unknown_provider(client):
    response = client.post("/api/settings/llm-providers/totally-unknown/api-key", json={"api_key": "x"})

    assert response.status_code == 404
    assert "totally-unknown" in response.json()["detail"]


def test_save_api_key_rejects_empty_key(client):
    response = client.post("/api/settings/llm-providers/openai/api-key", json={"api_key": ""})

    assert response.status_code == 422  # Pydantic min_length=1 validation


def test_delete_api_key_calls_storage_layer(monkeypatch, client):
    captured = []
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.delete_api_key",
        lambda provider: captured.append(provider),
    )

    response = client.delete("/api/settings/llm-providers/groq/api-key")

    assert response.status_code == 204
    assert captured == ["groq"]


def test_save_model_calls_storage_layer(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.save_model",
        lambda provider, model: captured.update(provider=provider, model=model),
    )

    response = client.post("/api/settings/llm-providers/anthropic/model", json={"model": "claude-haiku-4-5"})

    assert response.status_code == 204
    assert captured == {"provider": "anthropic", "model": "claude-haiku-4-5"}


def test_discover_models_happy_path(monkeypatch, client):
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.get_effective_api_key",
        lambda provider, enabled_env: "resolved-key",
    )
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router._LIST_MODELS_FUNCS",
        {"openai": lambda api_key: ModelDiscoveryResult(models=["gpt-4o-mini", "gpt-4o"], total_available=2)},
    )

    response = client.post("/api/settings/llm-providers/openai/discover-models")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["models"] == ["gpt-4o-mini", "gpt-4o"]
    assert body["truncated"] is False
    assert body["total_available"] == 2


def test_discover_models_reports_truncation(monkeypatch, client):
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.get_effective_api_key",
        lambda provider, enabled_env: "resolved-key",
    )
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router._LIST_MODELS_FUNCS",
        {
            "featherless": lambda api_key: ModelDiscoveryResult(
                models=["m1", "m2"], truncated=True, total_available=250,
            ),
        },
    )

    response = client.post("/api/settings/llm-providers/featherless/discover-models")

    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert body["total_available"] == 250


def test_discover_models_returns_400_when_no_key_configured_anywhere(monkeypatch, client):
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.get_effective_api_key",
        lambda provider, enabled_env: None,
    )

    response = client.post("/api/settings/llm-providers/groq/discover-models")

    assert response.status_code == 400
    assert "No API key configured" in response.json()["detail"]


def test_discover_models_returns_401_on_auth_error_not_a_generic_500(monkeypatch, client):
    """Clear, honest UI feedback: a bad key must classify as 401, not a
    generic failure."""
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.get_effective_api_key",
        lambda provider, enabled_env: "bad-key",
    )

    def _raise_auth_error(api_key):
        raise ProviderAuthError("openai", RuntimeError("401 invalid_api_key"))

    monkeypatch.setattr(
        "semabridge.api.llm_provider_router._LIST_MODELS_FUNCS",
        {"openai": _raise_auth_error},
    )

    response = client.post("/api/settings/llm-providers/openai/discover-models")

    assert response.status_code == 401
    assert "rejected the configured API key" in response.json()["detail"]


def test_discover_models_returns_502_on_network_error_not_a_generic_500(monkeypatch, client):
    """Distinct from the auth-error case: a network/API failure must
    classify as 502, not 401 and not a generic 500."""
    monkeypatch.setattr(
        "semabridge.api.llm_provider_router.get_effective_api_key",
        lambda provider, enabled_env: "some-key",
    )

    def _raise_network_error(api_key):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(
        "semabridge.api.llm_provider_router._LIST_MODELS_FUNCS",
        {"openai": _raise_network_error},
    )

    response = client.post("/api/settings/llm-providers/openai/discover-models")

    assert response.status_code == 502
    assert "Could not reach" in response.json()["detail"]


def test_discover_models_rejects_unknown_provider(client):
    response = client.post("/api/settings/llm-providers/totally-unknown/discover-models")

    assert response.status_code == 404
