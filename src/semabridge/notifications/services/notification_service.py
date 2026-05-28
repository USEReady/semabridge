"""
Main notification service - entry point for sync engine.

The sync engine calls notification_service.emit(event) to trigger notifications.
This method returns immediately without blocking.
"""

import logging
from typing import Optional

import redis

from ..constants import RedisQueues
from ..models import NotificationEvent
from ..queue.redis_streams import RedisStreamsQueue

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Central notification service.
    
    Primary interface for other parts of the system.
    Emits events asynchronously without blocking the caller.
    """
    
    def __init__(self, redis_url: str):
        """
        Initialize notification service.
        
        Args:
            redis_url: Redis connection URL
        """
        self.redis = redis.from_url(redis_url)
        self.queue = RedisStreamsQueue(redis_url)
    
    async def emit(self, event: NotificationEvent) -> bool:
        """
        Emit a notification event.
        
        This method returns immediately. Delivery is handled asynchronously
        by background workers. The sync engine never blocks on notification delivery.
        
        Args:
            event: NotificationEvent to emit
        
        Returns:
            True if queued successfully, False if queue is unavailable
            (returning False does not block - caller can continue)
        """
        try:
            # Serialize event
            payload = event.to_dict()
            
            # Enqueue to main notification queue
            logger.warning("SYNC_NOTIFICATION_TRACE: redis xadd invoked")
            message_id = self.queue.enqueue(RedisQueues.NOTIFICATIONS, payload)
            
            logger.info(f"Emitted event {event.id} (correlation: {event.correlation_id})")
            return message_id is not None
        
        except Exception as e:
            logger.error(f"Failed to emit notification: {e}")
            # Fail gracefully - do not raise, do not block sync engine
            return False
            
    def emit_sync(self, event: NotificationEvent) -> bool:
        """
        Emit a notification event synchronously.
        
        Args:
            event: NotificationEvent to emit
        
        Returns:
            True if queued successfully, False if queue is unavailable
        """
        try:
            # Serialize event
            payload = event.to_dict()
            
            # Enqueue to main notification queue
            logger.warning("SYNC_NOTIFICATION_TRACE: redis xadd invoked")
            message_id = self.queue.enqueue(RedisQueues.NOTIFICATIONS, payload)
            
            logger.info({
                "redis_notification_publish": True,
                "stream": RedisQueues.NOTIFICATIONS,
                "event_title": event.title,
            })
            
            logger.info(f"Emitted event {event.id} (correlation: {event.correlation_id}) synchronously")
            return message_id is not None
        
        except Exception as e:
            logger.error(f"Failed to emit notification synchronously: {e}")
            # Fail gracefully - do not raise, do not block sync engine
            return False
    
    def health_check(self) -> bool:
        """
        Check notification system health.
        
        Returns:
            True if Redis is accessible
        """
        try:
            self.redis.ping()
            return True
        except redis.RedisError:
            return False
    
    def get_queue_depth(self) -> int:
        """
        Get number of pending messages in main queue.
        
        Returns:
            Number of queued messages
        """
        try:
            return self.queue.get_pending_count(
                RedisQueues.NOTIFICATIONS,
                "dispatcher"
            )
        except Exception as e:
            logger.error(f"Failed to get queue depth: {e}")
            return -1
