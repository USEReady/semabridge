"""
Routing service for determining which channels should receive notifications.
"""

import logging
import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import redis

from sqlalchemy.orm import Session

from ..constants import matches_level, ChannelStatus
from ..models import NotificationChannel, NotificationEvent

logger = logging.getLogger(__name__)


class RoutingService:
    """
    Route notifications to appropriate channels based on:
    1. Rule-based routing (if configured)
    2. Level mask matching (legacy fallback)
    3. Project scope matching (legacy fallback)
    4. Channel status and enablement
    """
    
    def __init__(self, db_session: Session, redis_url: Optional[str] = None):
        """
        Initialize routing service.
        
        Args:
            db_session: SQLAlchemy session
            redis_url: Optional Redis URL for rule caching
        """
        self.db = db_session
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.rule_cache_ttl = int(os.getenv("ROUTING_RULES_CACHE_TTL_SEC", "60"))
        try:
            self.redis = redis.from_url(self.redis_url)
        except Exception:
            self.redis = None
    
    async def get_matching_channels(
        self,
        event: NotificationEvent
    ) -> List[Dict[str, Any]]:
        """
        Get all channels that should receive this event.
        
        Merges rule-based routing channels with legacy fallback channels,
        preventing duplicate channel selection.
        
        Args:
            event: Notification event
        
        Returns:
            List of channel dicts with config
        """
        try:
            # 1. Evaluate active routing rules
            rule_channel_ids = await self._evaluate_routing_rules(event)
            rule_channels = await self._get_channels_by_ids(rule_channel_ids)
            
            # 2. Get legacy level-mask + project-scope routing channels
            legacy_channels = await self._legacy_route(event)
            
            # 3. Merge them and prevent duplicate channel selection
            merged_channels = []
            seen_channel_ids = set()
            
            # Add rule-based channels first (they have priority)
            for channel in rule_channels:
                ch_id = channel.get("id")
                if ch_id and ch_id not in seen_channel_ids:
                    seen_channel_ids.add(ch_id)
                    merged_channels.append(channel)
            
            # Add legacy fallback channels if not already matched
            for channel in legacy_channels:
                ch_id = channel.get("id")
                if ch_id and ch_id not in seen_channel_ids:
                    seen_channel_ids.add(ch_id)
                    merged_channels.append(channel)
            
            logger.debug(
                f"Routed event {event.id} to {len(merged_channels)} channels "
                f"(rule-based: {len(rule_channels)}, legacy: {len(legacy_channels)})"
            )
            return merged_channels
        
        except Exception as e:
            logger.error(f"Failed to route event: {e}", exc_info=True)
            return []
    
    async def _evaluate_routing_rules(self, event: NotificationEvent) -> List[str]:
        """
        Evaluate routing rules and return matching channel IDs.
        
        Args:
            event: Notification event
        
        Returns:
            List of channel IDs (deduplicated)
        """
        try:
            from ..models import NotificationRoutingRuleRow, NotificationRoutingRule, RoutingConditions
            
            # Query active rules ordered by priority ascending (lower is higher priority)
            rules_rows = self.db.query(NotificationRoutingRuleRow).filter(
                NotificationRoutingRuleRow.enabled == True
            ).order_by(NotificationRoutingRuleRow.priority.asc()).all()
            
            if not rules_rows:
                return []
                
            matched_channel_ids = []
            
            for rule_row in rules_rows:
                cond_dict = rule_row.conditions or {}
                conditions = RoutingConditions(
                    level_mask=cond_dict.get("level_mask"),
                    project_ids=cond_dict.get("project_ids", []),
                    source_pattern=cond_dict.get("source_pattern"),
                    title_contains=cond_dict.get("title_contains"),
                    payload_key_exists=cond_dict.get("payload_key_exists"),
                    payload_value_matches=cond_dict.get("payload_value_matches"),
                )
                
                rule = NotificationRoutingRule(
                    id=str(rule_row.id),
                    name=rule_row.name,
                    priority=rule_row.priority,
                    conditions=conditions,
                    channel_ids=rule_row.channel_ids or [],
                    stop_on_match=rule_row.stop_on_match,
                    enabled=rule_row.enabled,
                )
                
                if rule.matches(event):
                    matched_channel_ids.extend(rule.channel_ids)
                    logger.debug(f"Event {event.id} matched routing rule '{rule.name}' targeting channels {rule.channel_ids}")
                    if rule.stop_on_match:
                        logger.debug(f"Rule '{rule.name}' has stop_on_match=True, stopping evaluation")
                        break
            
            # Deduplicate matched channel IDs while preserving order
            seen = set()
            unique_channel_ids = []
            for ch_id in matched_channel_ids:
                if ch_id not in seen:
                    seen.add(ch_id)
                    unique_channel_ids.append(ch_id)
                    
            return unique_channel_ids
            
        except Exception as e:
            logger.error(f"Failed to evaluate routing rules: {e}", exc_info=True)
            return []
    
    async def _legacy_route(self, event: NotificationEvent) -> List[Dict[str, Any]]:
        """
        Legacy routing using level mask and project scope.
        
        Args:
            event: Notification event
        
        Returns:
            List of matching channels
        """
        try:
            query = self.db.query(NotificationChannel).filter(
                NotificationChannel.enabled == True,
                NotificationChannel.status == ChannelStatus.ACTIVE,
            )
            
            matching_channels = []
            
            for channel in query.all():
                # Check level mask
                if not matches_level(event.level, channel.level_mask):
                    continue
                
                # Check project scope
                if channel.project_scope and channel.project_scope != event.project_id:
                    continue
                
                matching_channels.append({
                    "id": str(channel.id),
                    "name": channel.name,
                    "channel_type": channel.channel_type.value,
                    "config_json": channel.config_json,
                    "quiet_hours_enabled": channel.quiet_hours_enabled,
                    "quiet_hours_start": channel.quiet_hours_start,
                    "quiet_hours_end": channel.quiet_hours_end,
                    "timezone": channel.timezone,
                    "digest_enabled": channel.digest_enabled,
                })
            
            return matching_channels
        
        except Exception as e:
            logger.error(f"Legacy routing failed: {e}")
            return []
    
    async def _get_channels_by_ids(self, channel_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Get channel details for a list of channel IDs.
        
        Args:
            channel_ids: List of channel UUIDs
        
        Returns:
            List of channel dicts
        """
        if not channel_ids:
            return []
        
        channels = []
        for ch_id in channel_ids:
            channel = await self.get_channel_by_id(ch_id)
            if channel and channel.get("enabled") and channel.get("status") == ChannelStatus.ACTIVE:
                channels.append(channel)
        
        return channels
    
    async def get_channel_by_id(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific channel by ID.
        
        Args:
            channel_id: Channel UUID
        
        Returns:
            Channel dict or None
        """
        try:
            from uuid import UUID as pyUUID
            ch_uuid = pyUUID(str(channel_id)) if isinstance(channel_id, str) else channel_id
            channel = self.db.query(NotificationChannel).filter(
                NotificationChannel.id == ch_uuid
            ).first()
            
            if not channel:
                return None
            
            return {
                "id": str(channel.id),
                "name": channel.name,
                "channel_type": channel.channel_type.value,
                "config_json": channel.config_json,
                "level_mask": channel.level_mask,
                "project_scope": channel.project_scope,
                "quiet_hours_enabled": channel.quiet_hours_enabled,
                "quiet_hours_start": channel.quiet_hours_start,
                "quiet_hours_end": channel.quiet_hours_end,
                "timezone": channel.timezone,
                "digest_enabled": channel.digest_enabled,
                "status": channel.status.value,
                "enabled": channel.enabled,
            }
        
        except Exception as e:
            logger.error(f"Failed to get channel: {e}")
            return None
