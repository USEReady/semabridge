"""
Tests for Routing Service rule-based routing.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from semabridge.notifications.services.routing_service import RoutingService
from semabridge.notifications.models import NotificationEvent
from semabridge.notifications.models.routing import (
    NotificationRoutingRule,
    RoutingConditions,
)


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    return MagicMock()


@pytest.fixture
def routing_service(mock_db_session):
    """Routing service fixture."""
    return RoutingService(mock_db_session)


@pytest.fixture
def sample_event():
    """Sample notification event."""
    return NotificationEvent(
        id=uuid4(),
        correlation_id="corr_123",
        sync_job_id="job_456",
        project_id="prod-web",
        type="sync_completion",
        level=4,  # ERROR
        title="Production Sync Failed",
        message="Data sync encountered errors",
        payload={"error_count": 5},
        source="sync_engine",
    )


def test_routing_rule_matches_by_level_mask(sample_event):
    """Test routing rule matches by level mask."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Errors only",
        priority=1,
        conditions=RoutingConditions(level_mask=4),  # ERROR
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is True


def test_routing_rule_no_match_wrong_level(sample_event):
    """Test routing rule doesn't match wrong level."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Critical only",
        priority=1,
        conditions=RoutingConditions(level_mask=2),  # CRITICAL
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is False


def test_routing_rule_matches_by_project_id(sample_event):
    """Test routing rule matches by project ID."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Prod only",
        priority=1,
        conditions=RoutingConditions(project_ids=["prod-web", "prod-api"]),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is True


def test_routing_rule_no_match_wrong_project(sample_event):
    """Test routing rule doesn't match wrong project."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Dev only",
        priority=1,
        conditions=RoutingConditions(project_ids=["dev-web"]),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is False


def test_routing_rule_matches_source_pattern(sample_event):
    """Test routing rule matches source pattern with wildcard."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Sync engine events",
        priority=1,
        conditions=RoutingConditions(source_pattern="sync_*"),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is True


def test_routing_rule_matches_title_contains(sample_event):
    """Test routing rule matches title contains (case-insensitive)."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Production errors",
        priority=1,
        conditions=RoutingConditions(title_contains="production"),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is True


def test_routing_rule_matches_title_case_insensitive(sample_event):
    """Test routing rule title match is case-insensitive."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Production errors",
        priority=1,
        conditions=RoutingConditions(title_contains="PRODUCTION"),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is True


def test_routing_rule_matches_payload_key_exists(sample_event):
    """Test routing rule checks payload key exists."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Has errors",
        priority=1,
        conditions=RoutingConditions(payload_key_exists="error_count"),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is True


def test_routing_rule_no_match_payload_key_missing(sample_event):
    """Test routing rule doesn't match missing payload key."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Has warnings",
        priority=1,
        conditions=RoutingConditions(payload_key_exists="warning_count"),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is False


def test_routing_rule_matches_payload_value(sample_event):
    """Test routing rule matches payload value."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="5 errors",
        priority=1,
        conditions=RoutingConditions(
            payload_value_matches={"key": "error_count", "value": 5}
        ),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is True


def test_routing_rule_conditions_anded(sample_event):
    """Test multiple conditions are ANDed."""
    rule = NotificationRoutingRule(
        id="rule1",
        name="Prod errors",
        priority=1,
        conditions=RoutingConditions(
            level_mask=4,  # ERROR
            project_ids=["prod-web"],
        ),
        channel_ids=["ch1"],
    )
    
    assert rule.matches(sample_event) is True
    
    # Change project to not match
    sample_event.project_id = "staging"
    assert rule.matches(sample_event) is False


@pytest.mark.asyncio
async def test_routing_service_legacy_fallback(routing_service, mock_db_session, sample_event):
    """Test routing service falls back to legacy routing when no rules match."""
    # Mock empty rules and channels
    mock_channel = MagicMock()
    mock_channel.enabled = True
    mock_channel.status.value = "ACTIVE"
    mock_channel.level_mask = 7  # SYNC_RESULT | CRITICAL | ERROR
    mock_channel.project_scope = None
    mock_channel.id = uuid4()
    mock_channel.name = "Default Slack"
    mock_channel.channel_type.value = "slack"
    mock_channel.config_json = "{}"
    mock_channel.quiet_hours_enabled = False
    mock_channel.timezone = "UTC"
    mock_channel.digest_enabled = False
    
    mock_query = MagicMock()
    mock_query.filter.return_value.all.return_value = [mock_channel]
    mock_db_session.query.return_value = mock_query
    
    channels = await routing_service.get_matching_channels(sample_event)
    
    # Should have found the channel via legacy routing
    assert len(channels) > 0


@pytest.mark.asyncio
async def test_routing_service_get_channel_by_id(routing_service, mock_db_session):
    """Test get_channel_by_id retrieves channel details."""
    channel_id = uuid4()
    
    mock_channel = MagicMock()
    mock_channel.id = channel_id
    mock_channel.name = "Test Channel"
    mock_channel.channel_type.value = "slack"
    mock_channel.config_json = "{}"
    mock_channel.level_mask = 63
    mock_channel.project_scope = None
    mock_channel.quiet_hours_enabled = False
    mock_channel.quiet_hours_start = None
    mock_channel.quiet_hours_end = None
    mock_channel.timezone = "UTC"
    mock_channel.digest_enabled = False
    mock_channel.status.value = "ACTIVE"
    mock_channel.enabled = True
    
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = mock_channel
    mock_db_session.query.return_value = mock_query
    
    channel = await routing_service.get_channel_by_id(str(channel_id))
    
    assert channel is not None
    assert channel["id"] == str(channel_id)
    assert channel["name"] == "Test Channel"


@pytest.mark.asyncio
async def test_get_channels_by_ids_filters_inactive(routing_service, mock_db_session):
    """Test _get_channels_by_ids filters out inactive channels."""
    channel_id = uuid4()
    
    mock_channel = MagicMock()
    mock_channel.id = channel_id
    mock_channel.name = "Disabled Channel"
    mock_channel.channel_type.value = "slack"
    mock_channel.config_json = "{}"
    mock_channel.level_mask = 63
    mock_channel.project_scope = None
    mock_channel.quiet_hours_enabled = False
    mock_channel.quiet_hours_start = None
    mock_channel.quiet_hours_end = None
    mock_channel.timezone = "UTC"
    mock_channel.digest_enabled = False
    mock_channel.status.value = "DISABLED"  # Disabled
    mock_channel.enabled = True
    
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = mock_channel
    mock_db_session.query.return_value = mock_query
    
    channels = await routing_service._get_channels_by_ids([str(channel_id)])
    
    # Should filter out disabled channel
    assert len(channels) == 0
