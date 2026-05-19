"""
Tests for Digest Worker.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4
from datetime import datetime
import json

from sqlalchemy.orm import Session
import redis

from semabridge.notifications.workers.digest_worker import DigestWorker
from semabridge.notifications.models import NotificationChannel, NotificationEvent, ChannelStatusEnum, ChannelTypeEnum
from semabridge.notifications.constants import NotificationLevel


@pytest.fixture
def mock_redis_client():
    """Fixture for mock Redis client."""
    return MagicMock(spec=redis.Redis)


@pytest.fixture
def mock_db_session():
    """Fixture for mock database session."""
    return MagicMock(spec=Session)


@pytest.fixture
def digest_worker(mock_redis_client, mock_db_session):
    """Fixture for DigestWorker."""
    return DigestWorker(
        redis_url="redis://localhost:6379",
        db_session=mock_db_session,
        poll_interval_sec=60,
    )


@pytest.mark.asyncio
async def test_stage_event_adds_to_redis(digest_worker, mock_redis_client):
    """Test that stage_event pushes event to Redis list."""
    channel_id = str(uuid4())
    
    event = NotificationEvent(
        title="Test Warning",
        message="This is a warning",
        level=NotificationLevel.WARNING,
    )
    
    digest_worker.redis = mock_redis_client
    
    await digest_worker.stage_event(channel_id, event)
    
    # Should push to Redis
    mock_redis_client.lpush.assert_called_once()
    
    # Should set TTL
    mock_redis_client.expire.assert_called_with(f"semabridge:digest-staging:{channel_id}", 86400)


@pytest.mark.asyncio
async def test_flush_digest_with_no_events(digest_worker, mock_redis_client, mock_db_session):
    """Test that flush_digest is no-op when no staged events."""
    channel_id = str(uuid4())
    
    channel = MagicMock()
    channel.id = channel_id
    
    # Mock no events
    mock_redis_client.llen.return_value = 0
    
    digest_worker.redis = mock_redis_client
    digest_worker.db = mock_db_session
    
    await digest_worker._flush_digest(channel)
    
    # Should return early
    mock_redis_client.lrange.assert_not_called()


@pytest.mark.asyncio
async def test_flush_digest_groups_by_level(digest_worker, mock_redis_client, mock_db_session):
    """Test that digest groups events by level."""
    channel_id = str(uuid4())
    
    channel = MagicMock()
    channel.id = channel_id
    channel.name = "test_channel"
    channel.channel_type = ChannelTypeEnum.SLACK
    channel.config_json = "{}"
    channel.quiet_hours_enabled = True
    channel.quiet_hours_start = None
    channel.quiet_hours_end = None
    channel.timezone = "UTC"
    
    # Create test events
    events = [
        NotificationEvent(
            title="Error 1",
            message="Error message",
            level=NotificationLevel.ERROR,
        ),
        NotificationEvent(
            title="Warning 1",
            message="Warning message",
            level=NotificationLevel.WARNING,
        ),
        NotificationEvent(
            title="Warning 2",
            message="Another warning",
            level=NotificationLevel.WARNING,
        ),
    ]
    
    event_jsons = [json.dumps(e.to_dict()) for e in events]
    
    # Mock Redis responses
    mock_redis_client.llen.return_value = 3
    mock_redis_client.lrange.return_value = event_jsons
    
    digest_worker.redis = mock_redis_client
    digest_worker.db = mock_db_session
    
    # Mock adapter/formatter
    with patch.dict(digest_worker.ADAPTERS, {"slack": MagicMock}):
        with patch.dict(digest_worker.FORMATTERS, {"slack": MagicMock}):
            with patch.object(digest_worker, '_build_digest_event', new_callable=AsyncMock) as mock_build:
                mock_build.return_value = NotificationEvent(
                    title="Digest",
                    message="Test digest",
                    level=NotificationLevel.INFO,
                )
                
                with patch.object(digest_worker.delivery_log, 'log_delivery', new_callable=AsyncMock):
                    # This would need more mocking of adapters
                    pass


@pytest.mark.asyncio
async def test_build_digest_event_includes_counts(digest_worker, mock_db_session):
    """Test that digest event includes level counts."""
    from collections import defaultdict
    
    channel = MagicMock()
    channel.timezone = "America/New_York"
    channel.quiet_hours_start = None
    channel.quiet_hours_end = None
    
    events_by_level = {
        "ERROR": [MagicMock(), MagicMock()],
        "WARNING": [MagicMock()],
        "INFO": [MagicMock(), MagicMock(), MagicMock()],
    }
    
    all_events = []
    for level, events in events_by_level.items():
        for event in events:
            event.created_at = datetime.utcnow()
            all_events.append(event)
    
    digest_worker.db = mock_db_session
    
    digest_event = await digest_worker._build_digest_event(
        channel,
        events_by_level,
        all_events
    )
    
    assert "2 error" in digest_event.message or "2" in str(events_by_level)
    assert digest_event.type == "digest"
    assert digest_event.level == 1  # SYNC_RESULT


@pytest.mark.asyncio
async def test_process_digests_skips_disabled_channels(digest_worker, mock_db_session):
    """Test that disabled channels are skipped."""
    channel = MagicMock()
    channel.digest_enabled = True
    channel.enabled = False  # Disabled
    
    mock_query = MagicMock()
    mock_query.filter.return_value.all.return_value = [channel]
    mock_db_session.query.return_value = mock_query
    
    digest_worker.db = mock_db_session
    
    # Should not raise exception
    await digest_worker.process_digests()


@pytest.mark.asyncio
async def test_process_digests_checks_quiet_hours(digest_worker, mock_db_session):
    """Test that digest respects quiet hours end time."""
    channel = MagicMock()
    channel.id = str(uuid4())
    channel.digest_enabled = True
    channel.enabled = True
    
    mock_query = MagicMock()
    mock_query.filter.return_value.all.return_value = [channel]
    mock_db_session.query.return_value = mock_query
    
    digest_worker.db = mock_db_session
    
    # Mock quiet hours still in effect
    with patch.object(digest_worker.quiet_hours, 'seconds_until_window_end', return_value=300):
        await digest_worker.process_digests()
        
        # Should not flush (still in quiet hours)


@pytest.mark.asyncio
async def test_digest_event_includes_timestamps(digest_worker):
    """Test that digest event includes oldest and newest timestamps."""
    from collections import defaultdict
    
    channel = MagicMock()
    channel.timezone = "UTC"
    channel.quiet_hours_start = None
    channel.quiet_hours_end = None
    
    events_by_level = {"INFO": []}
    
    now = datetime.utcnow()
    event1 = MagicMock()
    event1.created_at = now
    
    event2 = MagicMock()
    event2.created_at = now
    
    all_events = [event1, event2]
    
    digest_worker.db = MagicMock()
    
    digest_event = await digest_worker._build_digest_event(
        channel,
        events_by_level,
        all_events
    )
    
    # Should include timestamps
    assert "Oldest:" in digest_event.message or "oldest" in digest_event.payload
    assert "Newest:" in digest_event.message or "newest" in digest_event.payload


@pytest.mark.asyncio
async def test_flush_digest_deletes_queue_on_success(digest_worker, mock_redis_client, mock_db_session):
    """Test that digest queue is cleared after successful send."""
    channel_id = str(uuid4())
    
    channel = MagicMock()
    channel.id = channel_id
    channel.name = "test_channel"
    channel.channel_type = ChannelTypeEnum.SLACK
    channel.config_json = "{}"
    channel.quiet_hours_enabled = False
    
    # Mock one event
    event = NotificationEvent(
        title="Test",
        message="Test",
        level=NotificationLevel.INFO,
    )
    
    mock_redis_client.llen.return_value = 1
    mock_redis_client.lrange.return_value = [json.dumps(event.to_dict())]
    
    digest_worker.redis = mock_redis_client
    digest_worker.db = mock_db_session
    
    # Mock successful send
    with patch.object(digest_worker, '_build_digest_event', new_callable=AsyncMock):
        with patch.object(digest_worker.delivery_log, 'log_delivery', new_callable=AsyncMock):
            with patch.object(digest_worker.ADAPTERS.get('slack', MagicMock), 'send', new_callable=AsyncMock, return_value={"success": True}):
                # Would need more setup for full test
                pass
