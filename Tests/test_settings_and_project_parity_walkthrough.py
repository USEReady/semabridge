from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from semabridge.api.main import app
from semabridge.auth.encryption import encrypt_token, decrypt_token, _get_fernet
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Account, Project
from semabridge.repository.orm.session_factory import db_manager, reset_engine
from semabridge.auth import token_resolver, token_refresher
from semabridge.auth.token_refresher import TokenExpiredError
from semabridge.core.engine.exceptions import AuthenticationError as EngineAuthError
from semabridge.domain.exceptions import AuthenticationError, NotFoundError
from semabridge.sources.base import SourceAuthError, SourceCredentials
from semabridge.sources.fabric.credentials import resolve_fabric_credentials
from semabridge.core.engine.deployment.snowflake import target_snowflake_config
from semabridge.core.engine.context import RunContext
from semabridge.core.settings import SnowflakeConfig


@pytest.fixture(scope="module")
def sample_rsa_key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    initial_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"initial-secret-passphrase"),
    ).decode("utf-8")
    rotated_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"new-rotated-passphrase"),
    ).decode("utf-8")
    return initial_pem, rotated_pem


@pytest.fixture(autouse=True)
def _setup_test_db(monkeypatch, tmp_path):
    key_file = tmp_path / "walkthrough.key"
    key_file.write_text("walkthrough-secret-fernet-key-32chars!", encoding="utf-8")

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///file::memory:?cache=shared&uri=true")
    monkeypatch.setenv("SEMABRIDGE_DB_BACKEND", "orm")
    monkeypatch.setenv("SEMABRIDGE_ENCRYPTION_KEY_FILE", str(key_file))
    monkeypatch.delenv("SEMABRIDGE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("FABRIC_CLIENT_ID", raising=False)
    _get_fernet.cache_clear()

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)
    yield
    db_manager.reset()
    _get_fernet.cache_clear()


# ============================================================================
# WALKTHROUGH: FABRIC DEVICE CODE (INTERACTIVE)
# ============================================================================

