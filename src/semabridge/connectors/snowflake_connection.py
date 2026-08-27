"""
Snowflake Connection Helper.

Centralised factory that builds ``snowflake.connector.connect()`` keyword
arguments from a :class:`SnowflakeConfig`, supporting **password**,
**key-pair**, and **SSO (externalbrowser)** authentication modes.

All auth-sensitive data (passwords, private keys) is read from
``SnowflakeConfig`` which itself loads values from ``os.environ`` — secrets
are *never* passed as function parameters.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from semabridge.core.settings import SnowflakeConfig
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def _normalize_snowflake_account(raw_account: str) -> str:
    """Normalize Snowflake account input into the connector-safe account id.

    Accepts common user-entered forms:
    - ``xy12345.us-east-1`` (already valid)
    - ``xy12345.us-east-1.snowflakecomputing.com``
    - ``https://xy12345.us-east-1.snowflakecomputing.com``
    """
    account = (raw_account or "").strip()
    if not account:
        return account

    parsed = urlparse(account)
    if parsed.scheme and parsed.netloc:
        account = parsed.netloc.strip()

    account = account.rstrip("/")
    if account.endswith(".snowflakecomputing.com"):
        account = account[: -len(".snowflakecomputing.com")]

    return account


def _format_pem_key(raw: str) -> str:
    """Ensure a PEM private key string has correct formatting.

    Handles keys that arrive as single-line blobs, with irregular whitespace
    from browser ``<textarea>`` inputs, or with literal ``\\n`` escape
    sequences instead of real newlines.

    Args:
        raw: Raw private key string (may be mangled).

    Returns:
        A well-formed PEM block with 64-char lines.

    Raises:
        ValueError: If the input is clearly not a PEM key (e.g. JSON blob).
    """
    import json as _json

    # Strip surrounding whitespace
    raw = raw.strip()

    # Detect JSON blobs — the credential store sometimes injects the entire
    # JSON credential bundle as the key value when env-var mapping goes wrong.
    # byte 0 == '{' (ASCII 123) is the symptom reported by cryptography.
    if raw.startswith("{"):
        try:
            bundle = _json.loads(raw)
            if isinstance(bundle, dict) and "private_key" in bundle:
                # Unwrap the actual key from the bundle and recurse
                return _format_pem_key(bundle["private_key"])
        except _json.JSONDecodeError:
            pass
        raise ValueError(
            "SNOWFLAKE_PRIVATE_KEY contains a JSON object instead of a PEM key. "
            "Ensure the credential store maps only the private_key field, not the "
            "entire credential bundle."
        )

    # Expand literal \\n escape sequences (common when keys are stored in
    # .env files or JSON as a single-line string with escaped newlines).
    if "\\n" in raw and "\n" not in raw:
        raw = raw.replace("\\n", "\n")

    # Already looks correct — return as-is
    if raw.startswith("-----BEGIN") and "\n" in raw:
        return raw

    # Remove any header/footer lines so we can re-wrap
    body = raw
    body = re.sub(r"-----BEGIN[A-Z \-]+-----", "", body)
    body = re.sub(r"-----END[A-Z \-]+-----", "", body)
    body = re.sub(r"\s+", "", body)  # collapse all whitespace

    # Re-wrap at 64 characters per RFC 7468
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]

    return (
        "-----BEGIN PRIVATE KEY-----\n"
        + "\n".join(lines)
        + "\n-----END PRIVATE KEY-----"
    )


def _parse_private_key(
    pem_text: str,
    passphrase: Optional[str] = None,
) -> bytes:
    """Parse a PEM private key string into DER bytes for Snowflake.

    Args:
        pem_text: PEM-encoded private key.
        passphrase: Optional passphrase for encrypted keys.

    Returns:
        DER-encoded private key bytes.

    Raises:
        ValueError: If the key cannot be parsed.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    formatted = _format_pem_key(pem_text)

    pwd = passphrase.encode("utf-8") if passphrase else None

    try:
        private_key = serialization.load_pem_private_key(
            formatted.encode("utf-8"),
            password=pwd,
            backend=default_backend(),
        )
    except Exception as exc:
        raise ValueError(
            f"Unable to parse PEM private key: {exc}. "
            "Ensure the key is a valid PKCS#8 PEM block."
        ) from exc

    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _acquire_oauth_token(config: SnowflakeConfig) -> str:
    """Acquire an OAuth access token via the client-credentials grant.

    Posts to the configured token endpoint with the client ID and secret
    to obtain a short-lived access token for Snowflake External OAuth.

    Args:
        config: Snowflake configuration containing OAuth parameters.

    Returns:
        The access token string.

    Raises:
        ValueError: If required OAuth parameters are missing or the
            token request fails.
    """
    import requests as _requests

    # Resolve oauth_token_endpoint (do NOT mutate config)
    oauth_token_endpoint = config.oauth_token_endpoint
    if not oauth_token_endpoint:
        import os
        tenant_id = os.environ.get("AZURE_TENANT_ID", "organizations")
        oauth_token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    if not config.oauth_client_id:
        raise ValueError(
            "OAuth auth selected but SNOWFLAKE_OAUTH_CLIENT_ID is not set."
        )
    if not config.oauth_client_secret:
        raise ValueError(
            "OAuth auth selected but SNOWFLAKE_OAUTH_CLIENT_SECRET is not set."
        )

    # Resolve oauth_scope (do NOT mutate config)
    oauth_scope = config.oauth_scope
    if not oauth_scope:
        oauth_scope = f"api://{config.oauth_client_id}/.default"

    payload: Dict[str, str] = {
        "client_id": config.oauth_client_id,
        "client_secret": config.oauth_client_secret.get_secret_value(),
        "grant_type": "client_credentials",
        "scope": oauth_scope,
    }

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = _requests.post(
            oauth_token_endpoint,
            data=payload,
            timeout=30,
            verify=False
        )
        resp.raise_for_status()
        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError(
                f"Token endpoint returned no access_token. Response: {token_data}"
            )
        logger.info(
            "OAuth S2S token acquired (expires_in=%s)",
            token_data.get("expires_in", "unknown"),
        )
        return access_token
    except Exception as exc:
        raise ValueError(
            f"OAuth token acquisition failed from {oauth_token_endpoint}: {exc}"
        ) from exc


