"""
Automatic token refresh for Account-based authentication.

Before every pipeline execution this module checks whether the Account's
access token is expired (or about to expire within a 5-minute grace window).
If yes, it uses the stored refresh_token to obtain a fresh access token
from the appropriate IdP (MSAL for Fabric, OAuth2 for Databricks).

Usage::

    from semabridge.auth.token_refresher import ensure_valid_token

    token = ensure_valid_token(account, db)
    # → returns a valid access_token string, refreshing if needed

Raises ``TokenExpiredError`` if the refresh token is also invalid and
the user must re-authenticate interactively.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from semabridge.auth.encryption import decrypt_token, encrypt_token
from semabridge.repository.orm.models import Account
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Grace window in seconds — refresh 5 minutes before actual expiry.
_REFRESH_GRACE_SECONDS = 300


class TokenExpiredError(Exception):
    """Raised when both access and refresh tokens are expired/invalid.

    Attributes:
        connector_type: The connector that failed (FABRIC, DATABRICKS, etc.).
        identity: The user identity (email or tag) that needs re-auth.
    """

    def __init__(self, connector_type: str, identity: str) -> None:
        self.connector_type = connector_type
        self.identity = identity
        super().__init__(
            f"{connector_type} token expired for '{identity}'. "
            "Please re-authenticate via the Settings → Connections page."
        )


def _is_token_expired(account: Account) -> bool:
    """Return True if the account's access token is expired or about to expire."""
    if not account.token_expires_at:
        # No expiry recorded — assume still valid (e.g. PAT or key-pair auth)
        return False

    now = datetime.now(tz=timezone.utc)
    expires_at = account.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    remaining = (expires_at - now).total_seconds()
    return remaining < _REFRESH_GRACE_SECONDS


def _refresh_fabric_token(account: Account, db: Session) -> str:
    """Refresh a Fabric access token using MSAL's refresh_token grant.

    Args:
        account: The Fabric Account row.
        db: Active DB session for writing the new token.

    Returns:
        Fresh access_token string.

    Raises:
        TokenExpiredError: If the refresh token is also expired.
    """
    if not account.refresh_token:
        raise TokenExpiredError("FABRIC", account.identity_email or account.tag)

    from msal import PublicClientApplication
    import os

    stored_refresh = decrypt_token(account.refresh_token)

    # Use the same MSAL client_id as the interactive login flow.
    client_id = os.environ.get("FABRIC_CLIENT_ID", "")

    if not client_id:
        try:
            from semabridge.repository.credential_manager import CredentialManager
            cm = CredentialManager()
            creds = cm.get_credentials("fabric")
            client_id = creds.get("client_id", "")
        except Exception:
            pass

    if not client_id:
        raise TokenExpiredError("FABRIC", account.identity_email or account.tag)

    scopes = ["https://analysis.windows.net/powerbi/api/.default"]
    app = PublicClientApplication(
        client_id, authority="https://login.microsoftonline.com/organizations"
    )

    result = app.acquire_token_by_refresh_token(stored_refresh, scopes=scopes)

    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "unknown"))
        logger.warning("Fabric token refresh failed: %s", error)
        raise TokenExpiredError("FABRIC", account.identity_email or account.tag)

    # Check if the existing token is a JSON bundle
    try:
        import json
        orig_decrypted = decrypt_token(account.encrypted_token)
        orig_bundle = json.loads(orig_decrypted)
        if isinstance(orig_bundle, dict):
            orig_bundle["access_token"] = result["access_token"]
            if result.get("refresh_token"):
                orig_bundle["refresh_token"] = result["refresh_token"]
            orig_bundle["expires_in"] = result.get("expires_in", 3600)
            account.encrypted_token = encrypt_token(json.dumps(orig_bundle))
        else:
            account.encrypted_token = encrypt_token(result["access_token"])
    except (json.JSONDecodeError, TypeError):
        account.encrypted_token = encrypt_token(result["access_token"])

    if result.get("refresh_token"):
        account.refresh_token = encrypt_token(result["refresh_token"])

    expires_in = int(result.get("expires_in", 3600))
    account.token_expires_at = datetime.fromtimestamp(
        time.time() + expires_in, tz=timezone.utc
    )
    db.commit()

    logger.info(
        "Fabric token refreshed for %s, expires in %ds",
        account.identity_email,
        expires_in,
    )
    return result["access_token"]


