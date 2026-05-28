"""
Unit tests for BatchingFlushWorker and active batch tracking.
"""

import json
import pytest
import asyncio
from uuid import uuid4
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from semabridge.notifications.models import NotificationEvent
from semabridge.notifications.services.batching_service import BatchingService
from semabridge.notifications.workers.batch_worker import BatchingFlushWorker
from semabridge.notifications.constants import NotificationLevel, RedisQueues


@pytest.fixture
def batch_service(redis_client):
    """BatchingService with a fake Redis client."""
    return BatchingService(redis_client, batch_window_sec=2)


@pytest.fixture
def batch_worker(redis_client):
    """BatchingFlushWorker with a fake Redis client and mock DB."""
    mock_db = MagicMock()
    worker = BatchingFlushWorker(
        redis_url="redis://localhost:6379",
        db_session=mock_db,
        poll_interval_sec=1,
        count_threshold=5,  # Trigger immediate flush if count >= 5
    )
    worker.redis = redis_client
    worker.queue.redis = redis_client
    return worker


@pytest.mark.asyncio
async def test_add_to_batch_tracks_active_batch(batch_service, redis_client):
    """Test that adding the first item to a batch starts tracking it."""
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr_1",
        sync_job_id="job_123",
        project_id="proj_1",
        type="sync_fail",
        level=NotificationLevel.ERROR,
        title="Sync Failed",
        message="Failure details",
        payload={},
        source="sync",
    )
    channel_id = str(uuid4())
    
    # Add to batch
    success = await batch_service.add_to_batch(event, channel_id)
    assert success is True
    
    # 1. Descriptor should be added to active batches set
    active = redis_client.smembers("semabridge:active_batches")
    assert f"job_123:{channel_id}" in active
    
    # 2. Metadata key should exist
    meta_raw = redis_client.get(f"semabridge:batch_meta:job_123:{channel_id}")
    assert meta_raw is not None
    meta_data = json.loads(meta_raw)
    assert meta_data["sync_job_id"] == "job_123"
    assert meta_data["channel_id"] == channel_id
    assert "flush_at" in meta_data


@pytest.mark.asyncio
async def test_time_threshold_trigger_flush(batch_worker, batch_service, redis_client):
    """Test that polling flushes a batch after the time window expires."""
    batch_worker.batching_service = batch_service
    batch_worker.redis = redis_client
    
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr_1",
        sync_job_id="job_123",
        project_id="proj_1",
        type="sync_fail",
        level=NotificationLevel.ERROR,
        title="Sync Failed",
        message="Failure details",
        payload={},
        source="sync",
    )
    channel_id = str(uuid4())
    
    # Add to batch
    await batching_add_and_set_expired(batch_service, redis_client, event, channel_id, expired=True)
    
    # Poll and flush
    await batch_worker.poll_and_flush_batches()
    
    # Batch should be cleared and tracking removed
    active = redis_client.smembers("semabridge:active_batches")
    assert f"job_123:{channel_id}" not in active
    assert redis_client.get(f"semabridge:batch_meta:job_123:{channel_id}") is None
    
    # Verify synthetic event was enqueued
    queued = redis_client.xrange(RedisQueues.NOTIFICATIONS)
    assert len(queued) == 1
    queued_event = json.loads(queued[0][1]["payload"])
    assert queued_event["type"] == "batch_aggregate"
    assert queued_event["payload"]["bypass_batching"] is True
    assert queued_event["payload"]["target_channel_id"] == channel_id


@pytest.mark.asyncio
async def test_count_threshold_trigger_flush(batch_worker, batch_service, redis_client):
    """Test that polling flushes a batch immediately if item count >= threshold."""
    batch_worker.batching_service = batch_service
    batch_worker.redis = redis_client
    
    channel_id = str(uuid4())
    sync_job_id = "job_123"
    
    # Add 5 events (which matches count_threshold=5)
    for i in range(5):
        event = NotificationEvent(
            id=uuid4(),
            correlation_id=f"corr_{i}",
            sync_job_id=sync_job_id,
            project_id="proj_1",
            type="sync_fail",
            level=NotificationLevel.ERROR,
            title=f"Sync Failed {i}",
            message="Failure details",
            payload={},
            source="sync",
        )
        await batch_service.add_to_batch(event, channel_id)
        
    # Poll and flush
    await batch_worker.poll_and_flush_batches()
    
    # Batch should be cleared immediately
    active = redis_client.smembers("semabridge:active_batches")
    assert f"{sync_job_id}:{channel_id}" not in active
    
    # Verify synthetic event was enqueued
    queued = redis_client.xrange(RedisQueues.NOTIFICATIONS)
    assert len(queued) == 1
    queued_event = json.loads(queued[0][1]["payload"])
    assert queued_event["payload"]["batch_details"]["total_count"] == 5


@pytest.mark.asyncio
async def test_graceful_shutdown_flushes_remaining(batch_worker, batch_service, redis_client):
    """Test that graceful shutdown flushes any remaining active batches immediately."""
    batch_worker.batching_service = batch_service
    batch_worker.redis = redis_client
    
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr_1",
        sync_job_id="job_shutdown",
        project_id="proj_1",
        type="sync_fail",
        level=NotificationLevel.ERROR,
        title="Sync Failed During Shutdown",
        message="Shutdown details",
        payload={},
        source="sync",
    )
    channel_id = str(uuid4())
    
    # Add to batch (not expired)
    await batch_service.add_to_batch(event, channel_id)
    
    # Trigger graceful shutdown flush
    await batch_worker.flush_all_active_batches()
    
    # Verify remaining batch was successfully flushed
    active = redis_client.smembers("semabridge:active_batches")
    assert f"job_shutdown:{channel_id}" not in active
    
    queued = redis_client.xrange(RedisQueues.NOTIFICATIONS)
    assert len(queued) == 1


# Helper function to manipulate flush_at timestamp
async def batching_add_and_set_expired(batch_service, redis_client, event, channel_id, expired=False):
    await batch_service.add_to_batch(event, channel_id)
    if expired:
        meta_key = f"semabridge:batch_meta:{event.sync_job_id}:{channel_id}"
        meta_raw = redis_client.get(meta_key)
        if meta_raw:
            meta_data = json.loads(meta_raw)
            # Set flush_at to 10 seconds ago
            past_time = datetime.utcnow() - timedelta(seconds=10)
            meta_data["flush_at"] = past_time.isoformat()
            redis_client.set(meta_key, json.dumps(meta_data))
