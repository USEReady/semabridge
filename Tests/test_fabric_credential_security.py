from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from semabridge.api.main import app
from semabridge.auth.encryption import encrypt_token, decrypt_token
from semabridge.repository.credential_manager import CredentialManager
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Account, Credential
from semabridge.repository.orm.session_factory import db_manager, reset_engine
from semabridge.api.bootstrap.db_migrations import migrate_credentials_encryption
from semabridge.auth import token_resolver, token_refresher
from semabridge.domain.exceptions import AuthenticationError


@pytest.fixture(autouse=True)
def _setup_db(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///file::memory:?cache=shared&uri=true")
    monkeypatch.setenv("SEMABRIDGE_DB_BACKEND", "orm")
    # Ensure FABRIC_CLIENT_ID is not required
    monkeypatch.delenv("FABRIC_CLIENT_ID", raising=False)

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)
    yield
    db_manager.reset()


def test_secrets_encrypted_in_credentials_table_and_not_leaked_in_api():
    """
    1. Secret rows saved in semabridge_credentials must be encrypted (Fernet) on disk/DB.
    2. get_credentials(mask_secrets=True) must return bullet points ('••••••••').
    3. get_credentials(mask_secrets=False) returns decrypted values.
    4. API responses must never leak secrets or encrypted tokens in plain text.
    """
    cm = CredentialManager()
    cm.save_credentials(
        "snowflake",
        {
            "account": "xy12345.us-east-1",
            "user": "analyst",
            "password": "SuperSecretPassword123!",
            "warehouse": "COMPUTE_WH",
        },
    )

    # Direct raw SQL inspection of semabridge_credentials table
    engine = db_manager.get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT key, value, is_secret FROM semabridge_credentials WHERE service = 'snowflake'")
        ).mappings().all()

    by_key = {r["key"]: r for r in rows}
    assert by_key["user"]["value"] == "analyst"
    assert by_key["user"]["is_secret"] in (False, 0)

    # Password MUST be encrypted in raw DB row
    assert by_key["password"]["is_secret"] in (True, 1)
    raw_password = by_key["password"]["value"]
    assert raw_password != "SuperSecretPassword123!"
    assert decrypt_token(raw_password) == "SuperSecretPassword123!"

    # Masked read for UI/display
    masked = cm.get_credentials("snowflake", mask_secrets=True)
    assert masked["password"] == "••••••••"
    assert masked["user"] == "analyst"

    # Internal unmasked read
    unmasked = cm.get_credentials("snowflake", mask_secrets=False)
    assert unmasked["password"] == "SuperSecretPassword123!"

    # Account API check: credentials with secrets stored on Account
    with TestClient(app) as client:
        # Create an account with secret credentials
        create_res = client.post(
            "/api/accounts",
            json={
                "connector_type": "FABRIC",
                "tag": "prod-spn",
                "identity_email": "spn-app-id",
                "credentials": {
                    "auth_type": "service_principal",
                    "tenant_id": "tenant-abc",
                    "client_id": "client-xyz",
                    "client_secret": "VerySecretAppKey999!",
                },
            },
        )
        assert create_res.status_code == 201

        # Check API listing response
        list_res = client.get("/api/accounts?connector=FABRIC")
        assert list_res.status_code == 200
        data = list_res.json()
        assert len(data) >= 1
        account = next(a for a in data if a["tag"] == "prod-spn")

        # Must not contain client_secret in plain text anywhere
        json_str = json.dumps(account)
        assert "VerySecretAppKey999!" not in json_str
        # Must not contain encrypted_token in API response
        assert "encrypted_token" not in account or account.get("encrypted_token") is None


