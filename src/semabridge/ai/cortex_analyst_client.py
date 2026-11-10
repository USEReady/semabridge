"""Snowflake Cortex Analyst REST API client with Key-Pair JWT Authentication."""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import jwt

from semabridge.connectors.snowflake_connection import (
    _format_pem_key,
    _normalize_snowflake_account,
)
from semabridge.core.settings import SnowflakeConfig

logger = logging.getLogger("semabridge.ai.cortex_analyst_client")


class CortexAnalystUnavailableError(Exception):
    """Raised when Snowflake Cortex Analyst cannot be reached or configured."""
    pass


def generate_snowflake_keypair_jwt(
    account: str,
    user: str,
    private_key_pem: str,
    passphrase: Optional[str] = None,
) -> str:
    """Generate a Snowflake-compliant RS256 JWT using Key-Pair authentication.

    Strict formatting rules:
    - sub: <account>.<user> (all uppercase, dots replaced with hyphens in account)
    - iss: <account>.<user>.SHA256:<fingerprint> (all uppercase)
    """
    if not account or not user or not private_key_pem:
        raise ValueError("account, user, and private_key_pem are required for Key-Pair JWT.")

    norm_acc = _normalize_snowflake_account(account).upper().replace(".", "-")
    norm_usr = user.strip().upper()

    formatted_pem = _format_pem_key(private_key_pem)
    pwd = passphrase.encode("utf-8") if passphrase else None

    private_key = serialization.load_pem_private_key(
        formatted_pem.encode("utf-8"),
        password=pwd,
        backend=default_backend(),
    )

    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    sha256hash = hashlib.sha256()
    sha256hash.update(public_key_raw)
    public_key_fp = "SHA256:" + base64.b64encode(sha256hash.digest()).decode("utf-8")

    iss_claim = f"{norm_acc}.{norm_usr}.{public_key_fp}"
    sub_claim = f"{norm_acc}.{norm_usr}"

    now = datetime.now(timezone.utc)
    payload = {
        "iss": iss_claim,
        "sub": sub_claim,
        "iat": now,
        "exp": now + timedelta(minutes=55),
    }

    return jwt.encode(payload, private_key, algorithm="RS256")


def call_cortex_analyst(
    prompt: str,
    semantic_view: str,
    config: SnowflakeConfig,
) -> Dict[str, Any]:
    """Call Snowflake Cortex Analyst REST API with Key-Pair authentication.

    Args:
        prompt: Natural language question.
        semantic_view: Fully-qualified semantic view name ('"DB"."SCHEMA"."VIEW"').
        config: Snowflake configuration containing account, user, and private_key.

    Returns:
        Dict with 'generated_sql', 'explanation', 'request_id'.

    Raises:
        CortexAnalystUnavailableError: If credentials are missing or API fails.
    """
    private_key = config.private_key
    if not private_key:
        raise CortexAnalystUnavailableError("Snowflake key-pair private key is not configured.")

    account = _normalize_snowflake_account(config.account)
    if not account:
        raise CortexAnalystUnavailableError("Snowflake account is not configured.")

    user = config.user
    if not user:
        raise CortexAnalystUnavailableError("Snowflake user is not configured.")

    passphrase = config.private_key_passphrase.get_secret_value() if config.private_key_passphrase else None

    try:
        jwt_token = generate_snowflake_keypair_jwt(
            account=account,
            user=user,
            private_key_pem=private_key,
            passphrase=passphrase,
        )
    except Exception as exc:
        raise CortexAnalystUnavailableError(f"Failed to generate Key-Pair JWT: {exc}") from exc

    url = f"https://{account}.snowflakecomputing.com/api/v2/cortex/analyst/message"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    }
                ],
            }
        ],
        "semantic_view": semantic_view,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=45)
        if resp.status_code != 200:
            raise CortexAnalystUnavailableError(
                f"Cortex Analyst REST API error (HTTP {resp.status_code}): {resp.text[:300]}"
            )

        data = resp.json()
        content_items = data.get("message", {}).get("content", [])
        explanation = ""
        generated_sql = ""

        for item in content_items:
            itype = item.get("type")
            if itype == "text":
                explanation += item.get("text", "") + "\n"
            elif itype == "sql":
                generated_sql = item.get("statement", "")

        return {
            "generated_sql": generated_sql.strip(),
            "explanation": explanation.strip() or "Generated by Snowflake Cortex Analyst.",
            "request_id": data.get("request_id", ""),
            "raw_response": data,
        }
    except requests.RequestException as exc:
        raise CortexAnalystUnavailableError(f"Failed to reach Snowflake Cortex Analyst endpoint: {exc}") from exc

