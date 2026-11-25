"""Bearer-token helpers."""

from __future__ import annotations

import base64
import json


def jwt_expiry(token: str) -> float:
    """Return the ``exp`` claim (epoch seconds) of a JWT access token.

    Reads the payload only (no signature check): the value is used to decide
    when to refresh, never to trust the token. Raises ValueError with the
    reason when the token is not a JWT or has no numeric ``exp``.
    """
    parts = str(token or "").split(".")
    if len(parts) != 3 or not parts[1]:
        raise ValueError("access token is not a JWT (expected header.payload.signature)")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception as exc:  # noqa: BLE001 - re-raised with reason
        raise ValueError(f"access token payload is not valid base64 JSON: {exc}") from exc
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, (int, float)) or exp <= 0:
        raise ValueError("access token has no numeric 'exp' claim")
    return float(exp)