def test_migration_encrypts_existing_plaintext_credential_rows():
    """
    Existing plaintext secret rows in semabridge_credentials must be encrypted by
    migrate_credentials_encryption. Running it again must be idempotent.
    """
    engine = db_manager.get_engine()
    # Insert legacy unencrypted rows directly via raw SQL
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO semabridge_credentials (owner_id, service, key, value, is_secret) "
                "VALUES (0, 'legacy_svc', 'api_key', 'plain_secret_key_123', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO semabridge_credentials (owner_id, service, key, value, is_secret) "
                "VALUES (0, 'legacy_svc', 'username', 'plain_user_456', 0)"
            )
        )
        conn.commit()

    # Verify rows before migration
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT key, value, is_secret FROM semabridge_credentials WHERE service = 'legacy_svc'")
        ).mappings().all()
    by_key = {r["key"]: r["value"] for r in rows}
    assert by_key["api_key"] == "plain_secret_key_123"
    assert by_key["username"] == "plain_user_456"

    # Run migration
    encrypted_count = migrate_credentials_encryption(engine)
    assert encrypted_count >= 1

    # Verify rows after migration
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT key, value, is_secret FROM semabridge_credentials WHERE service = 'legacy_svc'")
        ).mappings().all()
    by_key_after = {r["key"]: r["value"] for r in rows}
    # Secret row is encrypted now
    assert by_key_after["api_key"] != "plain_secret_key_123"
    assert decrypt_token(by_key_after["api_key"]) == "plain_secret_key_123"
    # Non-secret row unchanged
    assert by_key_after["username"] == "plain_user_456"

    # Run migration a second time (idempotency check)
    second_count = migrate_credentials_encryption(engine)
    assert second_count == 0  # Already encrypted, nothing new to encrypt
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM semabridge_credentials WHERE service = 'legacy_svc' AND key = 'api_key'")
        ).mappings().first()
    assert decrypt_token(row["value"]) == "plain_secret_key_123"


def test_token_resolver_ignores_global_fabric_token_fallback():
    """
    token_resolver must NOT fall back to global plain-text fabric_token rows in semabridge_credentials.
    If the project's selected account has no token, it must fail / request reauth,
    never quietly reading a global token.
    """
    engine = db_manager.get_engine()
    # Insert legacy global fabric_token in credentials table
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO semabridge_credentials (owner_id, service, key, value, is_secret) "
                "VALUES (0, 'fabric_token', 'access_token', 'rogue_global_access_token', 1)"
            )
        )
        conn.commit()

    with pytest.raises(AuthenticationError) as exc_info:
        token_resolver.resolve_fabric_access_token(None, identity_id="nonexistent-account-id")

    assert "reauth_required" in str(exc_info.value)
    # Confirm it never returned the rogue global token
    assert "rogue_global_access_token" not in str(exc_info.value)


def test_refresh_fabric_token_single_path_no_client_id_env(monkeypatch):
    """
    Refresh must go through token_resolver as the single source.
    auth/token_refresher.ensure_valid_token uses the account token, with no dependency on
    the FABRIC_CLIENT_ID environment variable.
    """
    # Explicitly ensure FABRIC_CLIENT_ID is absent
    monkeypatch.delenv("FABRIC_CLIENT_ID", raising=False)

    payload = {
        "access_token": "expired-access-token",
        "refresh_token": "valid-rt-12345",
        "tenant_id": "contoso-tenant-id",
        "username": "user@contoso.com",
    }

    # Create account with an expired token and refresh_token
    with db_manager.get_session() as session:
        session.add(
            Account(
                id="acct-fabric-refresh",
                connector_type="FABRIC",
                tag="refresh-acct",
                identity_email="user@contoso.com",
                auth_type="interactive",
                encrypted_token=encrypt_token(json.dumps(payload)),
                token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                status="Active",
            )
        )
        session.commit()

    recorded_token_response = {
        "access_token": "refreshed-access-token-999",
        "refresh_token": "new-rt-999",
        "expires_in": 3600,
    }

    from semabridge.domain.exceptions import SemaBridgeError

    def _mock_validate(token):
        if token != "refreshed-access-token-999":
            raise SemaBridgeError("Token expired")
        return token

    with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", side_effect=_mock_validate):
        with patch("msal.PublicClientApplication") as MockPubApp:
            mock_inst = MagicMock()
            mock_inst.acquire_token_by_refresh_token.return_value = recorded_token_response
            MockPubApp.return_value = mock_inst

            # Call ensure_valid_token directly
            with db_manager.get_session() as session:
                acct = session.get(Account, "acct-fabric-refresh")
                token = token_refresher.ensure_valid_token(acct, session)

            assert token == "refreshed-access-token-999"

            # Verify that account was updated with the new token
            with db_manager.get_session() as session:
                acct = session.get(Account, "acct-fabric-refresh")
                assert acct is not None
                decrypted = json.loads(decrypt_token(acct.encrypted_token))
                assert decrypted["access_token"] == "refreshed-access-token-999"
                assert decrypted["refresh_token"] == "new-rt-999"


