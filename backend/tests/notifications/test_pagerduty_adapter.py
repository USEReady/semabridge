"""
Tests for PagerDuty adapter and formatter.
"""

import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime
import hashlib

from semabridge.notifications.adapters.pagerduty_adapter import PagerDutyAdapter
from semabridge.notifications.formatters.pagerduty_formatter import PagerDutyFormatter
from semabridge.notifications.models import NotificationEvent
from semabridge.notifications.constants import NotificationLevel


@pytest.mark.asyncio
async def test_pagerduty_adapter_trigger():
    """Test PagerDuty trigger event send."""
    config = {"routing_key": "1234567890abcdef1234567890abcdef"}
    adapter = PagerDutyAdapter(config)
    
    formatter = PagerDutyFormatter()
    event = NotificationEvent(
        title="Service Down",
        message="Database connection failed",
        level=NotificationLevel.CRITICAL,
        sync_job_id="job_123",
        project_id="proj_xyz",
    )
    
    payload = formatter.format(event, config)
    
    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 202
        mock_response.text = AsyncMock(return_value='{"status":"success"}')
        mock_post.return_value.__aenter__.return_value = mock_response
        
        result = await adapter.send(payload, config)
        
        assert result["success"] is True
        assert result["response_code"] == 202


@pytest.mark.asyncio
async def test_pagerduty_adapter_timeout():
    """Test PagerDuty timeout handling."""
    config = {"routing_key": "1234567890abcdef1234567890abcdef"}
    adapter = PagerDutyAdapter(config)
    
    formatter = PagerDutyFormatter()
    event = NotificationEvent(
        title="Test",
        message="Test message",
        level=NotificationLevel.ERROR,
    )
    
    payload = formatter.format(event, config)
    
    with patch('aiohttp.ClientSession.post') as mock_post:
        import asyncio
        mock_post.return_value.__aenter__.side_effect = asyncio.TimeoutError()
        
        result = await adapter.send(payload, config)
        
        assert result["success"] is False
        assert "Timeout" in result.get("error", "")


@pytest.mark.asyncio
async def test_pagerduty_formatter_trigger_critical():
    """Test PagerDuty formatter triggers on CRITICAL."""
    formatter = PagerDutyFormatter()
    
    event = NotificationEvent(
        title="Critical Alert",
        message="System failure detected",
        level=NotificationLevel.CRITICAL,
        sync_job_id="job_123",
        project_id="proj_xyz",
        correlation_id="corr_abc",
    )
    
    payload = formatter.format(event, {})
    
    assert payload["event_action"] == "trigger"
    assert payload["payload"]["severity"] == "critical"
    assert payload["payload"]["summary"] == "Critical Alert"
    assert "sync_job_id" in payload["payload"]["custom_details"]


@pytest.mark.asyncio
async def test_pagerduty_formatter_resolve_on_success():
    """Test PagerDuty formatter resolves incident on success."""
    formatter = PagerDutyFormatter()
    
    event = NotificationEvent(
        title="Sync Complete",
        message="Sync completed successfully with 0 errors",
        level=NotificationLevel.SYNC_RESULT,
        type="sync_completion",
        sync_job_id="job_123",
    )
    
    payload = formatter.format(event, {})
    
    # Should be a resolve action (success case)
    assert payload["event_action"] in ["trigger", "resolve"]
    # If it detects success keywords, it should resolve
    if "success" in event.message.lower():
        assert payload["event_action"] == "resolve"


@pytest.mark.asyncio
async def test_pagerduty_formatter_dedup_key():
    """Test PagerDuty formatter generates consistent dedup keys."""
    formatter = PagerDutyFormatter()
    
    event1 = NotificationEvent(
        title="Sync Error",
        message="Error 1",
        level=NotificationLevel.ERROR,
        sync_job_id="job_123",
        project_id="proj_xyz",
    )
    
    event2 = NotificationEvent(
        title="Sync Error",
        message="Error 2",  # Different message
        level=NotificationLevel.ERROR,
        sync_job_id="job_123",
        project_id="proj_xyz",
    )
    
    payload1 = formatter.format(event1, {})
    payload2 = formatter.format(event2, {})
    
    # Same dedup key means they'll be part of the same incident
    assert payload1["dedup_key"] == payload2["dedup_key"]


@pytest.mark.asyncio
async def test_pagerduty_formatter_severity_levels():
    """Test PagerDuty formatter maps severity levels correctly."""
    formatter = PagerDutyFormatter()
    
    test_cases = [
        (NotificationLevel.CRITICAL, "critical"),
        (NotificationLevel.ERROR, "error"),
        (NotificationLevel.WARNING, "warning"),
        (NotificationLevel.INFO, "info"),
    ]
    
    for level, expected_severity in test_cases:
        event = NotificationEvent(
            title="Test",
            message="Test message",
            level=level,
        )
        
        payload = formatter.format(event, {})
        assert payload["payload"]["severity"] == expected_severity


@pytest.mark.asyncio
async def test_pagerduty_adapter_missing_routing_key():
    """Test PagerDuty adapter rejects missing routing key."""
    adapter = PagerDutyAdapter({})
    
    config = {}  # Missing routing_key
    is_valid, error = await adapter.validate_config(config)
    
    assert is_valid is False
    assert "routing_key" in error


@pytest.mark.asyncio
async def test_pagerduty_adapter_invalid_routing_key():
    """Test PagerDuty adapter validates routing key length."""
    adapter = PagerDutyAdapter({})
    
    config = {"routing_key": "too_short"}
    is_valid, error = await adapter.validate_config(config)
    
    assert is_valid is False


@pytest.mark.asyncio
async def test_pagerduty_formatter_message_truncation():
    """Test PagerDuty formatter truncates long messages."""
    formatter = PagerDutyFormatter()
    
    long_message = "x" * 5000
    event = NotificationEvent(
        title="Test",
        message=long_message,
        level=NotificationLevel.ERROR,
    )
    
    payload = formatter.format(event, {})
    
    # Message should be truncated to 1000
    assert len(payload["payload"]["custom_details"]["message"]) <= 1000


@pytest.mark.asyncio
async def test_pagerduty_formatter_custom_details():
    """Test PagerDuty formatter includes all custom details."""
    formatter = PagerDutyFormatter()
    
    event = NotificationEvent(
        title="Sync Failed",
        message="Database error",
        level=NotificationLevel.CRITICAL,
        sync_job_id="job_xyz",
        project_id="proj_123",
        correlation_id="corr_abc",
        source="sync_engine",
    )
    
    payload = formatter.format(event, {})
    
    details = payload["payload"]["custom_details"]
    assert details["sync_job_id"] == "job_xyz"
    assert details["project_id"] == "proj_123"
    assert details["correlation_id"] == "corr_abc"
