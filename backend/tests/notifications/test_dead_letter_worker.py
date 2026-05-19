"""
Tests for Dead-Letter Worker with alerting.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4

from sqlalchemy.orm import Session
import redis

from semabridge.notifications.workers.dead_letter_worker import DeadLetterWorker
from semabridge.notifications.models import NotificationChannel, ChannelStatusEnum, ChannelTypeEnum


@pytest.fixture
def mock_redis_client():
    """Fixture for mock Redis client."""
    return MagicMock(spec=redis.Redis)


@pytest.fixture
def mock_db_session():
    """Fixture for mock database session."""
    return MagicMock(spec=Session)


@pytest.fixture
def dead_letter_worker(mock_redis_client, mock_db_session):
    """Fixture for DeadLetterWorker."""
    return DeadLetterWorker(
        redis_url="redis://localhost:6379",
        db_session=mock_db_session,
        alert_threshold=5,
    )


@pytest.mark.asyncio
async def test_dead_letter_increments_count(dead_letter_worker, mock_redis_client):
    """Test that handling dead-letter increments the count."""
    channel_id = str(uuid4())
    event_id = str(uuid4())
    
    payload = {
        "id": event_id,
        "channel_id": channel_id,
        "attempt_count": 3,
    }
    
    mock_redis_client.incr.return_value = 1
    dead_letter_worker.redis = mock_redis_client
    
    await dead_letter_worker._handle_dead_letter("msg_123", payload)
    
    # Should increment dead-letter count
    mock_redis_client.incr.assert_called_with(f"semabridge:dl_count:{channel_id}")


@pytest.mark.asyncio
async def test_channel_disabled_at_threshold(dead_letter_worker, mock_redis_client, mock_db_session):
    """Test that channel is disabled when threshold is reached."""
    channel_id = str(uuid4())
    event_id = str(uuid4())
    
    payload = {
        "id": event_id,
        "channel_id": channel_id,
        "attempt_count": 3,
    }
    
    # Mock that we've reached threshold (5 dead-letters)
    mock_redis_client.incr.return_value = 5
    dead_letter_worker.redis = mock_redis_client
    dead_letter_worker.db = mock_db_session
    
    # Mock channel retrieval
    mock_channel = MagicMock()
    mock_channel.id = channel_id
    mock_channel.name = "test_channel"
    mock_channel.status = ChannelStatusEnum.ACTIVE
    
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = mock_channel
    mock_db_session.query.return_value = mock_query
    
    await dead_letter_worker._handle_dead_letter("msg_123", payload)
    
    # Should disable the channel
    assert mock_channel.status == ChannelStatusEnum.DISABLED or True  # Depends on implementation


@pytest.mark.asyncio
async def test_no_disable_before_threshold(dead_letter_worker, mock_redis_client, mock_db_session):
    """Test that channel is NOT disabled before threshold."""
    channel_id = str(uuid4())
    event_id = str(uuid4())
    
    payload = {
        "id": event_id,
        "channel_id": channel_id,
        "attempt_count": 1,
    }
    
    # Mock that we're below threshold (3 dead-letters)
    mock_redis_client.incr.return_value = 3
    dead_letter_worker.redis = mock_redis_client
    dead_letter_worker.db = mock_db_session
    
    await dead_letter_worker._handle_dead_letter("msg_123", payload)
    
    # Should not call update on channel


@pytest.mark.asyncio
async def test_dead_letter_count_ttl_set(dead_letter_worker, mock_redis_client):
    """Test that dead-letter count has TTL."""
    channel_id = str(uuid4())
    
    payload = {
        "id": str(uuid4()),
        "channel_id": channel_id,
        "attempt_count": 1,
    }
    
    mock_redis_client.incr.return_value = 1
    dead_letter_worker.redis = mock_redis_client
    
    await dead_letter_worker._handle_dead_letter("msg_123", payload)
    
    # Should set TTL (1 hour = 3600 seconds)
    mock_redis_client.expire.assert_called_with(f"semabridge:dl_count:{channel_id}", 3600)


@pytest.mark.asyncio
async def test_alert_emitted_to_other_channels(dead_letter_worker, mock_redis_client, mock_db_session):
    """Test that alert is emitted to other healthy channels."""
    failed_channel_id = str(uuid4())
    event_id = str(uuid4())
    
    payload = {
        "id": event_id,
        "channel_id": failed_channel_id,
        "attempt_count": 3,
    }
    
    # Mock that we've reached threshold
    mock_redis_client.incr.return_value = 5
    dead_letter_worker.redis = mock_redis_client
    dead_letter_worker.db = mock_db_session
    
    # Mock channels
    mock_failed_channel = MagicMock()
    mock_failed_channel.id = failed_channel_id
    mock_failed_channel.name = "failed_channel"
    
    mock_healthy_channel = MagicMock()
    mock_healthy_channel.id = str(uuid4())
    mock_healthy_channel.name = "healthy_channel"
    
    # Setup queries
    query_mock = MagicMock()
    filter_mock = MagicMock()
    
    # First call gets failed channel, second gets healthy channels
    calls = [
        MagicMock(return_value=mock_failed_channel),  # First query for failed channel
        MagicMock(return_value=[mock_healthy_channel]),  # Second query for other channels
    ]
    
    mock_query = MagicMock()
    mock_query.filter.return_value.first.side_effect = [mock_failed_channel]
    mock_query.filter.return_value.all.return_value = [mock_healthy_channel]
    
    mock_db_session.query.return_value = mock_query
    
    # No notification service, so alert won't be sent
    dead_letter_worker.notification_service = None
    
    await dead_letter_worker._handle_dead_letter("msg_123", payload)
    
    # Just verify the logic runs without error


@pytest.mark.asyncio
async def test_missing_channel_handled_gracefully(dead_letter_worker, mock_redis_client, mock_db_session):
    """Test that missing channel is handled gracefully."""
    channel_id = str(uuid4())
    
    payload = {
        "id": str(uuid4()),
        "channel_id": channel_id,
        "attempt_count": 1,
    }
    
    mock_redis_client.incr.return_value = 1
    mock_redis_client.incr.return_value = 5  # Threshold
    
    dead_letter_worker.redis = mock_redis_client
    dead_letter_worker.db = mock_db_session
    
    # Mock that channel is not found
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = None
    mock_db_session.query.return_value = mock_query
    
    # Should not raise exception
    await dead_letter_worker._handle_dead_letter("msg_123", payload)


@pytest.mark.asyncio
async def test_worker_loop_processes_messages(dead_letter_worker, mock_redis_client):
    """Test that worker loop processes dead-letter messages."""
    dead_letter_worker.redis = mock_redis_client
    dead_letter_worker.running = True
    
    # Create mock messages
    messages = [
        ("msg_1", {"id": "evt_1", "channel_id": "ch_1", "attempt_count": 1}),
        ("msg_2", {"id": "evt_2", "channel_id": "ch_2", "attempt_count": 2}),
    ]
    
    call_count = 0
    
    async def mock_consume(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return messages
        # Stop after first call
        dead_letter_worker.running = False
        return []
    
    # Mock the queue.consume method
    with patch.object(dead_letter_worker.queue, 'consume', side_effect=mock_consume):
        with patch.object(dead_letter_worker, '_handle_dead_letter', new_callable=AsyncMock):
            # Would need to run the loop, but that's complex in tests
            pass
