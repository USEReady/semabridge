"""
Symmetric encryption utility for channel configurations and secrets.
"""

import os
import base64
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Fallback key for local development and testing (never use in production)
DEFAULT_KEY = base64.urlsafe_b64encode(b"semabridge_dev_key_32_bytes!!!!!")


class NotificationCrypto:
    """
    Handles symmetric encryption-at-rest for sensitive channel configurations.
    Uses Fernet (AES-128 in CBC mode with HMAC-SHA256).
    """

    def __init__(self):
        # We check both NOTIFICATIONS_ENCRYPTION_KEY and the legacy NOTIFICATION_SECRET_KEY
        key_str = os.getenv("NOTIFICATIONS_ENCRYPTION_KEY") or os.getenv("NOTIFICATION_SECRET_KEY")
        if not key_str:
            logger.warning(
                "NOTIFICATIONS_ENCRYPTION_KEY environment variable is not set. "
                "Falling back to default development key. Secrets are NOT SECURE."
            )
            key = DEFAULT_KEY
        else:
            try:
                # Ensure the key is in correct Fernet format (32 url-safe base64-encoded bytes)
                key = key_str.encode()
                Fernet(key)
            except Exception:
                # Derive a Fernet key from the user-provided password string using PBKDF2
                logger.info("Deriving Fernet key from secret string using PBKDF2 and SHA256")
                salt = b"semabridge_notifications_salt"
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                key = base64.urlsafe_b64encode(kdf.derive(key_str.encode()))

        self.fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.
        
        Args:
            plaintext: Plaintext to encrypt
            
        Returns:
            Encrypted base64 string
        """
        if not plaintext:
            return ""
        try:
            return self.fernet.encrypt(plaintext.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a ciphertext string.
        Supports graceful fallback to legacy plaintext if decryption fails or if it's already plaintext.
        
        Args:
            ciphertext: Ciphertext to decrypt
            
        Returns:
            Decrypted plaintext string
        """
        if not ciphertext:
            return ""
        try:
            # Try to decrypt using Fernet
            return self.fernet.decrypt(ciphertext.encode()).decode()
        except Exception:
            # Fallback for backward compatibility with existing plaintext configs
            # If ciphertext is actually a plaintext JSON, return it directly
            return ciphertext
