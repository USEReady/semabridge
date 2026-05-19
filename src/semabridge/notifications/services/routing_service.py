"""
Routing service for determining which channels should receive notifications.
"""

import logging
import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import redis

from sqlalchemy.orm import Session

from ...constants import matches_level, ChannelStatus
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
        
        Routing order:
        1. Evaluate routing rules (if any enabled)
        2. Fall back to level mask + project scope matching
        3. Filter by channel status and enablement
        
        Args:
            event: Notification event
        
        Returns:
            List of channel dicts with config
        """
        try:
            # Try rule-based routing first
            channel_ids = await self._evaluate_routing_rules(event)
            
            # If rules matched, use those channels
            if channel_ids:
                logger.debug(f"Routing event {event.id} via rules to {len(channel_ids)} channels")
                return await self._get_channels_by_ids(channel_ids)
            
            # Fall back to legacy level-mask + project-scope routing
            logger.debug(f"No routing rules matched for event {event.id}, using legacy routing")
            return await self._legacy_route(event)
        
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
        # In production, would query:
        # SELECT * FROM notification_routing_rules WHERE enabled=true
        # ORDER BY priority ASC
        
        # For now, return empty (legacy routing used)
        # This will be populated once routing_rules table is created via migration
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
            if channel and channel.get("enabled") and channel.get("status") == ChannelStatus.ACTIVE.value:
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
            channel = self.db.query(NotificationChannel).filter(
                NotificationChannel.id == channel_id
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