def test_fabric_device_code_lifecycle_walkthrough():
    """
    Automated walkthrough for Fabric Device Code account lifecycle across steps 1-8.
    """
    with TestClient(app) as client:
        # Step 1: Create Account
        token_payload = {
            "access_token": "fabric-dc-initial-token-111",
            "refresh_token": "fabric-dc-initial-rt-111",
            "expires_at": int(time.time()) + 3600,
            "username": "analyst@fabric.example.com",
            "tenant_id": "tenant-dc-1",
        }
        res_create = client.post(
            "/api/accounts",
            json={
                "connection_id": "acct-dc-1",
                "connector_type": "FABRIC",
                "tag": "prod-fabric-dc",
                "identity_email": "analyst@fabric.example.com",
                "encrypted_token": json.dumps(token_payload),
            },
        )
        assert res_create.status_code == 201

        # Step 2: GET /api/accounts (masked secrets, token status shown)
        res_get = client.get("/api/accounts?connector_type=FABRIC")
        assert res_get.status_code == 200
        accounts = res_get.json()
        acct = next(a for a in accounts if a["id"] == "acct-dc-1")
        assert acct["tag"] == "prod-fabric-dc"
        assert acct["token_status"] == "valid"
        assert "encrypted_token" not in acct
        # Verify plain-text token not leaked
        assert "fabric-dc-initial-token-111" not in json.dumps(acct)
        assert "fabric-dc-initial-rt-111" not in json.dumps(acct)

        # Step 3: POST /api/accounts/{id}/test
        with patch("semabridge.auth.token_resolver.resolve_fabric_access_token", return_value="fabric-dc-initial-token-111"):
            res_test = client.post("/api/accounts/acct-dc-1/test")
            assert res_test.status_code == 200
            assert res_test.json() == {"status": "success", "message": "Fabric authentication successful"}

        # Step 4: Link account to project and run dry run -> credential origin = that account
        with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", return_value=True):
            creds = resolve_fabric_credentials(identity_id="acct-dc-1")
            assert creds.origin == "identity"
            assert creds.identity_id == "acct-dc-1"
            assert creds.access_token == "fabric-dc-initial-token-111"

        # Step 5: Token expiry
        # 5a: Simulate expired access token -> silent refresh
        with db_manager.get_session() as session:
            db_acct = session.get(Account, "acct-dc-1")
            db_acct.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            session.commit()

        refreshed_response = {
            "access_token": "fabric-dc-refreshed-token-222",
            "refresh_token": "fabric-dc-new-rt-222",
            "expires_in": 3600,
        }
        from semabridge.domain.exceptions import SemaBridgeError

        def _mock_validate_expiry(token):
            if token != "fabric-dc-refreshed-token-222":
                raise SemaBridgeError("Token expired")
            return True

        with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", side_effect=_mock_validate_expiry):
            with patch("msal.PublicClientApplication") as MockPubApp:
                mock_app = MagicMock()
                mock_app.acquire_token_by_refresh_token.return_value = refreshed_response
                MockPubApp.return_value = mock_app

                refreshed_token = token_resolver.resolve_fabric_access_token(None, identity_id="acct-dc-1")
                assert refreshed_token == "fabric-dc-refreshed-token-222"

        # 5b: Simulate invalid refresh token -> reauth_required and run fails naming account
        with patch("msal.PublicClientApplication") as MockPubApp:
            mock_app = MagicMock()
            mock_app.acquire_token_by_refresh_token.return_value = {"error": "invalid_grant", "error_description": "Revoked"}
            MockPubApp.return_value = mock_app

            with pytest.raises((AuthenticationError, TokenExpiredError)) as exc_info:
                with db_manager.get_session() as session:
                    db_acct = session.get(Account, "acct-dc-1")
                    db_acct.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=2)
                    token_refresher.ensure_valid_token(db_acct, session)
            assert "re-authenticate" in str(exc_info.value).lower() or "reauth_required" in str(exc_info.value).lower()

            with pytest.raises(SourceAuthError) as exc_run:
                resolve_fabric_credentials(identity_id="acct-dc-1")
            assert "acct-dc-1" in str(exc_run.value)
            assert "re-authenticate" in str(exc_run.value)

        # 5c: Reconnect restores account
        fresh_payload = {
            "access_token": "fabric-dc-restored-token-333",
            "refresh_token": "fabric-dc-restored-rt-333",
            "expires_at": int(time.time()) + 3600,
        }
        client.post(
            "/api/accounts",
            json={
                "connector_type": "FABRIC",
                "tag": "prod-fabric-dc",
                "encrypted_token": json.dumps(fresh_payload),
            },
        )
        res_get_restored = client.get("/api/accounts?connector_type=FABRIC")
        acct_restored = next(a for a in res_get_restored.json() if a["id"] == "acct-dc-1")
        assert acct_restored["token_status"] == "valid"

        # Step 6: Restart (new process, same key)
        _get_fernet.cache_clear()
        with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", return_value=True):
            creds_after_restart = resolve_fabric_credentials(identity_id="acct-dc-1")
            assert creds_after_restart.access_token == "fabric-dc-restored-token-333"

        # Step 7: Delete account -> project fails naming account with NO fallback
        res_del = client.delete("/api/accounts/acct-dc-1")
        assert res_del.status_code == 200

        with pytest.raises(SourceAuthError) as exc_deleted:
            resolve_fabric_credentials(identity_id="acct-dc-1")
        assert "acct-dc-1" in str(exc_deleted.value)
        assert "Could not resolve Fabric token for identity 'acct-dc-1'" in str(exc_deleted.value)

        # Step 8: Edit tag
        client.post(
            "/api/accounts",
            json={
                "connection_id": "acct-dc-edit",
                "connector_type": "FABRIC",
                "tag": "dc-tag-before",
                "encrypted_token": json.dumps({"access_token": "tok"}),
            },
        )
        res_tag = client.patch("/api/accounts/acct-dc-edit/tag", json={"tag": "dc-tag-after"})
        assert res_tag.status_code == 200
        edited = next(a for a in res_tag.json() if a["id"] == "acct-dc-edit")
        assert edited["tag"] == "dc-tag-after"


# ============================================================================
# WALKTHROUGH: FABRIC SERVICE PRINCIPAL
# ============================================================================

