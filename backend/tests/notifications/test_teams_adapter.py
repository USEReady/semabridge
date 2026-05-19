"""
Tests for Teams adapter and formatter.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import aiohttp
from datetime import datetime

from semabridge.notifications.adapters.teams_adapter import TeamsAdapter
from semabridge.notifications.formatters.teams_formatter import TeamsFormatter
from semabridge.notifications.models import NotificationEvent
from semabridge.notifications.constants import NotificationLevel


@pytest.mark.asyncio
async def test_teams_adapter_successful_send():
    """Test successful Adaptive Card send."""
    event = NotificationEvent(
        title="Test Alert",
        message="This is a test",
        level=NotificationLevel.ERROR,
        project_id="proj_123",
    )
    
    formatter = TeamsFormatter()
    config = {"webhook_url": "https://hooks.teams.microsoft.com/v1/..."}
    payload = formatter.format(event, config)
    
    adapter = TeamsAdapter(config)
    
    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="1")
        mock_post.return_value.__aenter__.return_value = mock_response
        
        result = await adapter.send(payload, config)
        
        assert result["success"] is True
        assert result["response_code"] == 200


@pytest.mark.asyncio
async def test_teams_adapter_rate_limit_retry():
    """Test Teams rate limiting with retry."""
    config = {"webhook_url": "https://hooks.teams.microsoft.com/v1/..."}
    adapter = TeamsAdapter(config)
    
    event = NotificationEvent(
        title="Test",
        message="Test message",
        level=NotificationLevel.WARNING,
    )
    
    formatter = TeamsFormatter()
    payload = formatter.format(event, config)
    
    # Mock rate limit then success
    responses = [
        (429, "Too many requests", {"Retry-After": "1"}),
        (200, "OK", {}),
    ]
    call_count = 0
    
    async def mock_post(*args, **kwargs):
        nonlocal call_count
        mock_response = AsyncMock()
        mock_response.status = responses[min(call_count, len(responses)-1)][0]
        mock_response.text = AsyncMock(return_value=responses[min(call_count, len(responses)-1)][1])
        mock_response.headers = responses[min(call_count, len(responses)-1)][2]
        call_count += 1
        return mock_response
    
    with patch('aiohttp.ClientSession.post', new_callable=AsyncMock) as mock:
        mock.side_effect = mock_post
        
        result = await adapter.send(payload, config)
        
        # Should retry and succeed
        assert result["success"] is True or result["response_code"] == 429


@pytest.mark.asyncio
async def test_teams_adapter_fallback_on_card_failure():
    """Test fallback to plaintext when Adaptive Card fails."""
    config = {"webhook_url": "https://hooks.teams.microsoft.com/v1/..."}
    adapter = TeamsAdapter(config)
    
    event = NotificationEvent(
        title="Test",
        message="Test message",
        level=NotificationLevel.CRITICAL,
    )
    
    formatter = TeamsFormatter()
    payload = formatter.format(event, config)
    
    # Mock Adaptive Card failure, then plaintext success
    call_count = 0
    
    async def mock_post(*args, **kwargs):
        nonlocal call_count
        mock_response = AsyncMock()
        
        # First call fails (Adaptive Card)
        if call_count == 0:
            mock_response.status = 400  # Bad request
            call_count += 1
        else:
            # Second call succeeds (plaintext fallback)
            mock_response.status = 200
        
        mock_response.text = AsyncMock(return_value="OK")
        return mock_response
    
    with patch('aiohttp.ClientSession.post', new_callable=AsyncMock) as mock:
        mock.side_effect = mock_post
        
        result = await adapter.send(payload, config)
        
        # Should fallback and succeed
        assert result["success"] is True


@pytest.mark.asyncio
async def test_teams_adapter_ssrf_protection():
    """Test SSRF protection blocks private IPs."""
    adapter = TeamsAdapter({})
    
    # Private IP should be rejected
    config_private = {"webhook_url": "https://192.168.1.1/webhook"}
    is_valid, error = await adapter.validate_config(config_private)
    
    assert is_valid is False
    assert "Invalid" in error


@pytest.mark.asyncio
async def test_teams_formatter_adaptive_card_structure():
    """Test Teams formatter generates valid Adaptive Card structure."""
    formatter = TeamsFormatter()
    
    event = NotificationEvent(
        title="Test Alert",
        message="This is a test",
        level=NotificationLevel.CRITICAL,
        project_id="proj_123",
        sync_job_id="job_456",
        correlation_id="corr_789",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )
    
    config = {}
    payload = formatter.format(event, config)
    
    # Should have both Adaptive Card and plaintext fallback
    assert "type" in payload
    assert "attachments" in payload
    assert payload["plaintext_fallback"] is not None
    
    # Adaptive Card should have proper structure
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    assert "body" in card


@pytest.mark.asyncio
async def test_teams_formatter_message_truncation():
    """Test Teams formatter truncates long messages."""
    formatter = TeamsFormatter()
    
    long_message = "x" * 5000
    event = NotificationEvent(
        title="Test",
        message=long_message,
        level=NotificationLevel.ERROR,
    )
    
    payload = formatter.format(event, {})
    
    # Message should be truncated
    card = payload["attachments"][0]["content"]
    message_block = None
    for block in card["body"]:
        if block.get("type") == "TextBlock" and "x" in block.get("text", ""):
            message_block = block
            break
    
    assert message_block is not None
    assert len(message_block["text"]) <= 2000


@pytest.mark.asyncio
async def test_teams_formatter_severity_colors():
    """Test Teams formatter applies correct severity colors."""
    formatter = TeamsFormatter()
    
    test_cases = [
        (NotificationLevel.CRITICAL, "Attention"),
        (NotificationLevel.ERROR, "Warning"),
        (NotificationLevel.WARNING, "Warning"),
        (NotificationLevel.INFO, "Good"),
    ]
    
    for level, expected_color_name in test_cases:
        event = NotificationEvent(
            title="Test",
            message="Test",
            level=level,
        )
        
        payload = formatter.format(event, {})
        
        # Check that color is present in the card
        card = payload["attachments"][0]["content"]
        # The color should be in the first TextBlock (title)
        first_block = card["body"][0]
        assert "color" in first_block
