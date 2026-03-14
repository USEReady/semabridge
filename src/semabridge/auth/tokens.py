"""
JWT token creation and validation.

Tokens are HS256-signed using a secret key loaded from the
``JWT_SECRET_KEY`` environment variable (never hardcoded).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

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
_DEFAULT_EXPIRE_MINUTES = 60 * 24  # 24 hours


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
        expires_delta: Custom expiry; defaults to 24 h.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    # RFC 7519 §4.1.2: "sub" MUST be a StringOrURI
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=_DEFAULT_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
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