def test_fabric_service_principal_lifecycle_walkthrough():
    """
    Automated walkthrough for Fabric Service Principal account lifecycle across steps 1-8.
    """
    with TestClient(app) as client:
        # Step 1: Create Account
        res_create = client.post(
            "/api/accounts",
            json={
                "connection_id": "acct-spn-1",
                "connector_type": "FABRIC",
                "tag": "prod-fabric-spn",
                "identity_email": "spn-app-guid-111",
                "credentials": {
                    "auth_type": "service_principal",
                    "tenant_id": "tenant-guid-111",
                    "client_id": "spn-app-guid-111",
                    "client_secret": "initial-secret-super-key-999",
                },
            },
        )
        assert res_create.status_code == 201

        # Step 2: GET /api/accounts (secrets masked, status shown)
        res_get = client.get("/api/accounts?connector_type=FABRIC")
        assert res_get.status_code == 200
        acct = next(a for a in res_get.json() if a["id"] == "acct-spn-1")
        assert acct["tag"] == "prod-fabric-spn"
        assert acct["auth_type"] == "service_principal"
        assert acct["token_status"] == "valid"
        assert "initial-secret-super-key-999" not in json.dumps(acct)
        assert "encrypted_token" not in acct

        # Step 3: POST /api/accounts/{id}/test
        with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", return_value=True):
            with patch("msal.ConfidentialClientApplication") as MockConfApp:
                mock_app = MagicMock()
                mock_app.acquire_token_for_client.return_value = {
                    "access_token": "spn-live-access-token-001",
                    "expires_in": 3600,
                }
                MockConfApp.return_value = mock_app

                res_test = client.post("/api/accounts/acct-spn-1/test")
                assert res_test.status_code == 200
                assert res_test.json() == {"status": "success", "message": "Fabric authentication successful"}

        # Step 4: Link account to project and run dry run -> credential origin = that account
        with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", return_value=True):
            with patch("msal.ConfidentialClientApplication") as MockConfApp:
                mock_app = MagicMock()
                mock_app.acquire_token_for_client.return_value = {
                    "access_token": "spn-dry-run-token-001",
                    "expires_in": 3600,
                }
                MockConfApp.return_value = mock_app

                creds = resolve_fabric_credentials(identity_id="acct-spn-1")
                assert creds.origin == "identity"
                assert creds.identity_id == "acct-spn-1"
                assert creds.access_token == "spn-dry-run-token-001"

        # Step 5: Token expiry / rotation
        # 5a: Client credentials tokens are acquired on demand; simulate invalid credentials in Azure
        with patch("msal.ConfidentialClientApplication") as MockConfApp:
            mock_app = MagicMock()
            mock_app.acquire_token_for_client.return_value = {
                "error": "invalid_client",
                "error_description": "AADSTS7000215: Invalid client secret provided.",
            }
            MockConfApp.return_value = mock_app

            with pytest.raises(SourceAuthError) as exc_info:
                resolve_fabric_credentials(identity_id="acct-spn-1")
            assert "acct-spn-1" in str(exc_info.value)
            assert "Please re-authenticate this account in the Connections panel" in str(exc_info.value)

        # 5b: Reconnect / secret rotation restores it
        res_update = client.patch(
            "/api/accounts/acct-spn-1",
            json={
                "credentials": {
                    "client_secret": "rotated-fresh-secret-key-1000",
                }
            },
        )
        assert res_update.status_code == 200

        with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", return_value=True):
            with patch("msal.ConfidentialClientApplication") as MockConfApp:
                mock_app = MagicMock()
                mock_app.acquire_token_for_client.return_value = {
                    "access_token": "spn-restored-token-002",
                    "expires_in": 3600,
                }
                MockConfApp.return_value = mock_app

                creds_restored = resolve_fabric_credentials(identity_id="acct-spn-1")
                assert creds_restored.access_token == "spn-restored-token-002"

        # Step 6: Restart (new process, same key)
        _get_fernet.cache_clear()
        with patch("semabridge.auth.fabric_validator.fabric_validator.validate_msal_token", return_value=True):
            with patch("msal.ConfidentialClientApplication") as MockConfApp:
                mock_app = MagicMock()
                mock_app.acquire_token_for_client.return_value = {
                    "access_token": "spn-restart-token-003",
                    "expires_in": 3600,
                }
                MockConfApp.return_value = mock_app

                creds_restart = resolve_fabric_credentials(identity_id="acct-spn-1")
                assert creds_restart.access_token == "spn-restart-token-003"

        # Step 7: Delete account -> project fails with clear error and NO fallback
        res_del = client.delete("/api/accounts/acct-spn-1")
        assert res_del.status_code == 200

        with pytest.raises(SourceAuthError) as exc_del:
            resolve_fabric_credentials(identity_id="acct-spn-1")
        assert "acct-spn-1" in str(exc_del.value)
        assert "Could not resolve Fabric token for identity 'acct-spn-1'" in str(exc_del.value)

        # Step 8: Edit tag & secret rotation (old secret purged from DB)
        res_create_for_edit = client.post(
            "/api/accounts",
            json={
                "connection_id": "acct-spn-edit",
                "connector_type": "FABRIC",
                "tag": "spn-tag-initial",
                "identity_email": "spn-app-guid-222",
                "credentials": {
                    "auth_type": "service_principal",
                    "tenant_id": "t2",
                    "client_id": "c2",
                    "client_secret": "old-unrotated-secret-xyz",
                },
            },
        )
        assert res_create_for_edit.status_code == 201

        # Rotate secret and update tag simultaneously
        res_edit = client.patch(
            "/api/accounts/acct-spn-edit",
            json={
                "tag": "spn-tag-rotated",
                "credentials": {
                    "client_secret": "brand-new-rotated-secret-abc",
                },
            },
        )
        assert res_edit.status_code == 200
        acct_edited = next(a for a in res_edit.json() if a["id"] == "acct-spn-edit")
        assert acct_edited["tag"] == "spn-tag-rotated"

        # Verify old secret is NOT stored in DB
        with db_manager.get_session() as session:
            row = session.get(Account, "acct-spn-edit")
            decrypted = json.loads(decrypt_token(row.encrypted_token))
            assert decrypted["client_secret"] == "brand-new-rotated-secret-abc"
            assert "old-unrotated-secret-xyz" not in row.encrypted_token
            assert "old-unrotated-secret-xyz" not in json.dumps(decrypted)


