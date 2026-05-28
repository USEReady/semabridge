"""
Tests for NotificationCrypto utility.
"""

import os
import json
import pytest
from unittest.mock import patch
from semabridge.notifications.utils.crypto import NotificationCrypto


def test_encryption_decryption_roundtrip():
    """Test encrypting and decrypting a standard string."""
    crypto = NotificationCrypto()
    secret = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
    
    ciphertext = crypto.encrypt(secret)
    assert ciphertext != secret
    assert len(ciphertext) > 0
    
    decrypted = crypto.decrypt(ciphertext)
    assert decrypted == secret


def test_custom_key_derivation():
    """Test key derivation from a user-provided secret passphrase."""
    with patch.dict(os.environ, {"NOTIFICATIONS_ENCRYPTION_KEY": "my_secure_passphrase_123!"}):
        crypto = NotificationCrypto()
        secret = "super_secret_smtp_password"
        
        ciphertext = crypto.encrypt(secret)
        assert ciphertext != secret
        
        decrypted = crypto.decrypt(ciphertext)
        assert decrypted == secret


def test_backward_compatibility_fallback():
    """Test that decrypting legacy plaintext config returns the plaintext unmodified."""
    crypto = NotificationCrypto()
    legacy_plaintext_config = json.dumps({"webhook_url": "https://hooks.slack.com/services/abc/123"})
    
    # Decrypting a plaintext string should fallback gracefully and return the plaintext as is
    decrypted = crypto.decrypt(legacy_plaintext_config)
    assert decrypted == legacy_plaintext_config


def test_empty_value_handling():
    """Test encrypt and decrypt on empty or None values."""
    crypto = NotificationCrypto()
    
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("") == ""
    assert crypto.decrypt(None) == ""
