"""
Integration tests for Notification Routing Rules and legacy routing merges.
"""

import pytest
from uuid import uuid4
from semabridge.notifications.services.routing_service import RoutingService
from semabridge.notifications.models import (
    NotificationEvent,
    NotificationChannel,
    NotificationRoutingRuleRow,
    ChannelTypeEnum,
    ChannelStatusEnum,
)


@pytest.fixture
def real_routing_service(db_session):
    """RoutingService with a real in-memory SQLite DB session."""
    return RoutingService(db_session)


@pytest.mark.asyncio
async def test_routing_rules_priority_and_matching(real_routing_service, db_session):
    """Test that routing rules are evaluated in priority order and match correctly."""
    # 1. Setup channels
    ch1_id = uuid4()
    ch2_id = uuid4()
    
    channel1 = NotificationChannel(
        id=ch1_id,
        name="Slack Critical",
        channel_type=ChannelTypeEnum.SLACK,
        enabled=True,
        config_json="{}",
        level_mask=63,
        status=ChannelStatusEnum.ACTIVE,
    )
    channel2 = NotificationChannel(
        id=ch2_id,
        name="Email Ops",
        channel_type=ChannelTypeEnum.EMAIL,
        enabled=True,
        config_json="{}",
        level_mask=63,
        status=ChannelStatusEnum.ACTIVE,
    )
    db_session.add_all([channel1, channel2])
    
    # 2. Setup routing rules
    # Rule 1: High priority (priority=5), matches source "sync_engine", routes to ch1
    rule1 = NotificationRoutingRuleRow(
        id=uuid4(),
        name="Sync engine rule",
        priority=5,
        enabled=True,
        conditions={"source_pattern": "sync_*"},
        channel_ids=[str(ch1_id)],
        stop_on_match=False,
    )
    # Rule 2: Low priority (priority=10), matches level=4 (ERROR), routes to ch2
    rule2 = NotificationRoutingRuleRow(
        id=uuid4(),
        name="Errors only rule",
        priority=10,
        enabled=True,
        conditions={"level_mask": 4},
        channel_ids=[str(ch2_id)],
        stop_on_match=False,
    )
    db_session.add_all([rule1, rule2])
    db_session.commit()
    
    # 3. Create a matching event (ERROR=4, source="sync_engine")
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr_1",
        sync_job_id="job_1",
        project_id="proj_1",
        type="sync_err",
        level=4,  # ERROR
        title="Sync Failed",
        message="Failure message",
        payload={},
        source="sync_engine",
    )
    
    # 4. Route!
    channels = await real_routing_service.get_matching_channels(event)
    
    # Both rules should match and return both channels
    assert len(channels) == 2
    matched_ids = [ch["id"] for ch in channels]
    assert str(ch1_id) in matched_ids
    assert str(ch2_id) in matched_ids


@pytest.mark.asyncio
async def test_routing_rules_stop_on_match(real_routing_service, db_session):
    """Test that stop_on_match breaks evaluation of subsequent rules."""
    ch1_id = uuid4()
    ch2_id = uuid4()
    
    channel1 = NotificationChannel(id=ch1_id, name="Ch1", channel_type=ChannelTypeEnum.SLACK, config_json="{}", level_mask=2, status=ChannelStatusEnum.ACTIVE)
    channel2 = NotificationChannel(id=ch2_id, name="Ch2", channel_type=ChannelTypeEnum.SLACK, config_json="{}", level_mask=2, status=ChannelStatusEnum.ACTIVE)
    db_session.add_all([channel1, channel2])
    
    # Rule 1: priority=1, stop_on_match=True, matches sync_job_id
    rule1 = NotificationRoutingRuleRow(
        id=uuid4(),
        name="Rule 1",
        priority=1,
        enabled=True,
        conditions={"project_ids": ["prod-web"]},
        channel_ids=[str(ch1_id)],
        stop_on_match=True,  # Stops next rules!
    )
    # Rule 2: priority=2, would match level, but should be skipped due to stop_on_match in Rule 1
    rule2 = NotificationRoutingRuleRow(
        id=uuid4(),
        name="Rule 2",
        priority=2,
        enabled=True,
        conditions={"level_mask": 4},
        channel_ids=[str(ch2_id)],
        stop_on_match=False,
    )
    db_session.add_all([rule1, rule2])
    db_session.commit()
    
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr_1",
        sync_job_id="job_1",
        project_id="prod-web",
        type="sync_err",
        level=4,
        title="Sync Failed",
        message="Failure",
        payload={},
        source="sync_engine",
    )
    
    channels = await real_routing_service.get_matching_channels(event)
    
    # Only channel 1 should be returned because Rule 1 stopped evaluation
    assert len(channels) == 1
    assert channels[0]["id"] == str(ch1_id)


@pytest.mark.asyncio
async def test_routing_rules_merge_with_legacy_and_deduplicate(real_routing_service, db_session):
    """Test merging rule-based channels with legacy level-mask channels, ensuring no duplicates."""
    ch1_id = uuid4()
    ch2_id = uuid4()
    
    # Channel 1 matches rule-based routing AND legacy routing (ERROR level mask)
    channel1 = NotificationChannel(
        id=ch1_id,
        name="Slack Main",
        channel_type=ChannelTypeEnum.SLACK,
        enabled=True,
        config_json="{}",
        level_mask=4,  # Matches legacy ERROR
        status=ChannelStatusEnum.ACTIVE,
    )
    # Channel 2 matches legacy routing ONLY
    channel2 = NotificationChannel(
        id=ch2_id,
        name="Email Main",
        channel_type=ChannelTypeEnum.EMAIL,
        enabled=True,
        config_json="{}",
        level_mask=4,  # Matches legacy ERROR
        status=ChannelStatusEnum.ACTIVE,
    )
    db_session.add_all([channel1, channel2])
    
    # Rule targets channel 1 explicitly
    rule = NotificationRoutingRuleRow(
        id=uuid4(),
        name="Slack Rule",
        priority=1,
        enabled=True,
        conditions={"source_pattern": "sync_*"},
        channel_ids=[str(ch1_id)],
        stop_on_match=False,
    )
    db_session.add(rule)
    db_session.commit()
    
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr_1",
        sync_job_id="job_1",
        project_id="prod",
        type="sync_err",
        level=4,  # ERROR
        title="Sync Failed",
        message="Failure",
        payload={},
        source="sync_engine",
    )
    
    channels = await real_routing_service.get_matching_channels(event)
    
    # We should have both channel 1 and channel 2, but channel 1 must not be duplicated!
    assert len(channels) == 2
    matched_ids = [ch["id"] for ch in channels]
    assert str(ch1_id) in matched_ids
    assert str(ch2_id) in matched_ids
    # Ensure uniqueness
    assert len(set(matched_ids)) == 2