def _refresh_databricks_token(account: Account, db: Session) -> str:
    """Refresh a Databricks access token.

    For M2M (Service Principal) auth, uses the client_credentials grant
    which never requires a stored refresh token.
    For interactive/U2M auth, uses the OAuth2 refresh_token grant.

    Args:
        account: The Databricks Account row.
        db: Active DB session.

    Returns:
        Fresh access_token string.

    Raises:
        TokenExpiredError: If refresh is not possible.
    """
    auth_type = account.auth_type or ""

    # M2M / Service Principal — use client_credentials grant (never expires)
    if auth_type in ("m2m", "service_principal", "client_credentials"):
        return _databricks_client_credentials(account, db)

    # Interactive / U2M — use refresh_token grant
    if not account.refresh_token:
        raise TokenExpiredError("DATABRICKS", account.identity_email or account.tag)

    import os
    import requests

    host = os.environ.get("DATABRICKS_HOST", "")
    if not host:
        try:
            from semabridge.repository.credential_manager import CredentialManager
            cm = CredentialManager()
            creds = cm.get_credentials("databricks")
            host = creds.get("host", "")
        except Exception:
            pass

    if not host:
        raise TokenExpiredError("DATABRICKS", account.identity_email or account.tag)

    stored_refresh = decrypt_token(account.refresh_token)
    token_url = f"https://{host.rstrip('/')}/oidc/v1/token"

    resp = requests.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": stored_refresh,
            "client_id": os.environ.get("DATABRICKS_CLIENT_ID", ""),
        },
        timeout=30,
    )

    if resp.status_code != 200:
        logger.warning("Databricks token refresh failed: %s", resp.text)
        raise TokenExpiredError("DATABRICKS", account.identity_email or account.tag)

    data = resp.json()
    account.encrypted_token = encrypt_token(data["access_token"])
    if data.get("refresh_token"):
        account.refresh_token = encrypt_token(data["refresh_token"])

    expires_in = int(data.get("expires_in", 3600))
    account.token_expires_at = datetime.fromtimestamp(
        time.time() + expires_in, tz=timezone.utc
    )
    db.commit()

    logger.info(
        "Databricks token refreshed for %s, expires in %ds",
        account.identity_email,
        expires_in,
    )
    return data["access_token"]


def _databricks_client_credentials(account: Account, db: Session) -> str:
    """Obtain a Databricks token using M2M client_credentials grant.

    This flow never requires a stored refresh token because the
    client_id + client_secret are sufficient to issue new tokens.
    """
    import os
    import requests

    host = os.environ.get("DATABRICKS_HOST", "")
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")

    if not all([host, client_id, client_secret]):
        raise TokenExpiredError("DATABRICKS", account.identity_email or account.tag)

    token_url = f"https://{host.rstrip('/')}/oidc/v1/token"
    resp = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "all-apis",
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise TokenExpiredError("DATABRICKS", account.identity_email or account.tag)

    data = resp.json()
    account.encrypted_token = encrypt_token(data["access_token"])
    expires_in = int(data.get("expires_in", 3600))
    account.token_expires_at = datetime.fromtimestamp(
        time.time() + expires_in, tz=timezone.utc
    )
    db.commit()

    logger.info("Databricks M2M token obtained, expires in %ds", expires_in)
    return data["access_token"]


