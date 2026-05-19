"""Test masking utilities."""

import pytest
from semabridge.notifications.utils.masking import (
    mask_secret,
    mask_url,
    mask_api_key,
    mask_password,
    mask_config_json,
)


class TestMasking:
    """Test secret masking."""
    
    def test_mask_url(self):
        """Test URL masking."""
        url = "https://hooks.slack.com/services/T123456/B123456/abcdefgh12345678"
        masked = mask_url(url)
        
        # Should show scheme and host but not full path
        assert "hooks.slack.com" in masked
        assert "abcd" in masked  # Last 4 chars should be visible
        assert "T123456" not in masked  # Middle part should be hidden
    
    def test_mask_api_key(self):
        """Test API key masking."""
        key = "sk_live_12345678901234567890"
        masked = mask_api_key(key)
        
        # Should show only last 4 chars
        assert "1234" in masked
        assert "sk_live" not in masked
    
    def test_mask_password(self):
        """Test password masking."""
        password = "super_secret_password_123"
        masked = mask_password(password)
        
        # Should be fully redacted
        assert masked == "[REDACTED]"
        assert "secret" not in masked.lower()
    
    def test_mask_config_json(self):
        """Test masking config JSON."""
        config = {
            "webhook_url": "https://hooks.slack.com/services/T123/B456/abcd",
            "api_key": "sk_live_abc123def456",
            "password": "secret123",
            "username": "user@example.com",
        }
        
        masked = mask_config_json(config)
        
        # URLs and keys should be masked
        assert masked["webhook_url"] != config["webhook_url"]
        assert masked["api_key"] != config["api_key"]
        
        # Password should be fully redacted
        assert masked["password"] == "[REDACTED]"
        
        # Non-secret fields unchanged
        assert masked["username"] == "user@example.com"
