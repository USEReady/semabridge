from __future__ import annotations

import pytest
import time

from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.core.settings import FabricConfig


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err


def test_fabric_extractor_respects_retry_after_header(monkeypatch):
    """If the Fabric call returns 429 with a Retry-After header, sleep for that amount and retry."""
    config = FabricConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        workspace_id="workspace",
    )
    extractor = FabricExtractor(config, access_token="token")

    calls = []
    sleeps = []

    def _fake_request(method: str, url: str, **kwargs):
        del kwargs
        calls.append(url)
        if len(calls) == 1:
            return _FakeResponse(429, {"error": "Rate limited"}, {"Retry-After": "15"})
        return _FakeResponse(200, {"value": [{"id": "model-1", "displayName": "My Model"}]})

    monkeypatch.setattr(extractor, "resolve_workspace_id", lambda w: w)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", _fake_request)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.time.sleep", sleeps.append)

    models = extractor.list_semantic_models()

    assert len(models) == 1
    assert models[0]["id"] == "model-1"
    assert len(calls) == 2
    assert sleeps == [15.0]


def test_fabric_extractor_exponential_backoff_on_429_without_header(monkeypatch):
    """If 429 returns without a Retry-After header, it falls back to exponential backoff."""
    config = FabricConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        workspace_id="workspace",
    )
    extractor = FabricExtractor(config, access_token="token")

    calls = []
    sleeps = []

    def _fake_request(method: str, url: str, **kwargs):
        del kwargs
        calls.append(url)
        if len(calls) < 3:
            return _FakeResponse(429, {"error": "Rate limited"})
        return _FakeResponse(200, {"value": [{"id": "model-1", "displayName": "My Model"}]})

    monkeypatch.setattr(extractor, "resolve_workspace_id", lambda w: w)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", _fake_request)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.time.sleep", sleeps.append)

    # list_semantic_models internally uses transient_retries=2, retry_delay=2.0
    models = extractor.list_semantic_models()

    assert len(models) == 1
    assert models[0]["id"] == "model-1"
    assert len(calls) == 3
    # Attempt 1: retry_delay * (2 ** 0) = 2.0 * 1 = 2.0
    # Attempt 2: retry_delay * (2 ** 1) = 2.0 * 2 = 4.0
    assert sleeps == [2.0, 4.0]


def test_fabric_extractor_exponential_backoff_on_invalid_retry_after_header(monkeypatch):
    """If Retry-After is not a valid float/int, fall back to exponential backoff."""
    config = FabricConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        workspace_id="workspace",
    )
    extractor = FabricExtractor(config, access_token="token")

    calls = []
    sleeps = []

    def _fake_request(method: str, url: str, **kwargs):
        del kwargs
        calls.append(url)
        if len(calls) == 1:
            return _FakeResponse(429, {"error": "Rate limited"}, {"Retry-After": "invalid-date-format"})
        return _FakeResponse(200, {"value": [{"id": "model-1", "displayName": "My Model"}]})

    monkeypatch.setattr(extractor, "resolve_workspace_id", lambda w: w)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", _fake_request)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.time.sleep", sleeps.append)

    models = extractor.list_semantic_models()

    assert len(models) == 1
    assert models[0]["id"] == "model-1"
    assert len(calls) == 2
    assert sleeps == [2.0]