def get_snowflake_connect_kwargs(config: SnowflakeConfig) -> Dict[str, Any]:
    """Build keyword arguments for ``snowflake.connector.connect()``.

    Examines ``config.auth_type`` to determine which credentials to
    include:

    * ``password``  — uses ``config.password``
    * ``keypair``   — parses ``config.private_key`` into DER bytes
    * ``oauth``     — acquires an OAuth token via client-credentials grant

    Args:
        config: Fully resolved Snowflake configuration.

    Returns:
        A dict ready to be unpacked into ``snowflake.connector.connect(**kw)``.

    Raises:
        ValueError: When required credentials for the chosen auth mode
            are missing or invalid, or when a field contains a corrupted value
            (e.g. a raw .env comment string such as ``# SNOWFLAKE_USER=...``).
    """
    # --- Guard: reject corrupted credential values before hitting the network ---
    # A value like '# SNOWFLAKE_USER=SABIHA' is a raw .env comment that ended up
    # in the credential store.  Sending it to Snowflake produces a cryptic
    # "JWT token is invalid" (or "Failed to connect") error with no hint about
    # the real cause.  Catching it here gives an immediately actionable message.
    for field_name in ("user", "account", "warehouse", "database"):
        val: str = getattr(config, field_name, None) or ""
        val_stripped = val.strip()
        if val_stripped.startswith("#") or (
            "=" in val_stripped and not val_stripped.startswith("-----")
        ):
            raise ValueError(
                f"SNOWFLAKE_{field_name.upper()} has an invalid value: {val!r}. "
                "This looks like a raw .env comment or key=value string. "
                "Update the credential in Settings \u2192 Connections and retry."
            )

    normalized_account = _normalize_snowflake_account(config.account)
    if not normalized_account:
        raise ValueError("SNOWFLAKE_ACCOUNT resolved to an empty value after normalization.")

    kwargs: Dict[str, Any] = {
        "user": config.user,
        "account": normalized_account,
        "schema": config.schema_name,
    }
    # Only include warehouse/database/schema when they are set — discovery
    # calls (SHOW WAREHOUSES, SHOW DATABASES) work without them, and passing
    # None causes Snowflake connector errors on fresh accounts.
    if config.warehouse:
        kwargs["warehouse"] = config.warehouse
    if config.database:
        kwargs["database"] = config.database

    if config.role:
        kwargs["role"] = config.role

    auth = (config.auth_type or "password").lower().strip()

    if auth == "keypair":
        if not config.private_key:
            raise ValueError(
                "Key Pair auth selected but SNOWFLAKE_PRIVATE_KEY is not set."
            )
        passphrase: Optional[str] = None
        if config.private_key_passphrase:
            passphrase = config.private_key_passphrase.get_secret_value()

        kwargs["private_key"] = _parse_private_key(
            config.private_key,
            passphrase=passphrase,
        )
        logger.info("Using Key Pair authentication for Snowflake")

    elif auth == "oauth":
        token = _acquire_oauth_token(config)
        kwargs["authenticator"] = "oauth"
        kwargs["token"] = token
        logger.info("Using OAuth S2S authentication for Snowflake")

    elif auth == "externalbrowser":
        kwargs["authenticator"] = "externalbrowser"
        logger.info("Using externalbrowser authentication for Snowflake")

    else:
        # Default: password auth
        if not config.password:
            raise ValueError(
                "Password auth selected but SNOWFLAKE_PASSWORD is not set."
            )
        kwargs["password"] = config.password.get_secret_value()
        logger.info("Using password authentication for Snowflake")

    return kwargs
