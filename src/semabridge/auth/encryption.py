"""
Module: encryption
Purpose: Encrypt and decrypt sensitive token values used by Semabridge.
Responsibilities:
- Derive a Fernet key from environment-provided secret material.
- Provide helper functions for token encryption and decryption.
"""

import logging
import os
from typing import Optional
from cryptography.fernet import Fernet
import base64
import hashlib

_encryption_logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    """Derives a Fernet key from a configured secret key or environment variable."""
    secret = os.environ.get("SEMABRIDGE_ENCRYPTION_KEY")
    if not secret:
        # Allow dev mode to proceed with a warning, but never silently use a hardcoded key
        # without logging loudly. In production (AUTH_ENABLED=true) this is a hard failure.
        auth_enabled = os.environ.get("AUTH_ENABLED", "true").lower() == "true"
        if auth_enabled:
            raise RuntimeError(
                "SEMABRIDGE_ENCRYPTION_KEY environment variable must be set. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        # Dev/test mode: warn loudly but continue with a deterministic dev key
        _encryption_logger.warning(
            "SECURITY WARNING: SEMABRIDGE_ENCRYPTION_KEY is not set. "
            "Using an insecure dev key — DO NOT use this in production. "
            "Set AUTH_ENABLED=true or SEMABRIDGE_ENCRYPTION_KEY to suppress this warning."
        )
        secret = "dev-only-insecure-key-do-not-use-in-production"
    # Fernet requires a 32-url-safe-base64-encoded key. We use sha256 to ensure length/format:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode('utf-8')).digest())
    return Fernet(key)

def encrypt_token(plain_token: str) -> str:
    """Encrypts a string (e.g. auth token or password)."""
    if not plain_token:
        return ""
    f = _get_fernet()
    return f.encrypt(plain_token.encode('utf-8')).decode('utf-8')

def decrypt_token(encrypted_token: str) -> str:
    """Decrypts a previously encrypted string."""
    if not encrypted_token:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(encrypted_token.encode('utf-8')).decode('utf-8')
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to decrypt token: {e}")
        return ""
