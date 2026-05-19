"""
Batching service for aggregating notifications within time windows.

Prevents notification storms by batching similar events.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import redis

from ...constants import DEFAULT_BATCH_WINDOW_SEC, NotificationLevel
from ..models import NotificationEvent

logger = logging.getLogger(__name__)


class BatchingService:
    """
    Batch notifications to prevent storms.
    
    Aggregates WARNING and ERROR events per sync_job_id within a window.
    CRITICAL events always bypass batching.
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        batch_window_sec: int = DEFAULT_BATCH_WINDOW_SEC
    ):
        """
        Initialize batching service.
        
        Args:
            redis_client: Redis client
            batch_window_sec: Window in seconds for batching
        """
        self.redis = redis_client
        self.batch_window_sec = batch_window_sec
        self.prefix = "semabridge:batch:"
    
    async def should_batch(self, event: NotificationEvent) -> bool:
        """
        Determine if an event should be batched vs. sent immediately.
        
        CRITICAL always bypasses batching.
        WARNING and ERROR are candidates for batching.
        
        Args:
            event: Notification event
        
        Returns:
            True if should batch, False if send immediately
        """
        # CRITICAL bypasses batching
        if event.level & NotificationLevel.CRITICAL:
            return False
        
        # WARNING and ERROR are candidates
        if not (event.level & (NotificationLevel.WARNING | NotificationLevel.ERROR)):
            return False
        
        # Must have a sync_job_id to batch
        if not event.sync_job_id:
            return False
        
        return True
    
    async def add_to_batch(self, event: NotificationEvent, channel_id: str) -> bool:
        """
        Add event to batch queue.
        
        Args:
            event: Notification event
            channel_id: Channel to batch for
        
        Returns:
            True if successful
        """
        if not event.sync_job_id:
            return False
        
        key = f"{self.prefix}{event.sync_job_id}:{channel_id}"
        
        try:
            batch_item = {
                "event_id": str(event.id),
                "level": event.level,
                "title": event.title,
                "message": event.message[:500],  # Truncate
                "added_at": datetime.utcnow().isoformat(),
            }
            
            # Add to set (deduplicates by title within window)
            self.redis.lpush(key, json.dumps(batch_item))
            
            # Set expiry on first add
            self.redis.expire(key, self.batch_window_sec)
            
            logger.debug(f"Added event to batch for {event.sync_job_id}:{channel_id}")
            return True
        except redis.RedisError as e:
            logger.error(f"Redis error adding to batch: {e}")
            return False
    
    async def get_batch(self, sync_job_id: str, channel_id: str) -> Optional[Dict]:
        """
        Retrieve and clear batch for a job.
        
        Args:
            sync_job_id: Sync job ID
            channel_id: Channel ID
        
        Returns:
            Batch data or None if empty
        """
        key = f"{self.prefix}{sync_job_id}:{channel_id}"
        
        try:
            # Get all items
            items_raw = self.redis.lrange(key, 0, -1)
            if not items_raw:
                return None
            
            items = []
            for item_raw in items_raw:
                try:
                    item = json.loads(item_raw)
                    items.append(item)
                except json.JSONDecodeError:
                    pass
            
            # Count by level
            warning_count = sum(1 for item in items if item.get("level") & NotificationLevel.WARNING)
            error_count = sum(1 for item in items if item.get("level") & NotificationLevel.ERROR)
            
            # Delete key
            self.redis.delete(key)
            
            return {
                "sync_job_id": sync_job_id,
                "warning_count": warning_count,
                "error_count": error_count,
                "items": items,
            }
        except redis.RedisError as e:
            logger.error(f"Redis error getting batch: {e}")
            return None
    
    async def is_batching_enabled(self) -> bool:
        """Check if batching is enabled."""
        return self.batch_window_sec > 0
