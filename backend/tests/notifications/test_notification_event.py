"""Test notification event model and fingerprinting."""

import pytest
from uuid import uuid4
from semabridge.notifications.models import NotificationEvent
from semabridge.notifications.constants import NotificationLevel, level_to_string, matches_level


class TestNotificationEvent:
    """Test NotificationEvent model."""
    
    def test_create_event(self):
        """Test creating a notification event."""
        event = NotificationEvent(
            sync_job_id="job_123",
            type="sync_completion",
            level=NotificationLevel.ERROR,
            title="Sync Failed",
            message="Test error",
        )
        
        assert event.sync_job_id == "job_123"
        assert event.level == NotificationLevel.ERROR
        assert event.title == "Sync Failed"
    
    def test_fingerprint_generation(self):
        """Test fingerprint generation for deduplication."""
        event = NotificationEvent(
            sync_job_id="job_123",
            level=NotificationLevel.ERROR,
            title="Error",
        )
        
        # Same channel should produce same fingerprint
        fp1 = event.generate_fingerprint("channel_1")
        fp2 = event.generate_fingerprint("channel_1")
        assert fp1 == fp2
        
        # Different channel should produce different fingerprint
        fp3 = event.generate_fingerprint("channel_2")
        assert fp1 != fp3
        
        # Fingerprint should be deterministic
        event2 = NotificationEvent(
            sync_job_id="job_123",
            level=NotificationLevel.ERROR,
            title="Error",
        )
        assert event.generate_fingerprint("channel_1") == event2.generate_fingerprint("channel_1")
    
    def test_event_serialization(self):
        """Test event to/from dict."""
        event = NotificationEvent(
            sync_job_id="job_123",
            correlation_id="corr_xyz",
            level=NotificationLevel.WARNING,
            title="Warning",
            message="Test",
        )
        
        # Serialize
        data = event.to_dict()
        assert data["sync_job_id"] == "job_123"
        assert data["correlation_id"] == "corr_xyz"
        
        # Deserialize
        event2 = NotificationEvent.from_dict(data)
        assert event2.sync_job_id == event.sync_job_id
        assert event2.correlation_id == event.correlation_id


class TestNotificationLevel:
    """Test level helpers."""
    
    def test_matches_level_bitmask(self):
        """Test level matching with bitmasks."""
        # Channel accepts ERROR | WARNING
        channel_mask = NotificationLevel.ERROR | NotificationLevel.WARNING
        
        # ERROR event matches
        assert matches_level(NotificationLevel.ERROR, channel_mask)
        # WARNING event matches
        assert matches_level(NotificationLevel.WARNING, channel_mask)
        # INFO event doesn't match
        assert not matches_level(NotificationLevel.INFO, channel_mask)
    
    def test_level_to_string(self):
        """Test level to string conversion."""
        assert level_to_string(NotificationLevel.CRITICAL) == "CRITICAL"
        assert level_to_string(NotificationLevel.ERROR) == "ERROR"
        assert level_to_string(NotificationLevel.WARNING) == "WARNING"
        assert level_to_string(NotificationLevel.INFO) == "INFO"
