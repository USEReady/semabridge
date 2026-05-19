"""Test deduplication service."""

import pytest
from datetime import datetime, timedelta


class TestDedupeService:
    """Test notification deduplication."""
    
    @pytest.mark.asyncio
    async def test_duplicate_detection(self, redis_client):
        """Test detecting duplicate fingerprints."""
        from semabridge.notifications.services.dedupe_service import DedupeService
        
        service = DedupeService(redis_client, ttl_sec=60)
        
        fingerprint = "abc123def456"
        
        # Should not be duplicate initially
        is_dup = await service.is_duplicate(fingerprint)
        assert not is_dup
        
        # Mark as processed
        await service.mark_processed(fingerprint)
        
        # Should now be duplicate
        is_dup = await service.is_duplicate(fingerprint)
        assert is_dup
    
    @pytest.mark.asyncio
    async def test_different_fingerprints(self, redis_client):
        """Test different fingerprints are not duplicates."""
        from semabridge.notifications.services.dedupe_service import DedupeService
        
        service = DedupeService(redis_client, ttl_sec=60)
        
        # Mark first
        await service.mark_processed("fingerprint1")
        
        # Different fingerprint should not be duplicate
        is_dup = await service.is_duplicate("fingerprint2")
        assert not is_dup
    
    @pytest.mark.asyncio
    async def test_ttl_expiry(self, redis_client):
        """Test fingerprint TTL expiry."""
        from semabridge.notifications.services.dedupe_service import DedupeService
        import time
        
        service = DedupeService(redis_client, ttl_sec=1)
        
        # Mark as processed with short TTL
        await service.mark_processed("short_lived")
        
        # Should be duplicate immediately
        is_dup = await service.is_duplicate("short_lived")
        assert is_dup
        
        # Wait for expiry
        time.sleep(2)
        
        # Should not be duplicate after TTL
        is_dup = await service.is_duplicate("short_lived")
        assert not is_dup
