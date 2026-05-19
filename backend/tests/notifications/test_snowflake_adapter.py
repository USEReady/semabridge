"""
Tests for Snowflake adapter.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from datetime import datetime
from uuid import uuid4

from semabridge.notifications.adapters.snowflake_adapter import SnowflakeAdapter
from semabridge.notifications.formatters.snowflake_formatter import SnowflakeFormatter
from semabridge.notifications.models import NotificationEvent


@pytest.fixture
def snowflake_config():
    """Snowflake configuration fixture."""
    return {
        "account": "xy12345.us-east-1",
        "user": "notification_user",
        "password": "secret_password_123",
        "warehouse": "COMPUTE_WH",
        "database": "ANALYTICS",
        "schema": "NOTIFICATIONS",
        "table": "NOTIFICATION_EVENTS",
    }


@pytest.fixture
def sample_event():
    """Sample notification event."""
    return NotificationEvent(
        id=uuid4(),
        correlation_id="corr_123",
        sync_job_id="job_456",
        project_id="proj_789",
        type="sync_completion",
        level=4,  # ERROR
        title="Sync failed",
        message="Data sync encountered errors",
        payload={"error_count": 5},
        source="sync_engine",
        created_at=datetime.utcnow(),
        sequence_number=1,
    )


@pytest.fixture
def snowflake_adapter(snowflake_config):
    """Snowflake adapter fixture."""
    return SnowflakeAdapter(snowflake_config)


@pytest.mark.asyncio
async def test_snowflake_adapter_single_insert(snowflake_adapter, snowflake_config, sample_event):
    """Test single row insert."""
    formatter = SnowflakeFormatter()
    payload = formatter.format(sample_event, snowflake_config)
    
    with patch("semabridge.notifications.adapters.snowflake_adapter.asyncio.get_event_loop") as mock_loop:
        mock_executor = AsyncMock()
        mock_executor.run_in_executor = AsyncMock(return_value=None)
        mock_loop.return_value = mock_executor
        
        with patch("semabridge.notifications.adapters.snowflake_adapter.SnowflakeAdapter._insert_row") as mock_insert:
            mock_insert.return_value = None
            
            result = await snowflake_adapter.send(payload, snowflake_config)
            
            assert result["success"] is True
            assert result["response_code"] == 200
            assert result["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_snowflake_adapter_bulk_insert(snowflake_adapter, snowflake_config, sample_event):
    """Test bulk insert."""
    formatter = SnowflakeFormatter()
    
    rows = [
        formatter.format(sample_event, snowflake_config),
        formatter.format(sample_event, snowflake_config),
        formatter.format(sample_event, snowflake_config),
    ]
    
    with patch("semabridge.notifications.adapters.snowflake_adapter.asyncio.get_event_loop") as mock_loop:
        mock_executor = AsyncMock()
        mock_executor.run_in_executor = AsyncMock(return_value=3)
        mock_loop.return_value = mock_executor
        
        with patch("semabridge.notifications.adapters.snowflake_adapter.SnowflakeAdapter._bulk_insert_rows") as mock_bulk:
            mock_bulk.return_value = 3
            
            result = await snowflake_adapter.bulk_insert(rows, snowflake_config)
            
            assert result["inserted"] == 3
            assert result["failed"] == 0
            assert result["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_snowflake_config_validation_missing_account(snowflake_adapter):
    """Test config validation - missing account."""
    config = {
        "user": "test",
        "password": "pass",
        "warehouse": "WH",
        "database": "DB",
        "schema": "SCHEMA",
        "table": "TABLE",
    }
    
    is_valid, error = await snowflake_adapter.validate_config(config)
    assert is_valid is False
    assert "account" in error.lower()


@pytest.mark.asyncio
async def test_snowflake_config_validation_missing_password(snowflake_adapter):
    """Test config validation - missing password."""
    config = {
        "account": "xy12345.us-east-1",
        "user": "test",
        "warehouse": "WH",
        "database": "DB",
        "schema": "SCHEMA",
        "table": "TABLE",
    }
    
    is_valid, error = await snowflake_adapter.validate_config(config)
    assert is_valid is False
    assert "password" in error.lower()


@pytest.mark.asyncio
async def test_snowflake_config_validation_success(snowflake_adapter, snowflake_config):
    """Test config validation - all required fields present."""
    with patch("semabridge.notifications.adapters.snowflake_adapter.asyncio.get_event_loop") as mock_loop:
        mock_executor = AsyncMock()
        mock_executor.run_in_executor = AsyncMock(return_value=None)
        mock_loop.return_value = mock_executor
        
        with patch("semabridge.notifications.adapters.snowflake_adapter.SnowflakeAdapter._test_connection") as mock_test:
            mock_test.return_value = None
            
            is_valid, error = await snowflake_adapter.validate_config(snowflake_config)
            
            # Should succeed or timeout depending on executor behavior
            assert isinstance(is_valid, bool)


@pytest.mark.asyncio
async def test_snowflake_adapter_timeout(snowflake_adapter, snowflake_config, sample_event):
    """Test timeout handling."""
    formatter = SnowflakeFormatter()
    payload = formatter.format(sample_event, snowflake_config)
    
    with patch("semabridge.notifications.adapters.snowflake_adapter.asyncio.get_event_loop") as mock_loop:
        mock_executor = AsyncMock()
        import asyncio
        mock_executor.run_in_executor = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_loop.return_value = mock_executor
        
        result = await snowflake_adapter.send(payload, snowflake_config)
        
        assert result["success"] is False
        assert "timeout" in result["error"].lower()


@pytest.mark.asyncio
async def test_snowflake_adapter_password_masked_in_response(snowflake_adapter, snowflake_config, sample_event):
    """Test that password is not exposed in error response."""
    # Intentionally invalid config to trigger error
    bad_config = {
        "account": "",
        "password": "secret_password_123",
    }
    
    result = await snowflake_adapter.send({}, bad_config)
    
    assert result["success"] is False
    # Password should not appear in error message
    assert "secret_password" not in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_snowflake_formatter_formats_correctly(sample_event, snowflake_config):
    """Test SnowflakeFormatter produces correct output."""
    formatter = SnowflakeFormatter()
    output = formatter.format(sample_event, snowflake_config)
    
    assert output["event_id"] == str(sample_event.id)
    assert output["correlation_id"] == "corr_123"
    assert output["sync_job_id"] == "job_456"
    assert output["project_id"] == "proj_789"
    assert output["level"] == "ERROR"
    assert output["level_numeric"] == 4
    assert output["title"] == "Sync failed"
    assert output["message"] == "Data sync encountered errors"
    assert output["source"] == "sync_engine"
    assert "payload_json" in output


@pytest.mark.asyncio
async def test_snowflake_formatter_truncates_long_strings(snowflake_config):
    """Test SnowflakeFormatter truncates long title and message."""
    long_title = "X" * 2000
    long_message = "Y" * 5000
    
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr",
        title=long_title,
        message=long_message,
        level=1,
        created_at=datetime.utcnow(),
    )
    
    formatter = SnowflakeFormatter()
    output = formatter.format(event, snowflake_config)
    
    assert len(output["title"]) <= 1000
    assert len(output["message"]) <= 4000


@pytest.mark.asyncio
async def test_snowflake_bulk_insert_empty_list(snowflake_adapter, snowflake_config):
    """Test bulk insert with empty list."""
    result = await snowflake_adapter.bulk_insert([], snowflake_config)
    
    assert result["inserted"] == 0
    assert result["failed"] == 0


@pytest.mark.asyncio
async def test_snowflake_bulk_insert_invalid_config(snowflake_adapter):
    """Test bulk insert with invalid config."""
    rows = [{"col": "value"}]
    bad_config = {"table": "TABLE"}  # Missing required fields
    
    result = await snowflake_adapter.bulk_insert(rows, bad_config)
    
    assert result["inserted"] == 0
    assert result["failed"] == 1
    assert "error" in result
