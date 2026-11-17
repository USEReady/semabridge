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
