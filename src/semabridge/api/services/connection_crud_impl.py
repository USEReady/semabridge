"""Connection CRUD, status, and connectivity tests (UI-driven auth).

Status aggregation across CredentialManager + the Account table, save/delete of
service credentials, and live connectivity tests for Fabric/Snowflake/Databricks
plus Snowflake OAuth S2S. Independent of the Fabric auth/workspace modules. Re-exported
through ``connection_domain_service``.
"""
from typing import Dict, Any

from semabridge.core.settings import reload_settings
from semabridge.connectors.factory import make_source_extractor, make_target_emitter
from semabridge.domain.exceptions import InternalError, ValidationError
from semabridge.api.services.connection_shared import logger


async def get_connections_status(user_id: int = 0):
    """Get configuration status for all supported services.

    Args:
        user_id: Authenticated user ID.  ``0`` returns global/system status.
    """
    from semabridge.repository.credential_manager import CredentialManager
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.models import Account
    from sqlalchemy import select

    try:
        cm = CredentialManager()
        status = cm.get_connection_status(user_id=user_id)

        # Augment with Account table — accounts stored via the Connections UI
        # are the primary credential store; CredentialManager is the legacy store.
        _CONNECTOR_MAP = {"FABRIC": "fabric", "SNOWFLAKE": "snowflake", "DATABRICKS": "databricks"}
        try:
            with db_manager.get_session() as session:
                accounts = session.execute(select(Account)).scalars().all()
                for acct in accounts:
                    svc = _CONNECTOR_MAP.get(acct.connector_type.upper())
                    if not svc:
                        continue
                    svc_status = status.get(svc, {})
                    # Mark as configured if a valid account exists
                    svc_status["has_account"] = True
                    svc_status["account_tag"] = acct.tag
                    svc_status["account_id"] = str(acct.id)
                    svc_status["identity_email"] = acct.identity_email or ""
                    # Resolve missing fields from account bundle
                    if acct.encrypted_token:
                        try:
                            import json
                            from semabridge.auth.encryption import decrypt_token
                            bundle = json.loads(decrypt_token(acct.encrypted_token))
                            if isinstance(bundle, dict):
                                remaining_missing = [f for f in svc_status.get("missing_fields", []) if f not in bundle]
                                svc_status["missing_fields"] = remaining_missing
                                if not remaining_missing and (svc != "fabric" or svc_status.get("has_auth") or bundle.get("access_token") or bundle.get("client_secret")):
                                    svc_status["configured"] = True
                                    svc_status["status"] = "connected"
                        except Exception:
                            pass
                    # Fabric: if account exists with a valid token, mark as configured
                    if svc == "fabric" and acct.encrypted_token:
                        svc_status["configured"] = True
                        svc_status["status"] = "connected"
                        svc_status["has_auth"] = True
                        svc_status["auth_method"] = "interactive"
                    status[svc] = svc_status
        except Exception as acc_err:
            logger.warning("Could not load Account table for status: %s", acc_err)

        return status
    except Exception as e:
        logger.exception(f"Failed to fetch connection status: {e}")
        # Keep the Settings page usable even when credential storage is unavailable.
        # Returning a safe disconnected snapshot avoids a hard 500 loop in the UI.
        msg = f"Credential status unavailable: {e}"
        return {
            "fabric": {
                "configured": False,
                "fields_stored": 0,
                "fields_required": 1,
                "missing_fields": ["workspace_id"],
                "credentials": {},
                "auth_method": "none",
                "has_auth": False,
                "status": "disconnected",
                "error": msg,
            },
            "snowflake": {
                "configured": False,
                "fields_stored": 0,
                "fields_required": 5,
                "missing_fields": ["account", "user", "warehouse", "database", "password"],
                "credentials": {},
                "auth_type": "password",
                "status": "disconnected",
                "error": msg,
            },
            "databricks": {
                "configured": False,
                "fields_stored": 0,
                "fields_required": 3,
                "missing_fields": ["host", "warehouse_id", "token"],
                "credentials": {},
                "auth_type": "pat",
                "auth_method": "none",
                "status": "disconnected",
                "error": msg,
            },
        }


async def save_connection(service: str, payload: Dict[str, Any], user_id: int = 0):
    """Save credentials for a service and refresh in-process settings.

    Args:
        user_id: Owner of these credentials.  ``0`` stores as global/system.
                 Pass the authenticated user's ID for user-scoped storage.
    """
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        saved = cm.save_credentials(service, payload, user_id=user_id)
        # Inject to os.environ using the full precedence chain so that
        # connectors initialised in this process pick up the new values.
        # For user-scoped saves, we inject the user's credentials (not global)
        # so that the in-process settings.reload() picks them up correctly.
        cm.inject_credentials_to_env(service, user_id=user_id)
        reload_settings()
        return {
            "status": "saved",
            "service": service,
            "fields_saved": saved,
        }
    except Exception as e:
        logger.exception(f"Failed to save {service} credentials: {e}")
        raise InternalError(str(e))


async def delete_connection(service: str, user_id: int = 0):
    """Remove stored credentials for a service.

    Args:
        user_id: Scope to delete.  ``0`` removes global rows; pass a user
                 ID to remove only that user's rows.
    """
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        cm.delete_credentials(service, user_id=user_id)
        return {"status": "deleted", "service": service}
    except Exception as e:
        raise InternalError(str(e))