def _refresh_snowflake_oauth_token(account: Account, db: Session) -> str:
    """Re-acquire a Snowflake OAuth M2M token via client_credentials grant.

    Snowflake External OAuth uses short-lived JWTs issued by the IdP.
    Industry best practice (dbt, Fivetran, Airbyte): never store the
    access token — simply re-acquire it from the token endpoint using
    the stored client credentials.

    Args:
        account: The Snowflake Account row containing encrypted OAuth creds.
        db: Active DB session for persisting the refreshed token.

    Returns:
        Fresh access_token string.

    Raises:
        TokenExpiredError: If the credential bundle is missing OAuth fields.
    """
    import json
    import requests as _requests

    if not account.encrypted_token:
        raise TokenExpiredError("SNOWFLAKE", account.identity_email or account.tag)

    try:
        decrypted = decrypt_token(account.encrypted_token)
        bundle = json.loads(decrypted)
    except (json.JSONDecodeError, TypeError):
        raise TokenExpiredError("SNOWFLAKE", account.identity_email or account.tag)

    client_id = bundle.get("oauth_client_id", "")
    client_secret = bundle.get("oauth_client_secret", "")
    token_endpoint = bundle.get("oauth_token_endpoint", "")
    scope = bundle.get("oauth_scope", "")

    if not all([client_id, client_secret, token_endpoint]):
        raise TokenExpiredError("SNOWFLAKE", account.identity_email or account.tag)

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    if scope:
        payload["scope"] = scope

    try:
        resp = _requests.post(token_endpoint, data=payload, timeout=30)
        resp.raise_for_status()
        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise TokenExpiredError("SNOWFLAKE", account.identity_email or account.tag)
    except TokenExpiredError:
        raise
    except Exception as exc:
        logger.warning("Snowflake OAuth token acquisition failed: %s", exc)
        raise TokenExpiredError("SNOWFLAKE", account.identity_email or account.tag)

    # Persist the fresh token back into the credential bundle
    bundle["access_token"] = access_token
    account.encrypted_token = encrypt_token(json.dumps(bundle))

    expires_in = int(token_data.get("expires_in", 3600))
    account.token_expires_at = datetime.fromtimestamp(
        time.time() + expires_in, tz=timezone.utc
    )
    db.commit()

    logger.info(
        "Snowflake OAuth M2M token acquired for %s, expires in %ds",
        account.identity_email or account.tag,
        expires_in,
    )
    return access_token


def ensure_valid_token(account: Account, db: Session) -> str:
    """Return a valid access token for the given Account, refreshing if needed.

    This is the main entry point. It checks the token expiry and
    transparently refreshes when necessary.

    For connectors that don't expire (Snowflake key-pair, PATs), the
    stored token is returned directly.

    Args:
        account: The Account ORM row.
        db: Active SQLAlchemy session for persisting refreshed tokens.

    Returns:
        A valid, decrypted access token string.

    Raises:
        TokenExpiredError: If the token cannot be refreshed automatically.
    """
    connector = account.connector_type.upper()

    # Check if token needs refresh
    if _is_token_expired(account):
        logger.info(
            "Token expired for %s/%s — attempting refresh",
            connector,
            account.identity_email or account.tag,
        )

        if connector == "FABRIC":
            return _refresh_fabric_token(account, db)
        elif connector == "DATABRICKS":
            return _refresh_databricks_token(account, db)
        elif connector == "SNOWFLAKE":
            # Only OAuth accounts need token refresh — key-pair/password
            # accounts don't store short-lived tokens.
            return _refresh_snowflake_oauth_token(account, db)

    # Token is still valid — decrypt and return
    if account.encrypted_token:
        decrypted = decrypt_token(account.encrypted_token)
        # Handle cases where the token is stored as a JSON credential bundle
        try:
            import json
            bundle = json.loads(decrypted)
            if isinstance(bundle, dict) and "access_token" in bundle:
                return bundle["access_token"]
        except (json.JSONDecodeError, TypeError):
            pass
        
        return decrypted

    raise TokenExpiredError(connector, account.identity_email or account.tag)
