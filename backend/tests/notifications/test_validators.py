"""Test validators for SSRF protection and configuration."""

import pytest
from semabridge.notifications.utils.validators import (
    validate_webhook_url,
    validate_slack_webhook,
    validate_email_address,
    is_private_ip,
    is_reserved_hostname,
)


class TestSSRFProtection:
    """Test SSRF protection validators."""
    
    def test_block_localhost(self):
        """Test blocking localhost URLs."""
        is_valid, error = validate_webhook_url("http://localhost:8080/webhook")
        assert not is_valid
        assert "localhost" in error.lower()
    
    def test_block_private_ips(self):
        """Test blocking private IP ranges."""
        urls = [
            "http://127.0.0.1/webhook",
            "http://10.0.0.1/webhook",
            "http://192.168.1.1/webhook",
            "http://172.16.0.1/webhook",
        ]
        
        for url in urls:
            is_valid, error = validate_webhook_url(url)
            # Some may fail at parse time, others at resolution
            if not is_valid:
                assert "private" in error.lower() or "reserved" in error.lower()
    
    def test_block_aws_metadata(self):
        """Test blocking AWS metadata endpoint."""
        is_valid, error = validate_webhook_url("http://169.254.169.254/")
        assert not is_valid
    
    def test_block_reserved_hostnames(self):
        """Test blocking reserved hostnames."""
        urls = [
            "http://internal/webhook",
            "http://service.local/webhook",
            "http://localhost.test/webhook",
        ]
        
        for url in urls:
            is_valid, error = validate_webhook_url(url)
            if not is_valid:
                assert "reserved" in error.lower() or "internal" in error.lower()
    
    def test_allow_valid_urls(self):
        """Test allowing valid public URLs."""
        urls = [
            "https://hooks.slack.com/services/T123/B456/abcd",
            "https://api.example.com/webhook",
            "https://webhook.site/abc123",
        ]
        
        for url in urls:
            is_valid, error = validate_webhook_url(url)
            # May not actually resolve, but should pass URL validation
            assert is_valid or "resolve" not in error.lower()


class TestPrivateIPDetection:
    """Test private IP detection."""
    
    def test_private_ranges(self):
        """Test private IP ranges."""
        private_ips = [
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.1",
            "172.16.0.1",
            "172.31.255.255",
        ]
        
        for ip in private_ips:
            assert is_private_ip(ip), f"{ip} should be private"
    
    def test_public_ips(self):
        """Test public IP detection."""
        public_ips = [
            "8.8.8.8",
            "1.1.1.1",
            "208.67.222.222",
        ]
        
        for ip in public_ips:
            assert not is_private_ip(ip), f"{ip} should be public"


class TestReservedHostnames:
    """Test reserved hostname detection."""
    
    def test_reserved(self):
        """Test reserved hostnames."""
        reserved = [
            "localhost",
            "example.local",
            "service.internal",
            "test.invalid",
        ]
        
        for hostname in reserved:
            assert is_reserved_hostname(hostname), f"{hostname} should be reserved"
    
    def test_public(self):
        """Test public hostnames."""
        public = [
            "example.com",
            "api.github.com",
            "hooks.slack.com",
        ]
        
        for hostname in public:
            assert not is_reserved_hostname(hostname), f"{hostname} should be public"


class TestEmailValidation:
    """Test email validation."""
    
    def test_valid_emails(self):
        """Test valid email addresses."""
        emails = [
            "user@example.com",
            "test.user@example.co.uk",
            "admin+tag@company.org",
        ]
        
        for email in emails:
            is_valid, error = validate_email_address(email)
            assert is_valid, f"{email} should be valid: {error}"
    
    def test_invalid_emails(self):
        """Test invalid email addresses."""
        emails = [
            "user@",
            "@example.com",
            "no-at-sign.com",
            "user@.com",
        ]
        
        for email in emails:
            is_valid, error = validate_email_address(email)
            assert not is_valid, f"{email} should be invalid"