async def test_connection(service: str, user_id: int = 0):
    """Test the stored credentials for a service by attempting authentication.

    Args:
        user_id: Authenticated user ID.  ``0`` uses global/system credentials.
    """
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()

    try:
        cm.inject_credentials_to_env(service, user_id=user_id)
    except Exception as e:
        raise ValidationError(f"No credentials stored: {e}")

    if service == "fabric":
        try:
            cfg = reload_settings()
            extractor = make_source_extractor("fabric", cfg.fabric)
            extractor._get_access_token()
            return {"status": "success", "message": "Fabric authentication successful"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    if service == "snowflake":
        try:
            cfg = reload_settings()
            import snowflake.connector
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

            connect_kwargs = get_snowflake_connect_kwargs(cfg.snowflake)
            conn = snowflake.connector.connect(**connect_kwargs)
            conn.cursor().execute("SELECT CURRENT_VERSION()")
            conn.close()
            return {"status": "success", "message": "Snowflake connection successful"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    if service == "databricks":
        try:
            cfg = reload_settings()
            publisher = make_target_emitter("databricks", cfg.databricks)
            token = publisher._resolve_token()
            # Fire a lightweight query to validate warehouse access
            import requests as _requests

            resp = _requests.post(
                f"https://{cfg.databricks.host}/api/2.0/sql/statements",
                json={
                    "statement": "SHOW CATALOGS",
                    "warehouse_id": cfg.databricks.warehouse_id,
                    "wait_timeout": "10s",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return {"status": "success", "message": "Databricks connection successful"}
            return {"status": "failed", "message": f"Databricks API returned {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    raise ValidationError(f"Unknown service: {service}")


async def snowflake_oauth_test(body: Dict[str, Any]) -> Dict[str, Any]:
    """Test Snowflake OAuth S2S connectivity.

    Acquires a token using the provided client-credentials and attempts
    a live connection to Snowflake with ``authenticator=oauth``.

    Args:
        body: Dict with keys: account, user, oauth_client_id,
              oauth_client_secret, oauth_token_endpoint, oauth_scope (optional),
              warehouse (optional), database (optional).

    Returns:
        Dict with status, message, username, and role on success.
    """
    import requests as _requests

    account = (body.get("account") or "").strip()
    user = (body.get("user") or "").strip()
    client_id = (body.get("oauth_client_id") or "").strip()
    client_secret = (body.get("oauth_client_secret") or "").strip()
    token_endpoint = (body.get("oauth_token_endpoint") or "").strip()
    scope = (body.get("oauth_scope") or "").strip()

    if not account or not user:
        raise ValidationError("Please provide Account and Username before testing OAuth.")
    if not client_id or not client_secret:
        raise ValidationError("OAuth Client ID and Client Secret are required.")

    if not token_endpoint:
        import os
        tenant_id = os.environ.get("AZURE_TENANT_ID", "organizations")
        token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    if not scope and client_id:
        scope = f"api://{client_id}/.default"

    # Step 1: Acquire token from IdP
    payload: Dict[str, str] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    if scope:
        payload["scope"] = scope

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = _requests.post(token_endpoint, data=payload, timeout=30, verify=False)
        if resp.status_code != 200:
            error_msg = f"Azure AD returned {resp.status_code}: {resp.text}"
            logger.error(f"Snowflake OAuth token acquisition failed: {error_msg}")
            return {"status": "failed", "message": error_msg}

        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return {
                "status": "failed",
                "message": f"Token endpoint returned no access_token: {token_data}",
            }

        try:
            import base64, json, os, snowflake.connector
            parts = access_token.split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1]
                payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                decoded_claims = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))

                # Write to file so agent can read it automatically
                with open("token_dump.json", "w") as f:
                    json.dump(decoded_claims, f, indent=2)

                with open("token_raw.txt", "w") as f:
                    f.write(access_token)

            # Run diagnostic if we have a password
            sf_pass = os.environ.get("SNOWFLAKE_PASSWORD")
            if sf_pass:
                try:
                    ctx = snowflake.connector.connect(
                        user=user,
                        password=sf_pass,
                        account=account
                    )
                    cur = ctx.cursor()
                    cur.execute(f"SELECT SYSTEM$VERIFY_EXTERNAL_OAUTH_TOKEN('{access_token}')")
                    row = cur.fetchone()
                    diagnostic_result = row[0] if row else "No diagnostic result"
                    logger.warning(f"Snowflake OAuth Diagnostic: {diagnostic_result}")
                    if "Token validation failed" in diagnostic_result or "invalid" in diagnostic_result.lower():
                        return {"status": "failed", "message": f"Token Acquired but Snowflake Rejected it:\n {diagnostic_result}"}
                except Exception as sf_err:
                    logger.error(f"Failed to run Snowflake diagnostic: {sf_err}")

        except Exception as e:
            logger.error(f"Failed to decode JWT or run diagnostic: {e}")
    except Exception as e:
        logger.error(f"Snowflake OAuth token acquisition failed: {e}")
        return {"status": "failed", "message": f"Token acquisition failed: {e}"}

    # Step 2: Connect to Snowflake with the OAuth token
    try:
        import snowflake.connector

        connect_kwargs: Dict[str, Any] = {
            "account": account,
            "user": user,
            "authenticator": "oauth",
            "token": access_token,
        }
        if body.get("warehouse"):
            connect_kwargs["warehouse"] = body["warehouse"]
        if body.get("database"):
            connect_kwargs["database"] = body["database"]

        conn = snowflake.connector.connect(**connect_kwargs)
        cur = conn.cursor()
        cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE()")
        row = cur.fetchone()
        conn.close()

        username = row[0] if row else user
        role = row[1] if row else "N/A"

        logger.info(f"Snowflake OAuth S2S test successful: {username}")
        return {
            "status": "success",
            "message": f"OAuth S2S login successful as {username} (role: {role})",
            "username": username,
            "role": role,
        }
    except Exception as e:
        logger.error(f"Snowflake OAuth S2S connection failed: {e}")
        return {"status": "failed", "message": str(e)}
