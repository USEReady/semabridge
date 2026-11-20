"""Offline unit & API tests for the Databricks connector experience.

All HTTP calls are intercepted via monkeypatched ``requests.request`` /
``requests.post`` — zero real Databricks workspaces are contacted.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.api.routers.connection_router import router as connection_router
from semabridge.api.controllers.discovery_controller import router as discovery_router
from semabridge.api.services import databricks_connection_service as dbx_svc
from semabridge.auth.encryption import decrypt_token, encrypt_token
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Account, User
from semabridge.repository.orm.session_factory import db_manager


# ---------------------------------------------------------------------------
# Fake HTTP response helper
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text if text else (json.dumps(json_data) if json_data is not None else "")
        self.content = self.text.encode("utf-8")
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


@pytest.fixture(autouse=True)
def _isolated_db_and_crypto(monkeypatch, tmp_path):
    """Ensure clean DB tables, deterministic Fernet key, and AUTH_ENABLED=false by default."""
    monkeypatch.setenv("SEMABRIDGE_ENCRYPTION_KEY", "test-databricks-connector-secret-key-32b")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("SEMABRIDGE_TLS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY", raising=False)
    from semabridge.auth.encryption import _get_fernet

    _get_fernet.cache_clear()
    engine = db_manager.get_engine()
    Base.metadata.create_all(engine)

    # Clean up Databricks rows before each test
    from sqlalchemy import delete
    from semabridge.repository.orm.infra_models import Credential

    with db_manager.get_session() as session:
        session.execute(delete(Credential).where(Credential.service == "databricks"))
        session.execute(delete(Account).where(Account.connector_type == "DATABRICKS"))
        session.commit()

    yield

    with db_manager.get_session() as session:
        session.execute(delete(Credential).where(Credential.service == "databricks"))
        session.execute(delete(Account).where(Account.connector_type == "DATABRICKS"))
        session.commit()
    _get_fernet.cache_clear()


@pytest.fixture()
def api_client() -> TestClient:
    app = FastAPI()
    app.include_router(connection_router)
    app.include_router(discovery_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Requirement 1: Encrypted Credentials, No Module Globals, Token Refresh
# ---------------------------------------------------------------------------

def test_no_module_level_session_token_globals():
    assert not hasattr(dbx_svc, "_databricks_session_token")
    assert not hasattr(dbx_svc, "_databricks_session_token_expires_at")


@pytest.mark.asyncio
async def test_save_connection_encrypts_databricks_secrets_and_masks_status(monkeypatch):
    from sqlalchemy import select
    from semabridge.api.services.connection_domain_service import get_connections_status, save_connection
    from semabridge.repository.orm.infra_models import Credential

    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.delenv("DATABRICKS_CLIENT_SECRET", raising=False)

    res = await save_connection(
        "databricks",
        {
            "host": "https://adb-12345.11.azuredatabricks.net/",
            "auth_type": "pat",
            "token": "dapi-super-secret-pat-999",
            "warehouse_id": "wh-123",
            "catalog": "main",
            "schema_name": "analytics",
        },
        user_id=0,
    )
    assert res["status"] == "saved"

    # Stored row in DB must be Fernet ciphertext, NEVER plaintext
    with db_manager.get_session() as session:
        row = session.execute(
            select(Credential).where(
                Credential.owner_id == 0,
                Credential.service == "databricks",
                Credential.key == "token",
            )
        ).scalar_one()
        assert row.value.startswith("gAAAAA")
        assert "dapi-super-secret-pat-999" not in row.value
        assert decrypt_token(row.value) == "dapi-super-secret-pat-999"

    # Secret must NOT be leaked into os.environ
    assert os.environ.get("DATABRICKS_TOKEN") != "dapi-super-secret-pat-999"

    # Partial update with masked placeholder must NOT overwrite stored secret
    await save_connection(
        "databricks",
        {
            "host": "adb-12345.11.azuredatabricks.net",
            "auth_type": "pat",
            "token": "••••••••",
            "warehouse_id": "wh-456",
        },
        user_id=0,
    )
    with db_manager.get_session() as session:
        row = session.execute(
            select(Credential).where(
                Credential.owner_id == 0,
                Credential.service == "databricks",
                Credential.key == "token",
            )
        ).scalar_one()
        assert decrypt_token(row.value) == "dapi-super-secret-pat-999"

    # Status API response must mask secrets
    status = await get_connections_status(user_id=0)
    creds = status["databricks"]["credentials"]
    assert creds.get("token") == "••••••••"
    assert "dapi-super-secret-pat-999" not in json.dumps(status)


def test_m2m_client_credentials_refresh_and_expiry_persistence(monkeypatch):
    calls: List[Dict[str, Any]] = []

    def fake_post(url, data=None, headers=None, timeout=None, verify=None):
        calls.append({"url": url, "data": data, "verify": verify, "timeout": timeout})
        return FakeResponse(
            200,
            {"access_token": f"m2m-tok-{len(calls)}", "expires_in": 3600},
        )

    monkeypatch.setattr(dbx_svc.requests, "post", fake_post)

    # Create a saved Databricks service_principal Account
    acct_id = str(uuid.uuid4())
    bundle = {
        "host": "adb-m2m.cloud.databricks.com",
        "auth_type": "service_principal",
        "client_id": "sp-client-1",
        "client_secret": "sp-secret-1",
        "warehouse_id": "wh-1",
    }
    with db_manager.get_session() as session:
        session.add(
            Account(
                id=acct_id,
                connector_type="DATABRICKS",
                tag="dbx-sp",
                identity_email="sp-client-1",
                encrypted_token=encrypt_token(json.dumps(bundle)),
                auth_type="service_principal",
                status="Active",
            )
        )
        session.commit()

    t0 = 1_700_000_000.0
    creds, resolved_id = dbx_svc.resolve_effective_databricks_credentials(0, {"connection_id": acct_id})
    tok1, exp1 = dbx_svc.resolve_databricks_bearer_token(
        creds, account_id=resolved_id, now_fn=lambda: t0
    )
    assert tok1 == "m2m-tok-1"
    assert exp1 == t0 + 3600
    assert len(calls) == 1
    assert calls[0]["url"] == "https://adb-m2m.cloud.databricks.com/oidc/v1/token"
    assert calls[0]["data"]["grant_type"] == "client_credentials"
    assert calls[0]["verify"] is True

    # Reload from DB before expiry -> should reuse cached token without HTTP call
    creds2, _ = dbx_svc.resolve_effective_databricks_credentials(0, {"connection_id": acct_id})
    tok2, _ = dbx_svc.resolve_databricks_bearer_token(
        creds2, account_id=acct_id, now_fn=lambda: t0 + 100
    )
    assert tok2 == "m2m-tok-1"
    assert len(calls) == 1

    # Advance past expiry buffer -> must refresh automatically
    tok3, exp3 = dbx_svc.resolve_databricks_bearer_token(
        creds2, account_id=acct_id, now_fn=lambda: t0 + 3560
    )
    assert tok3 == "m2m-tok-2"
    assert exp3 == t0 + 3560 + 3600
    assert len(calls) == 2


def test_oauth_u2m_refresh_token_rotation_and_persistence(monkeypatch):
    calls: List[Dict[str, Any]] = []

    def fake_post(url, data=None, headers=None, timeout=None, verify=None):
        calls.append({"url": url, "data": data, "verify": verify})
        return FakeResponse(
            200,
            {
                "access_token": "u2m-fresh-access",
                "refresh_token": "u2m-rotated-refresh",
                "expires_in": 1800,
            },
        )

    monkeypatch.setattr(dbx_svc.requests, "post", fake_post)

    acct_id = str(uuid.uuid4())
    t0 = 1_700_000_000.0
    bundle = {
        "host": "adb-u2m.cloud.databricks.com",
        "auth_type": "interactive",
        "client_id": "oauth-app-1",
        "access_token": "u2m-old-access",
        "refresh_token": "u2m-old-refresh",
        "expires_at": str(int(t0 - 10)),  # already expired
    }
    with db_manager.get_session() as session:
        session.add(
            Account(
                id=acct_id,
                connector_type="DATABRICKS",
                tag="dbx-u2m",
                identity_email="alice@corp.com",
                encrypted_token=encrypt_token(json.dumps(bundle)),
                refresh_token=encrypt_token("u2m-old-refresh"),
                auth_type="interactive",
                status="Active",
            )
        )
        session.commit()

    creds, _ = dbx_svc.resolve_effective_databricks_credentials(0, {"connection_id": acct_id})
    tok, exp = dbx_svc.resolve_databricks_bearer_token(creds, account_id=acct_id, now_fn=lambda: t0)
    assert tok == "u2m-fresh-access"
    assert exp == t0 + 1800
    assert len(calls) == 1
    assert calls[0]["data"]["grant_type"] == "refresh_token"
    assert calls[0]["data"]["refresh_token"] == "u2m-old-refresh"

    # Verify rotated refresh token was persisted encrypted in Account
    reloaded, _ = dbx_svc.resolve_effective_databricks_credentials(0, {"connection_id": acct_id})
    assert reloaded["access_token"] == "u2m-fresh-access"
    assert reloaded["refresh_token"] == "u2m-rotated-refresh"


def test_build_databricks_config_decrypts_and_applies_project_target():
    from semabridge.auth.credential_builder import build_databricks_config
    from semabridge.core.settings import DatabricksConfig

    acct_id = str(uuid.uuid4())
    bundle = {
        "host": "adb-builder.cloud.databricks.com",
        "auth_type": "pat",
        "token": encrypt_token("dapi-inner-encrypted"),
        "warehouse_id": "wh-acct",
        "catalog": "acct_cat",
        "schema_name": "acct_sch",
    }
    with db_manager.get_session() as session:
        acct = Account(
            id=acct_id,
            connector_type="DATABRICKS",
            tag="dbx-builder",
            identity_email="builder@corp.com",
            encrypted_token=encrypt_token(json.dumps(bundle)),
            auth_type="pat",
            status="Active",
        )
        session.add(acct)
        session.commit()

        base = DatabricksConfig(host="base.databricks.com", warehouse_id="wh-base", catalog="main", schema_name="default")
        built = build_databricks_config(
            acct,
            session,
            base,
            project_target={"catalog": "proj_cat", "schema": "proj_sch"},
        )
        assert built.host == "adb-builder.cloud.databricks.com"
        assert built.token.get_secret_value() == "dapi-inner-encrypted"
        assert built.warehouse_id == "wh-acct"
        assert built.catalog == "proj_cat"
        assert built.schema_name == "proj_sch"


# ---------------------------------------------------------------------------
# Requirement 2: POST /api/connections/databricks/test & /warehouse/start
# ---------------------------------------------------------------------------

def test_databricks_test_endpoint_all_steps_pass_and_paginates_catalogs(monkeypatch, api_client):
    requested_urls: List[str] = []

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, verify=None):
        requested_urls.append(f"{method} {url}")
        assert verify is True
        assert headers["Authorization"] == "Bearer dapi-good-token"
        if url.endswith("/api/2.0/preview/scim/v2/Me"):
            return FakeResponse(200, {"userName": "alice@corp.com"})
        if url.endswith("/api/2.0/sql/warehouses/wh-001"):
            return FakeResponse(200, {"id": "wh-001", "name": "Analytics WH", "state": "RUNNING"})
        if url.endswith("/api/2.1/unity-catalog/catalogs"):
            if not params:
                return FakeResponse(200, {"catalogs": [{"name": "analytics"}], "next_page_token": "p2"})
            return FakeResponse(200, {"catalogs": [{"name": "main"}]})
        if url.endswith("/api/2.1/unity-catalog/schemas"):
            assert params["catalog_name"] == "main"
            return FakeResponse(200, {"schemas": [{"name": "default"}, {"name": "semabridge"}]})
        raise AssertionError(f"Unexpected call: {method} {url} {params}")

    monkeypatch.setattr(dbx_svc.requests, "request", fake_request)

    resp = api_client.post(
        "/api/connections/databricks/test",
        json={
            "host": "https://adb-test.cloud.databricks.com/",
            "auth_type": "pat",
            "token": "dapi-good-token",
            "warehouse_id": "wh-001",
            "catalog": "main",
            "schema_name": "semabridge",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["principal"] == "alice@corp.com"
    assert body["warehouse"] == {"id": "wh-001", "name": "Analytics WH", "state": "RUNNING", "can_start": False}
    assert body["catalogs"] == ["analytics", "main"]
    assert body["schemas"] == ["default", "semabridge"]
    assert [s["id"] for s in body["steps"]] == ["auth", "warehouse", "catalogs", "target"]
    assert all(s["status"] == "passed" for s in body["steps"])
    assert "dapi-good-token" not in json.dumps(body)


def test_databricks_test_reports_stopped_warehouse_without_auto_starting_and_explicit_start_works(monkeypatch, api_client):
    started_calls: List[str] = []

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, verify=None):
        if url.endswith("/start"):
            started_calls.append(url)
            return FakeResponse(200, {})
        if url.endswith("/api/2.0/preview/scim/v2/Me"):
            return FakeResponse(200, {"userName": "bob@corp.com"})
        if url.endswith("/api/2.0/sql/warehouses/wh-stopped"):
            return FakeResponse(200, {"id": "wh-stopped", "name": "Cold WH", "state": "STOPPED"})
        if url.endswith("/api/2.1/unity-catalog/catalogs"):
            return FakeResponse(200, {"catalogs": [{"name": "main"}]})
        if url.endswith("/api/2.1/unity-catalog/schemas"):
            return FakeResponse(200, {"schemas": [{"name": "semabridge"}]})
        raise AssertionError(f"Unexpected call: {method} {url}")

    monkeypatch.setattr(dbx_svc.requests, "request", fake_request)

    draft = {
        "host": "adb-stopped.cloud.databricks.com",
        "auth_type": "pat",
        "token": "dapi-tok",
        "warehouse_id": "wh-stopped",
        "catalog": "main",
        "schema_name": "semabridge",
    }
    resp = api_client.post("/api/connections/databricks/test", json=draft)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "warning"
    assert body["warehouse"]["state"] == "STOPPED"
    assert body["warehouse"]["can_start"] is True
    # Test MUST NOT auto-start the warehouse
    assert started_calls == []

    # Explicit warehouse start endpoint DOES start it
    start_resp = api_client.post("/api/connections/databricks/warehouse/start", json=draft)
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "starting"
    assert len(started_calls) == 1


def test_databricks_test_enforces_auth_and_owner_isolation(monkeypatch, api_client):
    monkeypatch.setenv("AUTH_ENABLED", "true")

    # Unauthenticated -> 401
    resp = api_client.post("/api/connections/databricks/test", json={})
    assert resp.status_code == 401

    # Owned by user 10, accessed by user 20 -> 403
    acct_id = str(uuid.uuid4())
    with db_manager.get_session() as session:
        session.add(
            Account(
                id=acct_id,
                connector_type="DATABRICKS",
                tag="user10-dbx",
                identity_email="u10@corp.com",
                encrypted_token=encrypt_token(json.dumps({"host": "h.databricks.com", "token": "dapi-10"})),
                auth_type="pat",
                status="Active",
                owner_id=10,
            )
        )
        session.commit()

    monkeypatch.setattr(dbx_svc, "_resolve_request_user_id", lambda req: 20)
    resp_forbidden = api_client.post(
        "/api/connections/databricks/test",
        json={"connection_id": acct_id},
    )
    assert resp_forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Requirement 3: POST /api/connections/databricks/permissions
# ---------------------------------------------------------------------------

def test_databricks_permissions_warns_on_excessive_privileges_and_gives_grants(monkeypatch, api_client):
    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, verify=None):
        if url.endswith("/api/2.0/preview/scim/v2/Me"):
            return FakeResponse(200, {"userName": "sp-semabridge"})
        if "/effective-permissions/catalog/main" in url:
            return FakeResponse(
                200,
                {
                    "privilege_assignments": [
                        {
                            "principal": "sp-semabridge",
                            "privileges": [{"privilege": "USE_CATALOG"}],
                        }
                    ]
                },
            )
        if "/effective-permissions/schema/main.semabridge" in url:
            return FakeResponse(
                200,
                {
                    "privilege_assignments": [
                        {
                            "principal": "sp-semabridge",
                            "privileges": [
                                {"privilege": "USE_SCHEMA"},
                                {"privilege": "CREATE_VIEW"},
                                {"privilege": "MODIFY"},
                                {"privilege": "CREATE_TABLE"},
                            ],
                        }
                    ]
                },
            )
        if "/effective-permissions/table/main.sales.orders" in url:
            return FakeResponse(
                200,
                {
                    "privilege_assignments": [
                        {
                            "principal": "sp-semabridge",
                            "privileges": [{"privilege": "SELECT"}],
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected call: {method} {url}")

    monkeypatch.setattr(dbx_svc.requests, "request", fake_request)

    resp = api_client.post(
        "/api/connections/databricks/permissions",
        json={
            "host": "adb-perm.cloud.databricks.com",
            "auth_type": "pat",
            "token": "dapi-perm-tok",
            "catalog": "main",
            "schema_name": "semabridge",
            "source_tables": ["main.sales.orders"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "warning"
    assert body["principal"] == "sp-semabridge"
    assert body["missing_grants"] == []
    assert len(body["warnings"]) == 1
    assert "CREATE TABLE" in body["warnings"][0]
    assert "MODIFY" in body["warnings"][0]
    assert any("GRANT USE CATALOG ON CATALOG `main` TO `sp-semabridge`;" in g for g in body["recommended_grants"])
    assert any("GRANT CREATE VIEW ON SCHEMA `main`.`semabridge` TO `sp-semabridge`;" in g for g in body["recommended_grants"])
    assert any("GRANT SELECT ON TABLE `main`.`sales`.`orders` TO `sp-semabridge`;" in g for g in body["recommended_grants"])
    assert any("REVOKE MODIFY ON SCHEMA `main`.`semabridge` FROM `sp-semabridge`;" in r for r in body["recommended_revokes"])


def test_databricks_permissions_reports_missing_privileges(monkeypatch, api_client):
    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, verify=None):
        if url.endswith("/api/2.0/preview/scim/v2/Me"):
            return FakeResponse(200, {"userName": "analyst@corp.com"})
        if "/effective-permissions/catalog/main" in url:
            return FakeResponse(
                200,
                {"privilege_assignments": [{"principal": "analyst@corp.com", "privileges": [{"privilege": "USE_CATALOG"}]}]},
            )
        if "/effective-permissions/schema/main.semabridge" in url:
            return FakeResponse(
                200,
                {"privilege_assignments": [{"principal": "analyst@corp.com", "privileges": [{"privilege": "USE_SCHEMA"}]}]},
            )
        raise AssertionError(f"Unexpected call: {method} {url}")

    monkeypatch.setattr(dbx_svc.requests, "request", fake_request)

    resp = api_client.post(
        "/api/connections/databricks/permissions",
        json={
            "host": "adb-perm.cloud.databricks.com",
            "auth_type": "pat",
            "token": "dapi-perm-tok",
            "catalog": "main",
            "schema_name": "semabridge",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "missing_permissions"
    assert any("GRANT CREATE VIEW ON SCHEMA `main`.`semabridge` TO `analyst@corp.com`;" in g for g in body["missing_grants"])


# ---------------------------------------------------------------------------
# Requirement 4: TLS, Retries on 429/5xx, Clear 401 vs 403 Errors
# ---------------------------------------------------------------------------

def test_client_uses_requests_verify_and_retries_429_and_5xx(monkeypatch, tmp_path):
    ca_file = tmp_path / "corp-ca.pem"
    ca_file.write_text("-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")
    monkeypatch.setenv("SEMABRIDGE_TLS_CA_BUNDLE", str(ca_file))

    attempts: List[Dict[str, Any]] = []
    sleeps: List[float] = []

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, verify=None):
        attempts.append({"verify": verify, "timeout": timeout})
        if len(attempts) == 1:
            return FakeResponse(429, {"message": "Too many requests"}, headers={"Retry-After": "0.25"})
        if len(attempts) == 2:
            return FakeResponse(503, {"message": "Service temporarily unavailable"})
        return FakeResponse(200, {"catalogs": [{"name": "prod"}]})

    monkeypatch.setattr(dbx_svc.requests, "request", fake_request)

    client = dbx_svc.DatabricksRestClient(
        "adb-tls.cloud.databricks.com",
        "dapi-secret-xyz",
        max_retries=3,
        backoff_base=0.1,
        sleep_fn=sleeps.append,
    )
    catalogs, next_tok = client.list_catalogs()
    assert catalogs == [{"name": "prod"}]
    assert next_tok is None
    assert len(attempts) == 3
    assert all(a["verify"] == str(ca_file) for a in attempts)
    assert sleeps == [0.25, 0.2]


def test_client_distinguishes_401_and_403_and_redacts_secrets(monkeypatch):
    def fake_401(method, url, **kwargs):
        return FakeResponse(401, {"message": "Invalid token dapi-secret-leak-123"})

    monkeypatch.setattr(dbx_svc.requests, "request", fake_401)
    client = dbx_svc.DatabricksRestClient("adb-err.cloud.databricks.com", "dapi-secret-leak-123")
    with pytest.raises(dbx_svc.DatabricksAPIError) as exc_401:
        client.list_catalogs()
    assert exc_401.value.status_code == 401
    assert exc_401.value.category == "auth"
    assert "dapi-secret-leak-123" not in exc_401.value.message

    def fake_403(method, url, **kwargs):
        return FakeResponse(403, {"message": "User lacks USE CATALOG"})

    monkeypatch.setattr(dbx_svc.requests, "request", fake_403)
    with pytest.raises(dbx_svc.DatabricksAPIError) as exc_403:
        client.list_catalogs()
    assert exc_403.value.status_code == 403
    assert exc_403.value.category == "permission"
    assert "Permission denied (403)" in exc_403.value.message


# ---------------------------------------------------------------------------
# Requirement 6: Paginated Discovery (/api/discovery/databricks/sources)
# ---------------------------------------------------------------------------

def test_discovery_databricks_sources_paginated(monkeypatch, api_client):
    acct_id = str(uuid.uuid4())
    with db_manager.get_session() as session:
        session.add(
            Account(
                id=acct_id,
                connector_type="DATABRICKS",
                tag="dbx-disc",
                identity_email="disc@corp.com",
                encrypted_token=encrypt_token(
                    json.dumps({
                        "host": "adb-disc.cloud.databricks.com",
                        "auth_type": "pat",
                        "token": "dapi-disc-tok",
                        "warehouse_id": "wh-1",
                    })
                ),
                auth_type="pat",
                status="Active",
            )
        )
        session.commit()

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, verify=None):
        assert verify is True
        if url.endswith("/api/2.1/unity-catalog/catalogs"):
            return FakeResponse(200, {"catalogs": [{"name": "main"}, {"name": "system"}]})
        if url.endswith("/api/2.1/unity-catalog/schemas"):
            return FakeResponse(200, {"schemas": [{"name": "sales"}, {"name": "information_schema"}]})
        if url.endswith("/api/2.1/unity-catalog/tables"):
            return FakeResponse(
                200,
                {
                    "tables": [
                        {"name": "customers", "full_name": "main.sales.customers", "table_type": "MANAGED"},
                        {"name": "orders", "full_name": "main.sales.orders", "table_type": "MANAGED"},
                        {"name": "products", "full_name": "main.sales.products", "table_type": "EXTERNAL"},
                    ]
                },
            )
        raise AssertionError(f"Unexpected call: {method} {url}")

    monkeypatch.setattr(dbx_svc.requests, "request", fake_request)

    # Page 1 (page_size=2)
    r1 = api_client.get(f"/api/discovery/databricks/sources?identity_id={acct_id}&page_size=2")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["catalogs"] == ["main"]
    assert d1["total_count"] == 3
    assert d1["total_returned"] == 2
    assert d1["has_more"] is True
    assert d1["next_page_token"]
    assert d1["sources"] == [{"catalog": "main", "schema": "sales", "tables": ["customers", "orders"]}]

    # Page 2 using next_page_token
    r2 = api_client.get(
        f"/api/discovery/databricks/sources?identity_id={acct_id}&page_size=2&page_token={d1['next_page_token']}"
    )
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["total_returned"] == 1
    assert d2["has_more"] is False
    assert d2["next_page_token"] is None
    assert d2["sources"] == [{"catalog": "main", "schema": "sales", "tables": ["products"]}]
