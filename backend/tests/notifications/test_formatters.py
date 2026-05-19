"""Test formatters."""

import pytest
from semabridge.notifications.models import NotificationEvent
from semabridge.notifications.constants import NotificationLevel
from semabridge.notifications.formatters.slack_formatter import SlackFormatter
from semabridge.notifications.formatters.email_formatter import EmailFormatter
from semabridge.notifications.formatters.webhook_formatter import WebhookFormatter


class TestSlackFormatter:
    """Test Slack message formatting."""
    
    def test_basic_formatting(self):
        """Test basic Slack formatting."""
        formatter = SlackFormatter()
        
        event = NotificationEvent(
            sync_job_id="job_123",
            level=NotificationLevel.ERROR,
            title="Sync Error",
            message="An error occurred",
        )
        
        payload = formatter.format(event, {})
        
        # Should have blocks
        assert "blocks" in payload
        assert len(payload["blocks"]) > 0
        
        # Should have text fallback
        assert "text" in payload
        assert "Sync Error" in payload["text"]
    
    def test_message_truncation(self):
        """Test message truncation for Slack limits."""
        formatter = SlackFormatter()
        
        # Very long message
        long_message = "x" * 5000
        
        event = NotificationEvent(
            level=NotificationLevel.ERROR,
            title="Error",
            message=long_message,
        )
        
        payload = formatter.format(event, {})
        
        # Should have truncation notice
        formatted_text = str(payload)
        if len(long_message) > 3000:
            assert "truncat" in formatted_text.lower() or "full" in formatted_text.lower()


class TestEmailFormatter:
    """Test Email formatting."""
    
    def test_email_formatting(self):
        """Test email formatting."""
        formatter = EmailFormatter()
        
        event = NotificationEvent(
            sync_job_id="job_123",
            level=NotificationLevel.ERROR,
            title="Sync Error",
            message="An error occurred",
        )
        
        payload = formatter.format(event, {"from_address": "test@example.com"})
        
        # Should have subject and body
        assert "subject" in payload
        assert "html" in payload
        assert "plaintext" in payload
        
        # Subject should include level and title
        assert "ERROR" in payload["subject"] or "error" in payload["subject"].lower()
        assert "Sync Error" in payload["subject"]


class TestWebhookFormatter:
    """Test Webhook formatting."""
    
    def test_webhook_formatting(self):
        """Test webhook JSON formatting."""
        formatter = WebhookFormatter()
        
        event = NotificationEvent(
            sync_job_id="job_123",
            correlation_id="corr_xyz",
            level=NotificationLevel.WARNING,
            title="Warning",
            message="Test warning",
        )
        
        payload = formatter.format(event, {})
        
        # Should be JSON-serializable
        assert payload["event_id"]
        assert payload["correlation_id"] == "corr_xyz"
        assert payload["title"] == "Warning"
        assert "WARNING" in payload["level"] or "warning" in payload["level"].lower()
