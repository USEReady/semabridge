from __future__ import annotations

import base64
import json
import time

from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.core.settings import FabricConfig


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err


def test_list_semantic_models_retries_once_after_401_and_refreshes_token(monkeypatch):
    """If the first Fabric call returns 401, extractor should refresh and retry once."""
    config = FabricConfig(
        tenant_id="11111111-1111-1111-1111-111111111111",
        client_id="22222222-2222-2222-2222-222222222222",
        client_secret="secret-value",
        workspace_id="33333333-3333-3333-3333-333333333333",
    )
    extractor = FabricExtractor(config)

    auth_headers_seen: list[str] = []
    stale_env_token = _fake_jwt(exp=int(time.time()) + 3600)

    def _fake_request(method: str, url: str, headers=None, **kwargs):
        del kwargs
        assert method == "GET"
        assert url.endswith("/semanticModels")
        auth_header = (headers or {}).get("Authorization", "")
        auth_headers_seen.append(auth_header)

        if auth_header == f"Bearer {stale_env_token}":
            return _FakeResponse(401, {"error": "Unauthorized"}, "Unauthorized")

        if auth_header == "Bearer fresh-service-token":
            return _FakeResponse(
                200,
                {
                    "value": [
                        {"id": "model-1", "displayName": "My Semantic Model"}
                    ]
                },
            )

        return _FakeResponse(500, {"error": "unexpected auth header"}, "unexpected auth header")

    def _fake_post(url: str, data=None, **kwargs):
        del kwargs
        assert "oauth2/v2.0/token" in url
        assert data is not None
        return _FakeResponse(200, {"access_token": "fresh-service-token", "expires_in": 3600})

    monkeypatch.setattr(
        "semabridge.connectors.fabric_extractor.get_fabric_access_token_from_env",
        lambda: stale_env_token,
    )
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.request", _fake_request)
    monkeypatch.setattr("semabridge.connectors.fabric_extractor.requests.post", _fake_post)

    models = extractor.list_semantic_models()

    assert len(models) == 1
    assert models[0]["id"] == "model-1"
    assert models[0]["displayName"] == "My Semantic Model"

    # First call uses stale env token, second call retries with refreshed token.
    assert auth_headers_seen == [
        f"Bearer {stale_env_token}",
        "Bearer fresh-service-token",
    ]


def _fake_jwt(exp: int) -> str:
    def encode(part: dict) -> str:
        raw = json.dumps(part, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'exp': exp})}.signature"
