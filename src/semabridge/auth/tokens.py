"""
JWT token creation and validation.

Tokens are HS256-signed using a secret key loaded from the
``JWT_SECRET_KEY`` environment variable (never hardcoded).

Supports two token types:
    - **Access tokens** (short-lived, 15 min) — carried in Authorization header
    - **Refresh tokens** (long-lived, 7 days) — stored in HttpOnly cookie,
      one-time-use with rotation
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

try:
    from jose import JWTError, jwt
except ImportError:  # pragma: no cover
    raise ImportError(
        "python-jose is required for JWT auth. "
        "Run: pip install 'python-jose[cryptography]'"
    ) from None

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Defaults ----------------------------------------------------------------
_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_MINUTES = 15
_REFRESH_TOKEN_EXPIRE_DAYS = 7


def _get_secret_key() -> str:
    """Read ``JWT_SECRET_KEY`` from the environment.

    Raises:
        RuntimeError: If the variable is missing or empty.
    """
    key = os.environ.get("JWT_SECRET_KEY", "")
    if not key:
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable is not set. "
            "Add it to your .env file."
        )
    return key


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token.

    Args:
        data: Payload claims (must include ``sub`` for user identity).
              ``sub`` is coerced to ``str`` (required by RFC 7519).
        expires_delta: Custom expiry; defaults to 15 min.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    # RFC 7519 §4.1.2: "sub" MUST be a StringOrURI
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, _get_secret_key(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT access token.

    Args:
        token: The raw JWT string.

    Returns:
        The decoded payload dictionary.

    Raises:
        JWTError: If the token is invalid or expired.
    """
    return jwt.decode(token, _get_secret_key(), algorithms=[_ALGORITHM])


# ── Refresh Token Support ───────────────────────────────────────────────

def generate_refresh_token() -> str:
    """Generate a cryptographically secure random refresh token string.

    Returns:
        A 64-character hex token.
    """
    return secrets.token_hex(32)


def hash_refresh_token(raw_token: str) -> str:
    """SHA-256 hash of a refresh token for safe DB storage.

    Args:
        raw_token: The raw refresh token string.

    Returns:
        Hex-encoded SHA-256 hash (64 chars).
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def get_refresh_token_expiry() -> datetime:
    """Return the expiry datetime for a new refresh token.

    Returns:
        UTC datetime 7 days from now.
    """
    return datetime.now(timezone.utc) + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS)


def create_token_pair(user_id: int, username: str, role: str = "user") -> Tuple[str, str]:
    """Create both an access token and a refresh token for a user.

    Args:
        user_id: The user's database ID.
        username: The user's username (included in JWT claims).
        role: The user's role (included in JWT claims).

    Returns:
        Tuple of (access_token, raw_refresh_token).
        The raw refresh token should be set as an HttpOnly cookie.
    """
    access_token = create_access_token({
        "sub": str(user_id),
        "username": username,
        "role": role,
    })
    refresh_token = generate_refresh_token()
    return access_token, refresh_token
