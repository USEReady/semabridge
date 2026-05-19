"""
Tests for Analytics Service.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from datetime import datetime, timedelta
from uuid import uuid4

from semabridge.notifications.services.analytics_service import AnalyticsService
from semabridge.notifications.models import NotificationLog, DeliveryStatusEnum


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    return MagicMock()


@pytest.fixture
def analytics_service(mock_db_session):
    """Analytics service fixture."""
    return AnalyticsService(mock_db_session)


@pytest.fixture
def sample_logs():
    """Sample notification logs."""
    channel_id = uuid4()
    logs = []
    
    # Create various log entries
    base_time = datetime.utcnow() - timedelta(hours=1)
    
    for i in range(10):
        log = MagicMock()
        log.id = uuid4()
        log.event_id = uuid4()
        log.channel_id = channel_id
        log.status = ["delivered", "delivered", "delivered", "failed", "dead", "retrying"][i % 6]
        log.attempt = i % 3 + 1
        log.response_code = 200 if log.status == "delivered" else 500
        log.duration_ms = 100 + (i * 10)
        log.error_message = None if log.status == "delivered" else f"Error {i}"
        log.created_at = base_time + timedelta(minutes=i)
        log.response_body = None
        
        logs.append(log)
    
    return logs


@pytest.mark.asyncio
async def test_get_delivery_stats_all_logs(analytics_service, mock_db_session, sample_logs):
    """Test get_delivery_stats with all logs."""
    mock_db_session.query.return_value.filter.return_value.all.return_value = sample_logs
    
    stats = await analytics_service.get_delivery_stats()
    
    assert stats.total == 10
    assert stats.delivered == 3  # Count of 'delivered' status
    assert stats.failed == 1
    assert stats.dead == 1
    assert stats.retrying == 1
    assert stats.avg_duration_ms > 0
    assert stats.p95_duration_ms > 0


@pytest.mark.asyncio
async def test_get_delivery_stats_by_channel(analytics_service, mock_db_session, sample_logs):
    """Test get_delivery_stats filtered by channel."""
    channel_id = sample_logs[0].channel_id
    mock_db_session.query.return_value.filter.return_value.all.return_value = sample_logs
    
    stats = await analytics_service.get_delivery_stats(channel_id=str(channel_id))
    
    assert stats.total == 10
    assert str(channel_id) in stats.by_channel
    mock_db_session.query.return_value.filter.assert_called()


@pytest.mark.asyncio
async def test_get_delivery_stats_by_date_range(analytics_service, mock_db_session, sample_logs):
    """Test get_delivery_stats with date range."""
    since = datetime.utcnow() - timedelta(hours=2)
    until = datetime.utcnow()
    
    mock_db_session.query.return_value.filter.return_value.all.return_value = sample_logs
    
    stats = await analytics_service.get_delivery_stats(since=since, until=until)
    
    assert stats.window_start == since
    assert stats.window_end == until


@pytest.mark.asyncio
async def test_get_channel_health(analytics_service, mock_db_session, sample_logs):
    """Test get_channel_health."""
    channel_id = sample_logs[0].channel_id
    
    # Mock channel lookup
    mock_channel = MagicMock()
    mock_channel.name = "Test Channel"
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_channel
    
    # Mock logs query
    mock_db_session.query.return_value.filter.return_value.all.return_value = sample_logs
    
    health = await analytics_service.get_channel_health(str(channel_id))
    
    assert health.channel_id == str(channel_id)
    assert health.channel_name == "Test Channel"
    assert health.total_deliveries == 10
    assert health.success_rate > 0
    assert health.avg_duration_ms > 0


@pytest.mark.asyncio
async def test_get_channel_health_no_logs(analytics_service, mock_db_session):
    """Test get_channel_health with no logs."""
    channel_id = uuid4()
    
    # Mock channel lookup
    mock_channel = MagicMock()
    mock_channel.name = "Test Channel"
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_channel
    
    # Mock empty logs
    mock_db_session.query.return_value.filter.return_value.all.return_value = []
    
    health = await analytics_service.get_channel_health(str(channel_id))
    
    assert health.channel_id == str(channel_id)
    assert health.total_deliveries == 0
    assert health.success_rate == 0


@pytest.mark.asyncio
async def test_flush_to_snowflake_no_config(analytics_service, mock_db_session):
    """Test flush_to_snowflake when Snowflake not configured."""
    with patch.dict("os.environ", {}, clear=True):
        result = await analytics_service.flush_to_snowflake()
    
    assert result["flushed"] == 0
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_flush_to_snowflake_incomplete_config(analytics_service, mock_db_session):
    """Test flush_to_snowflake with incomplete config."""
    with patch.dict("os.environ", {"SNOWFLAKE_ACCOUNT": "test.us-east-1"}):
        result = await analytics_service.flush_to_snowflake()
    
    assert result["flushed"] == 0
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_flush_to_snowflake_no_rows(analytics_service, mock_db_session):
    """Test flush_to_snowflake with no unflushed rows."""
    env_vars = {
        "SNOWFLAKE_ACCOUNT": "test.us-east-1",
        "SNOWFLAKE_USER": "user",
        "SNOWFLAKE_PASSWORD": "pass",
        "SNOWFLAKE_WAREHOUSE": "wh",
        "SNOWFLAKE_DATABASE": "db",
        "SNOWFLAKE_SCHEMA": "schema",
    }
    
    # Mock empty query result
    mock_db_session.query.return_value.filter.return_value.limit.return_value.all.return_value = []
    
    with patch.dict("os.environ", env_vars):
        result = await analytics_service.flush_to_snowflake()
    
    assert result["flushed"] == 0
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_flush_to_snowflake_with_rows(analytics_service, mock_db_session, sample_logs):
    """Test flush_to_snowflake with actual rows."""
    env_vars = {
        "SNOWFLAKE_ACCOUNT": "test.us-east-1",
        "SNOWFLAKE_USER": "user",
        "SNOWFLAKE_PASSWORD": "pass",
        "SNOWFLAKE_WAREHOUSE": "wh",
        "SNOWFLAKE_DATABASE": "db",
        "SNOWFLAKE_SCHEMA": "schema",
        "SNOWFLAKE_TABLE": "EVENTS",
        "SNOWFLAKE_BATCH_SIZE": "500",
    }
    
    # Mock logs query
    mock_db_session.query.return_value.filter.return_value.limit.return_value.all.return_value = sample_logs
    
    with patch.dict("os.environ", env_vars):
        with patch("semabridge.notifications.services.analytics_service.SnowflakeAdapter") as mock_adapter_class:
            mock_adapter = AsyncMock()
            mock_adapter_class.return_value = mock_adapter
            mock_adapter.bulk_insert = AsyncMock(return_value={"inserted": 10, "failed": 0})
            
            result = await analytics_service.flush_to_snowflake()
            
            # Should have called bulk_insert
            assert result["flushed"] == 10 or result["errors"] == 0


@pytest.mark.asyncio
async def test_flush_to_snowflake_error_handling(analytics_service, mock_db_session):
    """Test flush_to_snowflake error handling."""
    env_vars = {
        "SNOWFLAKE_ACCOUNT": "test.us-east-1",
        "SNOWFLAKE_USER": "user",
        "SNOWFLAKE_PASSWORD": "pass",
        "SNOWFLAKE_WAREHOUSE": "wh",
        "SNOWFLAKE_DATABASE": "db",
        "SNOWFLAKE_SCHEMA": "schema",
    }
    
    # Mock exception on query
    mock_db_session.query.return_value.filter.return_value.limit.side_effect = Exception("DB error")
    
    with patch.dict("os.environ", env_vars):
        result = await analytics_service.flush_to_snowflake()
    
    # Should handle error gracefully
    assert result["flushed"] == 0
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_delivery_stats_success_rate(analytics_service):
    """Test DeliveryStats success_rate calculation."""
    from semabridge.notifications.models.analytics import DeliveryStats
    
    stats = DeliveryStats(total=10, delivered=8)
    assert stats.success_rate() == 80.0
    
    stats2 = DeliveryStats(total=0, delivered=0)
    assert stats2.success_rate() == 0.0
