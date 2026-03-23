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

from semabridge.core.settings import SnowflakeConfig
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def _format_pem_key(raw: str) -> str:
    """Ensure a PEM private key string has correct formatting.

    Handles keys that arrive as single-line blobs or with irregular
    whitespace from browser ``<textarea>`` inputs.

    Args:
        raw: Raw private key string (may be mangled).

    Returns:
        A well-formed PEM block with 64-char lines.
    """
    # Strip surrounding whitespace
    raw = raw.strip()

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


def get_snowflake_connect_kwargs(config: SnowflakeConfig) -> Dict[str, Any]:
    """Build keyword arguments for ``snowflake.connector.connect()``.

    Examines ``config.auth_type`` to determine which credentials to
    include:

    * ``password``  — uses ``config.password``
    * ``keypair``   — parses ``config.private_key`` into DER bytes
    * ``externalbrowser`` — sets ``authenticator='externalbrowser'``

    Args:
        config: Fully resolved Snowflake configuration.

    Returns:
        A dict ready to be unpacked into ``snowflake.connector.connect(**kw)``.

    Raises:
        ValueError: When required credentials for the chosen auth mode
            are missing or invalid.
    """
    kwargs: Dict[str, Any] = {
        "user": config.user,
        "account": config.account,
        "warehouse": config.warehouse,
        "database": config.database,
        "schema": config.schema_name,
    }

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

    elif auth == "externalbrowser":
        kwargs["authenticator"] = "externalbrowser"
        logger.info("Using SSO (externalbrowser) authentication for Snowflake")

    else:
        # Default: password auth
        if not config.password:
            raise ValueError(
                "Password auth selected but SNOWFLAKE_PASSWORD is not set."
            )
        kwargs["password"] = config.password.get_secret_value()
        logger.info("Using password authentication for Snowflake")

    return kwargs