def test_service_principal_account_used_when_selected_by_project():
    """
    A service-principal Fabric account stored encrypted in Account must be resolved
    and used when a project selects it via source.identity_id.
    """
    spn_payload = {
        "auth_type": "service_principal",
        "tenant_id": "tenant-guid-111",
        "client_id": "app-id-999",
        "client_secret": "my-client-secret-pass",
    }

    # Create service principal account
    with db_manager.get_session() as session:
        session.add(
            Account(
                id="spn-acct-1",
                connector_type="FABRIC",
                tag="finance-spn",
                identity_email="app-id-999",
                auth_type="service_principal",
                encrypted_token=encrypt_token(json.dumps(spn_payload)),
                status="Active",
            )
        )
        session.commit()

    recorded_spn_response = {
        "access_token": "spn-access-token-88888",
        "expires_in": 3600,
    }

    with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", return_value=True):
        with patch("msal.ConfidentialClientApplication") as MockConfApp:
            mock_inst = MagicMock()
            mock_inst.acquire_token_for_client.return_value = recorded_spn_response
            MockConfApp.return_value = mock_inst

            resolved_token = token_resolver.resolve_fabric_access_token(
                header_bearer_token=None,
                identity_id="spn-acct-1",
            )

            assert resolved_token == "spn-access-token-88888"
            mock_inst.acquire_token_for_client.assert_called_once_with(scopes=["https://api.fabric.microsoft.com/.default"])


def test_encrypted_secrets_readable_after_simulated_restart(tmp_path, monkeypatch):
    """
    (a) Encrypted secrets must remain readable after a simulated restart (new process, same key).
    """
    from semabridge.auth.encryption import _get_fernet

    key_file = tmp_path / "encryption.key"
    test_secret = "secret-key-persisted-across-restart-12345"
    key_file.write_text(test_secret, encoding="utf-8")

    monkeypatch.setenv("SEMABRIDGE_ENCRYPTION_KEY_FILE", str(key_file))
    monkeypatch.delenv("SEMABRIDGE_ENCRYPTION_KEY", raising=False)
    _get_fernet.cache_clear()

    # Save credentials
    cm = CredentialManager()
    cm.save_credentials(
        "snowflake",
        {
            "user": "persisted_user",
            "password": "PersistedSecretPassword!",
        },
    )

    # Verify encrypted in DB
    unmasked_before = cm.get_credentials("snowflake", mask_secrets=False)
    assert unmasked_before["password"] == "PersistedSecretPassword!"

    # Simulate restart: clear the process-level LRU cache
    _get_fernet.cache_clear()

    # Read back in "new process" with same key file
    cm_after = CredentialManager()
    unmasked_after = cm_after.get_credentials("snowflake", mask_secrets=False)
    assert unmasked_after["password"] == "PersistedSecretPassword!"


def test_missing_key_fails_startup_cleanly_without_data_loss(tmp_path, monkeypatch):
    """
    (b) A missing key fails startup with a clear error and leaves rows untouched
    (no re-encryption, no data loss).
    """
    from semabridge.auth.encryption import _get_fernet, ensure_encryption_key_available

    engine = db_manager.get_engine()
    # Insert existing rows into DB
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO semabridge_credentials (owner_id, service, key, value, is_secret) "
                "VALUES (0, 'safe_service', 'api_secret', 'critical_plaintext_data', 1)"
            )
        )
        conn.commit()

    # Configure missing key with auth enabled
    non_existent = tmp_path / "missing_folder" / "missing.key"
    monkeypatch.setenv("SEMABRIDGE_ENCRYPTION_KEY_FILE", str(non_existent))
    monkeypatch.delenv("SEMABRIDGE_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _get_fernet.cache_clear()

    # ensure_encryption_key_available must raise RuntimeError
    with pytest.raises(RuntimeError) as exc_info:
        ensure_encryption_key_available()

    assert "AUTH_ENABLED=true you must set SEMABRIDGE_ENCRYPTION_KEY" in str(exc_info.value)

    # Verify rows in DB remain completely untouched (no data loss)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM semabridge_credentials WHERE service = 'safe_service' AND key = 'api_secret'")
        ).mappings().first()
    assert row["value"] == "critical_plaintext_data"