# ============================================================================
# WALKTHROUGH: SNOWFLAKE KEY PAIR
# ============================================================================

def test_snowflake_key_pair_lifecycle_walkthrough(sample_rsa_key_pair):
    """
    Automated walkthrough for Snowflake Key Pair account lifecycle across steps 1-8.
    """
    initial_pem, rotated_pem = sample_rsa_key_pair

    with TestClient(app) as client:
        # Step 1: Create Account
        sf_credentials = {
            "account": "xy12345.us-east-1",
            "user": "sf_keypair_analyst",
            "warehouse": "FINANCE_WH",
            "database": "ANALYTICS",
            "schema_name": "PUBLIC",
            "auth_type": "keypair",
            "private_key": initial_pem,
            "private_key_passphrase": "initial-secret-passphrase",
        }
        res_create = client.post(
            "/api/accounts",
            json={
                "connection_id": "acct-sf-kp-1",
                "connector_type": "SNOWFLAKE",
                "tag": "prod-snowflake-kp",
                "identity_email": "sf_keypair_analyst",
                "credentials": sf_credentials,
            },
        )
        assert res_create.status_code == 201

        # Step 2: GET /api/accounts (secrets masked, status shown)
        res_get = client.get("/api/accounts?connector_type=SNOWFLAKE")
        assert res_get.status_code == 200
        acct = next(a for a in res_get.json() if a["id"] == "acct-sf-kp-1")
        assert acct["tag"] == "prod-snowflake-kp"
        assert acct["status"] == "Active"
        assert "private_key" not in acct
        assert "private_key_passphrase" not in acct
        assert "encrypted_token" not in acct
        assert "initial-secret-passphrase" not in json.dumps(acct)
        assert initial_pem not in json.dumps(acct)

        # Step 3: POST /api/accounts/{id}/test (success with key pair)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("snowflake.connector.connect", return_value=mock_conn) as mock_connect:
            res_test = client.post("/api/accounts/acct-sf-kp-1/test")
            assert res_test.status_code == 200
            assert res_test.json() == {"status": "success", "message": "Snowflake connection successful"}
            mock_connect.assert_called_once()
            call_kwargs = mock_connect.call_args[1]
            assert call_kwargs["account"] == "xy12345.us-east-1"
            assert call_kwargs["user"] == "sf_keypair_analyst"
            # Verify private_key bytes were parsed and supplied
            assert "private_key" in call_kwargs
            assert isinstance(call_kwargs["private_key"], (bytes, bytearray))
            mock_cursor.execute.assert_called_once_with("SELECT CURRENT_VERSION()")
            mock_conn.close.assert_called_once()

        # Step 4: Link account to project and verify target_snowflake_config resolves origin
        with db_manager.get_session() as session:
            session.add(
                Project(
                    project_id="proj-sf-walkthrough",
                    name="Walkthrough Project",
                    adapter="snowflake",
                    account_id=None,
                )
            )
            session.commit()

        # Link project to account
        res_link = client.patch(
            "/api/accounts/project/proj-sf-walkthrough/link-account",
            json={
                "account_id": "acct-sf-kp-1",
                "warehouse": "PROJECT_WH",
                "database": "PROJECT_DB",
            },
        )
        assert res_link.status_code == 200

        # Construct run context referencing identity_id
        mock_run_ctx = SimpleNamespace(
            config=SimpleNamespace(
                targets=[{"type": "snowflake", "identity_id": "acct-sf-kp-1"}],
                snowflake=SnowflakeConfig(
                    account="ambient-account",
                    user="ambient-user",
                    warehouse="AMBIENT_WH",
                    database="AMBIENT_DB",
                ),
            )
        )
        resolved_sf_cfg = target_snowflake_config(mock_run_ctx)
        assert resolved_sf_cfg.account == "xy12345.us-east-1"
        assert resolved_sf_cfg.user == "sf_keypair_analyst"
        assert resolved_sf_cfg.auth_type == "keypair"
        assert resolved_sf_cfg.private_key == initial_pem

        # Step 5: Key / Credential invalidation & rotation
        # Rotating key and passphrase via edit restores it
        res_rotate_key = client.patch(
            "/api/accounts/acct-sf-kp-1",
            json={
                "credentials": {
                    "private_key": rotated_pem,
                    "private_key_passphrase": "new-rotated-passphrase",
                }
            },
        )
        assert res_rotate_key.status_code == 200

        with db_manager.get_session() as session:
            db_sf = session.get(Account, "acct-sf-kp-1")
            decrypted = json.loads(decrypt_token(db_sf.encrypted_token))
            assert decrypted["private_key_passphrase"] == "new-rotated-passphrase"
            assert decrypted["private_key"] == rotated_pem
            assert "initial-secret-passphrase" not in db_sf.encrypted_token

        # Step 6: Restart (new process, same key)
        _get_fernet.cache_clear()
        resolved_after_restart = target_snowflake_config(mock_run_ctx)
        assert resolved_after_restart.account == "xy12345.us-east-1"
        assert resolved_after_restart.user == "sf_keypair_analyst"
        assert resolved_after_restart.private_key == rotated_pem

        # Step 7: Delete account
        # Extraction / discovery fails with clear error and NO fallback:
        from semabridge.api.services.core_discovery_impl import discover_snowflake
        from semabridge.core.engine.extraction import snowflake as snowflake_engine
        from semabridge.core.engine.exceptions import ExtractionError
        from semabridge.domain.exceptions import ValidationError

        res_del = client.delete("/api/accounts/acct-sf-kp-1")
        assert res_del.status_code == 200

        with pytest.raises(ValidationError) as exc_disc:
            discover_snowflake(identity_id="acct-sf-kp-1")
        assert "No Snowflake account found for identity_id 'acct-sf-kp-1'" in str(exc_disc.value)

        # Scoped extraction fails without fallback:
        mock_ext_engine = SimpleNamespace(_extract_snowflake_unscoped=lambda *a, **k: None)
        with pytest.raises(ExtractionError) as exc_ext:
            snowflake_engine._extract_snowflake_scoped(
                mock_ext_engine,
                mock_run_ctx,
                None,
                "acct-sf-kp-1",
            )
        assert "No Snowflake account found for identity_id 'acct-sf-kp-1'" in str(exc_ext.value)

        # Target deployment falls back to ambient config with a warning (pinned by test_deploy_to_snowflake_falls_back_when_target_identity_is_unlinked)
        fallback_cfg = target_snowflake_config(mock_run_ctx)
        assert fallback_cfg.account == "ambient-account"

        # Step 8: Edit tag and secret rotation on another account
        client.post(
            "/api/accounts",
            json={
                "connection_id": "acct-sf-edit",
                "connector_type": "SNOWFLAKE",
                "tag": "sf-tag-before",
                "identity_email": "user2",
                "credentials": {
                    "account": "acc2",
                    "user": "user2",
                    "auth_type": "keypair",
                    "private_key": initial_pem,
                    "private_key_passphrase": "pass-before",
                },
            },
        )
        res_tag_sf = client.patch("/api/accounts/acct-sf-edit/tag", json={"tag": "sf-tag-after"})
        assert res_tag_sf.status_code == 200
        acct_edited_sf = next(a for a in res_tag_sf.json() if a["id"] == "acct-sf-edit")
        assert acct_edited_sf["tag"] == "sf-tag-after"

        res_rot_sf = client.patch(
            "/api/accounts/acct-sf-edit",
            json={
                "credentials": {
                    "private_key_passphrase": "pass-after",
                }
            },
        )
        assert res_rot_sf.status_code == 200
        with db_manager.get_session() as session:
            db_acct_rot = session.get(Account, "acct-sf-edit")
            dec_rot = json.loads(decrypt_token(db_acct_rot.encrypted_token))
            assert dec_rot["private_key_passphrase"] == "pass-after"
            assert "pass-before" not in db_acct_rot.encrypted_token
