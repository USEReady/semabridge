"""Test batching service."""

import pytest
from semabridge.notifications.models import NotificationEvent
from semabridge.notifications.constants import NotificationLevel


class TestBatchingService:
    """Test notification batching."""
    
    @pytest.mark.asyncio
    async def test_critical_bypasses_batching(self, redis_client):
        """Test CRITICAL events bypass batching."""
        from semabridge.notifications.services.batching_service import BatchingService
        
        service = BatchingService(redis_client)
        
        # CRITICAL event
        event = NotificationEvent(
            sync_job_id="job_123",
            level=NotificationLevel.CRITICAL,
            title="Critical",
        )
        
        # Should not batch
        should_batch = await service.should_batch(event)
        assert not should_batch
    
    @pytest.mark.asyncio
    async def test_warning_batching(self, redis_client):
        """Test WARNING events can be batched."""
        from semabridge.notifications.services.batching_service import BatchingService
        
        service = BatchingService(redis_client)
        
        # WARNING event with sync_job_id
        event = NotificationEvent(
            sync_job_id="job_123",
            level=NotificationLevel.WARNING,
            title="Warning",
        )
        
        # Should batch
        should_batch = await service.should_batch(event)
        assert should_batch
    
    @pytest.mark.asyncio
    async def test_no_sync_job_no_batch(self, redis_client):
        """Test events without sync_job_id are not batched."""
        from semabridge.notifications.services.batching_service import BatchingService
        
        service = BatchingService(redis_client)
        
        # WARNING but no sync_job_id
        event = NotificationEvent(
            level=NotificationLevel.WARNING,
            title="Warning",
        )
        
        # Should not batch
        should_batch = await service.should_batch(event)
        assert not should_batch
    
    @pytest.mark.asyncio
    async def test_add_to_batch(self, redis_client):
        """Test adding event to batch."""
        from semabridge.notifications.services.batching_service import BatchingService
        
        service = BatchingService(redis_client)
        
        event = NotificationEvent(
            sync_job_id="job_123",
            level=NotificationLevel.WARNING,
            title="Warning",
        )
        
        # Add to batch
        result = await service.add_to_batch(event, "channel_1")
        assert result is True
        
        # Retrieve batch
        batch = await service.get_batch("job_123", "channel_1")
        assert batch is not None
        assert batch["warning_count"] == 1