def test_target_publish_path_uses_account_token_without_global_fallback():
    """
    Fabric target / publish path must resolve the access token strictly from the Account
    bundle without falling back to or writing global fabric_token in credentials.
    """
    from semabridge.auth.credential_builder import build_fabric_config
    from semabridge.core.settings import FabricConfig
    from semabridge.connectors.fabric_publisher import FabricPublisher

    # Add account with valid token
    payload = {
        "access_token": "account-scoped-target-token-xyz",
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + 3600,
        "tenant_id": "tenant-target-1",
        "username": "deployer@contoso.com",
    }
    with db_manager.get_session() as session:
        account = Account(
            id="acct-target-deploy",
            connector_type="FABRIC",
            tag="target-deploy-acct",
            identity_email="deployer@contoso.com",
            auth_type="interactive",
            encrypted_token=encrypt_token(json.dumps(payload)),
            status="Active",
        )
        session.add(account)
        session.commit()

        base_config = FabricConfig(
            tenant_id="default-tenant",
            client_id="default-client",
            workspace_id="ws-target-123",
        )

        with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", return_value=True):
            fabric_cfg, fabric_token = build_fabric_config(account, session, base_config)

    assert fabric_token == "account-scoped-target-token-xyz"
    assert fabric_cfg.workspace_id == "ws-target-123"

    # Verify publisher initialization
    publisher = FabricPublisher(fabric_cfg)
    publisher._access_token = fabric_token
    assert publisher._access_token == "account-scoped-target-token-xyz"

    # Confirm semabridge_credentials has NO global fabric_token row written
    engine = db_manager.get_engine()
    with engine.connect() as conn:
        global_rows = conn.execute(
            text("SELECT * FROM semabridge_credentials WHERE service = 'fabric_token'")
        ).mappings().all()
    assert len(global_rows) == 0


def test_post_account_test_connection_endpoints():
    """
    5. Test connection backend endpoint POST /api/accounts/{id}/test
    Verifies Fabric device-code, Fabric service principal, and Snowflake accounts
    with recorded responses.
    """
    # 1. Setup Fabric Device Code Account
    with db_manager.get_session() as session:
        session.add(
            Account(
                id="acct-test-dc",
                connector_type="FABRIC",
                tag="test-device-code",
                identity_email="devcode@contoso.com",
                auth_type="interactive",
                encrypted_token=encrypt_token("some-token"),
                status="Active",
            )
        )
        # 2. Setup Fabric Service Principal Account
        session.add(
            Account(
                id="acct-test-spn",
                connector_type="FABRIC",
                tag="test-spn",
                identity_email="spn-client-id",
                auth_type="service_principal",
                encrypted_token=encrypt_token(json.dumps({
                    "auth_type": "service_principal",
                    "tenant_id": "t1",
                    "client_id": "c1",
                    "client_secret": "s1",
                })),
                status="Active",
            )
        )
        # 3. Setup Snowflake Account
        session.add(
            Account(
                id="acct-test-sf",
                connector_type="SNOWFLAKE",
                tag="test-snowflake",
                identity_email="sf_analyst",
                encrypted_token=encrypt_token(json.dumps({
                    "account": "xy12345.us-east-1",
                    "user": "sf_analyst",
                    "password": "SfPassword123!",
                    "warehouse": "COMPUTE_WH",
                    "database": "ANALYTICS",
                })),
                status="Active",
            )
        )
        session.commit()

    with TestClient(app) as client:
        # Test 1: Fabric Device Code Account
        with patch("semabridge.auth.token_resolver.resolve_fabric_access_token", return_value="recorded-dc-token"):
            res_dc = client.post("/api/accounts/acct-test-dc/test")
            assert res_dc.status_code == 200
            assert res_dc.json() == {"status": "success", "message": "Fabric authentication successful"}

        # Test 2: Fabric Service Principal Account
        with patch("semabridge.auth.token_resolver.resolve_fabric_access_token", return_value="recorded-spn-token"):
            res_spn = client.post("/api/accounts/acct-test-spn/test")
            assert res_spn.status_code == 200
            assert res_spn.json() == {"status": "success", "message": "Fabric authentication successful"}

        # Test 3: Snowflake Account
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        with patch("snowflake.connector.connect", return_value=mock_conn) as mock_sf_connect:
            res_sf = client.post("/api/accounts/acct-test-sf/test")
            assert res_sf.status_code == 200
            assert res_sf.json() == {"status": "success", "message": "Snowflake connection successful"}
            mock_sf_connect.assert_called_once()
            mock_cursor.execute.assert_called_once_with("SELECT CURRENT_VERSION()")
            mock_conn.close.assert_called_once()

        # Test 4: Missing Account returns 404
        res_missing = client.post("/api/accounts/nonexistent-id/test")
        assert res_missing.status_code == 404

