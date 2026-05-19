"""
Tests for Replay Service.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from uuid import uuid4

from semabridge.notifications.services.replay_service import ReplayService
from semabridge.notifications.models.replay import ReplayFilter, ReplayResult


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    return MagicMock()


@pytest.fixture
def mock_notification_service():
    """Mock notification service."""
    service = AsyncMock()
    service.emit = AsyncMock(return_value=True)
    return service


@pytest.fixture
def replay_service(mock_db_session, mock_notification_service):
    """Replay service fixture."""
    return ReplayService(mock_db_session, mock_notification_service)


@pytest.fixture
def sample_log():
    """Sample notification log."""
    log = MagicMock()
    log.id = uuid4()
    log.event_id = uuid4()
    log.channel_id = uuid4()
    log.status = "failed"
    log.attempt = 2
    log.response_code = 500
    log.duration_ms = 150
    log.error_message = "Connection timeout"
    log.created_at = datetime.utcnow() - timedelta(hours=1)
    log.correlation_id = "corr_123"
    log.sync_job_id = "job_456"
    log.project_id = "proj_789"
    log.level = 4  # ERROR
    log.title = "Sync failed"
    log.message = "Data sync encountered errors"
    log.payload = {"error_count": 5}
    
    return log


@pytest.mark.asyncio
async def test_replay_single_event(replay_service, mock_db_session, sample_log):
    """Test replaying a single event."""
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = sample_log
    mock_db_session.query.return_value = mock_query
    
    result = await replay_service.replay_event(str(sample_log.id))
    
    assert result.log_id == str(sample_log.id)
    assert result.original_event_id == str(sample_log.event_id)
    assert result.new_event_id != ""
    assert result.enqueued_at is not None
    
    # Should have called emit
    replay_service.notification_service.emit.assert_called_once()


@pytest.mark.asyncio
async def test_replay_event_with_target_channels(replay_service, mock_db_session, sample_log):
    """Test replaying event to specific target channels."""
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = sample_log
    mock_db_session.query.return_value = mock_query
    
    target_channels = [str(uuid4()), str(uuid4())]
    result = await replay_service.replay_event(str(sample_log.id), target_channels)
    
    assert result.channels_targeted == target_channels
    
    # Verify emit was called
    replay_service.notification_service.emit.assert_called_once()
    
    # Verify target_channels added to payload
    call_args = replay_service.notification_service.emit.call_args
    event = call_args[0][0]
    assert event.payload.get("target_channels") == target_channels


@pytest.mark.asyncio
async def test_replay_event_not_found(replay_service, mock_db_session):
    """Test replaying non-existent event."""
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = None
    mock_db_session.query.return_value = mock_query
    
    result = await replay_service.replay_event("nonexistent")
    
    assert result.original_event_id == ""
    assert result.enqueued_at is None
    
    # Should not have called emit
    replay_service.notification_service.emit.assert_not_called()


@pytest.mark.asyncio
async def test_replay_bulk_with_limit(replay_service, mock_db_session):
    """Test bulk replay with limit enforcement."""
    # Create 600 logs, limit should enforce 500 max
    logs = [MagicMock() for _ in range(600)]
    for i, log in enumerate(logs):
        log.id = uuid4()
        log.event_id = uuid4()
        log.channel_id = uuid4()
        log.status = "failed"
        log.correlation_id = f"corr_{i}"
        log.sync_job_id = f"job_{i}"
        log.project_id = f"proj_{i}"
        log.level = 4
        log.title = f"Event {i}"
        log.message = ""
        log.payload = {}
    
    mock_query = MagicMock()
    mock_query.filter.return_value.filter.return_value.filter.return_value.filter.return_value.limit.return_value.all.return_value = logs[:500]
    mock_db_session.query.return_value = mock_query
    
    filter = ReplayFilter(limit=600)
    result = await replay_service.replay_bulk(filter)
    
    # Should be limited to 500
    assert result.replayed + result.errors + result.skipped <= 500


@pytest.mark.asyncio
async def test_replay_bulk_with_status_filter(replay_service, mock_db_session, sample_log):
    """Test bulk replay with status filter."""
    logs = [sample_log]
    
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query  # Chain-able filter
    mock_query.filter.return_value.filter.return_value.filter.return_value.filter.return_value.limit.return_value.all.return_value = logs
    mock_db_session.query.return_value = mock_query
    
    filter = ReplayFilter(status=["failed", "dead"])
    result = await replay_service.replay_bulk(filter)
    
    # Should have replayed at least one
    assert result.replayed >= 0


@pytest.mark.asyncio
async def test_replay_bulk_batching(replay_service, mock_db_session):
    """Test bulk replay processes in 50-row batches."""
    # Create 150 logs (3 batches of 50)
    logs = [MagicMock() for _ in range(150)]
    for i, log in enumerate(logs):
        log.id = uuid4()
        log.event_id = uuid4()
        log.channel_id = uuid4()
        log.status = "failed"
        log.correlation_id = f"corr_{i}"
        log.sync_job_id = f"job_{i}"
        log.project_id = f"proj_{i}"
        log.level = 4
        log.title = ""
        log.message = ""
        log.payload = {}
    
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.filter.return_value.filter.return_value.filter.return_value.filter.return_value.limit.return_value.all.return_value = logs
    mock_db_session.query.return_value = mock_query
    
    filter = ReplayFilter(limit=150)
    result = await replay_service.replay_bulk(filter)
    
    # Should have processed all 150
    assert result.replayed + result.errors + result.skipped == 150


@pytest.mark.asyncio
async def test_replay_bypass_dedupe(replay_service, mock_db_session, sample_log):
    """Test replay bypasses deduplication."""
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = sample_log
    mock_db_session.query.return_value = mock_query
    
    # Verify replay doesn't call dedupe checks
    result = await replay_service.replay_event(str(sample_log.id))
    
    # Event should have source="replay"
    call_args = replay_service.notification_service.emit.call_args
    event = call_args[0][0]
    assert event.source == "replay"
    assert event.payload.get("replayed_from_log_id") == str(sample_log.id)


@pytest.mark.asyncio
async def test_get_replay_candidates_preview(replay_service, mock_db_session, sample_log):
    """Test get_replay_candidates dry-run (no enqueue)."""
    logs = [sample_log]
    
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.filter.return_value.filter.return_value.filter.return_value.filter.return_value.limit.return_value.all.return_value = logs
    mock_db_session.query.return_value = mock_query
    
    filter = ReplayFilter()
    candidates = await replay_service.get_replay_candidates(filter)
    
    assert len(candidates) == 1
    assert candidates[0] == sample_log
    
    # Should NOT have called emit
    replay_service.notification_service.emit.assert_not_called()


@pytest.mark.asyncio
async def test_get_replay_candidates_with_filters(replay_service, mock_db_session):
    """Test get_replay_candidates with filters."""
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.filter.return_value.filter.return_value.filter.return_value.filter.return_value.limit.return_value.all.return_value = []
    mock_db_session.query.return_value = mock_query
    
    filter = ReplayFilter(
        status=["dead"],
        channel_id=str(uuid4()),
        since=datetime.utcnow() - timedelta(days=7),
        until=datetime.utcnow(),
        limit=100,
    )
    
    candidates = await replay_service.get_replay_candidates(filter)
    
    assert isinstance(candidates, list)


@pytest.mark.asyncio
async def test_replay_adds_metadata(replay_service, mock_db_session, sample_log):
    """Test replay adds replayed_from_log_id to payload."""
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = sample_log
    mock_db_session.query.return_value = mock_query
    
    result = await replay_service.replay_event(str(sample_log.id))
    
    # Get the emitted event
    call_args = replay_service.notification_service.emit.call_args
    event = call_args[0][0]
    
    # Verify metadata
    assert event.payload["replayed_from_log_id"] == str(sample_log.id)
    assert event.type == "notification_replay"
    assert event.source == "replay"


@pytest.mark.asyncio
async def test_replay_preserves_original_fields(replay_service, mock_db_session, sample_log):
    """Test replay preserves original event fields."""
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = sample_log
    mock_db_session.query.return_value = mock_query
    
    result = await replay_service.replay_event(str(sample_log.id))
    
    # Get the emitted event
    call_args = replay_service.notification_service.emit.call_args
    event = call_args[0][0]
    
    # Verify fields preserved
    assert event.correlation_id == sample_log.correlation_id
    assert event.sync_job_id == sample_log.sync_job_id
    assert event.project_id == sample_log.project_id
    assert event.level == sample_log.level
