from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_connections_status_returns_fallback_when_credential_manager_fails(monkeypatch):
    from semabridge.api.services import connection_domain_service as svc

    class _BoomCredentialManager:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        "semabridge.repository.credential_manager.CredentialManager",
        _BoomCredentialManager,
    )

    payload = await svc.get_connections_status(user_id=123)

    assert isinstance(payload, dict)
    assert payload["fabric"]["status"] == "disconnected"
    assert payload["snowflake"]["status"] == "disconnected"
    assert payload["databricks"]["status"] == "disconnected"
    assert "error" in payload["fabric"]
    assert "Credential status unavailable" in payload["fabric"]["error"]
