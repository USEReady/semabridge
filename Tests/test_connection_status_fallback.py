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


@pytest.mark.asyncio
async def test_get_connections_status_disconnected_when_no_accounts(monkeypatch):
    from semabridge.api.services import connection_domain_service as svc

    class _DummyCredentialManager:
        def get_connection_status(self, user_id=None):
            # Legacy manager might say configured: True from .env, but we should override it to disconnected
            return {
                "fabric": {"configured": True, "status": "connected"},
                "snowflake": {"configured": True, "status": "connected"},
                "databricks": {"configured": True, "status": "connected"},
            }

    class _DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def execute(self, stmt):
            class _Result:
                def scalars(self):
                    return self

                def all(self):
                    return []  # No accounts in DB
            return _Result()

    class _DummyDbManager:
        def get_session(self):
            return _DummySession()

    monkeypatch.setattr("semabridge.repository.credential_manager.CredentialManager", _DummyCredentialManager)
    monkeypatch.setattr("semabridge.repository.orm.session_factory.db_manager", _DummyDbManager())

    payload = await svc.get_connections_status(user_id=123)

    for svc_name in ("fabric", "snowflake", "databricks"):
        assert payload[svc_name]["has_account"] is False
        assert payload[svc_name]["configured"] is False
        assert payload[svc_name]["status"] == "disconnected"


@pytest.mark.asyncio
async def test_get_connections_status_connected_when_account_exists(monkeypatch):
    from semabridge.api.services import connection_domain_service as svc

    class _DummyCredentialManager:
        def get_connection_status(self, user_id=None):
            return {
                "fabric": {"configured": False, "status": "disconnected"},
                "snowflake": {"configured": False, "status": "disconnected"},
                "databricks": {"configured": False, "status": "disconnected"},
            }

    class _DummyAccount:
        def __init__(self, connector_type, tag, aid):
            self.connector_type = connector_type
            self.tag = tag
            self.id = aid
            self.identity_email = "test@example.com"
            self.owner_id = 123

    class _DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def execute(self, stmt):
            class _Result:
                def scalars(self):
                    return self

                def all(self):
                    return [_DummyAccount("SNOWFLAKE", "dev-snow", 42)]
            return _Result()

    class _DummyDbManager:
        def get_session(self):
            return _DummySession()

    monkeypatch.setattr("semabridge.repository.credential_manager.CredentialManager", _DummyCredentialManager)
    monkeypatch.setattr("semabridge.repository.orm.session_factory.db_manager", _DummyDbManager())

    payload = await svc.get_connections_status(user_id=123)

    # Snowflake has an account -> connected
    assert payload["snowflake"]["has_account"] is True
    assert payload["snowflake"]["configured"] is True
    assert payload["snowflake"]["status"] == "connected"
    assert payload["snowflake"]["account_tag"] == "dev-snow"

    # Fabric has no account -> disconnected
    assert payload["fabric"]["has_account"] is False
    assert payload["fabric"]["configured"] is False
    assert payload["fabric"]["status"] == "disconnected"

