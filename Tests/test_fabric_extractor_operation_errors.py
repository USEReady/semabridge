from __future__ import annotations

import pytest

from semabridge.connectors.fabric_extractor import FabricExtractionError, FabricExtractor
from semabridge.core.settings import FabricConfig


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err


def _extractor() -> FabricExtractor:
    return FabricExtractor(
        FabricConfig(
            tenant_id="tenant",
            client_id="client",
            client_secret="secret",
            workspace_id="workspace",
        ),
        access_token="token",
    )


def test_poll_operation_surfaces_result_error_payload(monkeypatch):
    extractor = _extractor()
    calls: list[str] = []

    def fake_request(method: str, url: str, **kwargs):
        del method, kwargs
        calls.append(url)
        if url.endswith("/result"):
            return _FakeResponse(
                200,
                {
                    "status": "Failed",
                    "createdTimeUtc": "2026-05-23T00:00:00Z",
                    "lastUpdatedTimeUtc": "2026-05-23T00:00:01Z",
                    "percentComplete": 100,
                    "error": {
                        "code": "PowerBIAccessDenied",
                        "message": "Caller does not have permission to export this semantic model.",
                    },
                },
            )
        return _FakeResponse(200, {"status": "Succeeded"})

    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", fake_request)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.time.sleep", lambda _: None)

    with pytest.raises(FabricExtractionError) as exc:
        extractor._poll_operation("https://fabric.example/v1/operations/op-1", 1)

    assert "PowerBIAccessDenied" in str(exc.value)
    assert "permission" in str(exc.value)
    assert calls[-1].endswith("/result")


def test_poll_operation_failed_status_includes_error_code_and_message(monkeypatch):
    extractor = _extractor()

    def fake_request(method: str, url: str, **kwargs):
        del method, url, kwargs
        return _FakeResponse(
            200,
            {
                "status": "Failed",
                "error": {
                    "code": "InvalidRequest",
                    "message": "The requested semantic model definition is unavailable.",
                },
            },
        )

    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", fake_request)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.time.sleep", lambda _: None)

    with pytest.raises(FabricExtractionError) as exc:
        extractor._poll_operation("https://fabric.example/v1/operations/op-2", 1)

    assert "InvalidRequest" in str(exc.value)
    assert "definition is unavailable" in str(exc.value)


def test_poll_operation_falls_back_from_regional_operation_url(monkeypatch):
    extractor = _extractor()
    calls: list[str] = []

    model_payload = base64_model_payload({"model": {"name": "Fallback Model", "tables": []}})

    def fake_request(method: str, url: str, **kwargs):
        del method, kwargs
        calls.append(url)
        if "wabi-india-central" in url:
            import requests

            raise requests.ConnectTimeout("regional operation endpoint timed out")
        if url.endswith("/result"):
            return _FakeResponse(200, {"definition": {"parts": [model_payload]}})
        return _FakeResponse(200, {"status": "Succeeded"})

    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", fake_request)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.time.sleep", lambda _: None)

    result = extractor._poll_operation(
        "https://wabi-india-central-a-primary-redirect.analysis.windows.net/v1/operations/op-3",
        1,
    )

    assert result["model"]["name"] == "Fallback Model"
    assert calls[0].startswith("https://wabi-india-central")
    assert "https://api.fabric.microsoft.com/v1/operations/op-3" in calls


def test_get_model_definition_retries_transient_initiate_timeout(monkeypatch):
    extractor = _extractor()
    calls: list[str] = []
    model_payload = base64_model_payload({"model": {"name": "Retry Model", "tables": []}})

    monkeypatch.setattr(extractor, "resolve_workspace_id", lambda workspace_id: workspace_id)
    monkeypatch.setattr(extractor, "resolve_model_id", lambda dataset_id: dataset_id)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.time.sleep", lambda _: None)

    def fake_request(method: str, url: str, **kwargs):
        del kwargs
        calls.append(f"{method} {url}")
        if len(calls) < 3:
            import requests

            raise requests.ConnectTimeout("api.fabric.microsoft.com timed out")
        return _FakeResponse(200, {"definition": {"parts": [model_payload]}})

    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", fake_request)

    result = extractor.get_model_definition("model-1")

    assert result["model"]["name"] == "Retry Model"
    assert len(calls) == 3
    assert all(call.startswith("POST ") for call in calls)


def test_list_semantic_models_retries_transient_timeout(monkeypatch):
    extractor = _extractor()
    calls: list[str] = []

    monkeypatch.setattr(extractor, "resolve_workspace_id", lambda workspace_id: workspace_id)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.time.sleep", lambda _: None)

    def fake_request(method: str, url: str, **kwargs):
        del kwargs
        calls.append(f"{method} {url}")
        if len(calls) < 3:
            import requests

            raise requests.ConnectTimeout("api.fabric.microsoft.com timed out")
        return _FakeResponse(
            200,
            {"value": [{"id": "model-1", "displayName": "Competitive Marketing Analysis"}]},
        )

    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", fake_request)

    models = extractor.list_semantic_models()

    assert models == [{"id": "model-1", "displayName": "Competitive Marketing Analysis"}]
    assert len(calls) == 3
    assert all(call.startswith("GET ") for call in calls)


def base64_model_payload(payload: dict) -> dict:
    import base64
    import json

    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return {
        "path": "model.bim",
        "payload": encoded,
        "payloadType": "InlineBase64",
    }
