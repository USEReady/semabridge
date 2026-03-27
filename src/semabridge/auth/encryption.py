import os
from typing import Optional
from cryptography.fernet import Fernet
import base64
import hashlib

def _get_fernet() -> Fernet:
    """Derives a Fernet key from a configured secret key or environment variable."""
    # Attempt to use a strong secret, fallback to a local deterministic key for dev
    secret = os.environ.get("SEMABRIDGE_ENCRYPTION_KEY", "default-insecure-dev-key")
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
