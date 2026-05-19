"""
Dead-letter worker for handling and alerting on permanently failed messages.

Phase 2 features:
- Auto-disable channels after repeated failures
- Alert other healthy channels about failures
- Threshold-based channel disabling
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from uuid import UUID

import redis
from sqlalchemy.orm import Session

from ...constants import NotificationChannelType, NotificationStatus, ChannelStatus, RedisQueues
from ..models import NotificationEvent, NotificationChannel, NotificationLog
from ..queue.redis_streams import RedisStreamsQueue
from ..services.delivery_log_service import DeliveryLogService
from ..services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# Default threshold for auto-disabling channels
DEFAULT_DEAD_LETTER_ALERT_THRESHOLD = 5


class DeadLetterWorker:
    """
    Process dead-letter messages that have exhausted retries.
    
    Responsibilities:
    - Persist failed messages for analysis
    - Alert operators of systematic failures
    - Auto-disable channels if failure threshold exceeded
    - Alert other healthy channels about failures
    """
    
    def __init__(
        self,
        redis_url: str,
        db_session: Session,
        consumer_name: str = "dead-letter-worker",
        alert_threshold: int = DEFAULT_DEAD_LETTER_ALERT_THRESHOLD,
        notification_service: Optional[NotificationService] = None,
    ):
        """
        Initialize dead-letter worker.
        
        Args:
            redis_url: Redis connection URL
            db_session: SQLAlchemy session for DB operations
            consumer_name: Consumer name for group tracking
            alert_threshold: Number of dead-letter events to trigger auto-disable
            notification_service: Optional NotificationService for alerting ops
        """
        self.redis = redis.from_url(redis_url)
        self.queue = RedisStreamsQueue(redis_url)
        self.db = db_session
        self.consumer_name = consumer_name
        self.alert_threshold = alert_threshold
        self.notification_service = notification_service
        self.running = False
        
        # Initialize services
        self.delivery_log = DeliveryLogService(db_session)
    
    async def start(self):
        """Start the dead-letter worker."""
        self.running = True
        logger.info("Dead-letter worker started")
        
        # Create consumer group
        self.queue.create_consumer_group(
            RedisQueues.DEAD_LETTERS,
            "dead-letter",
            start_id="0"
        )
        
        try:
            await self._worker_loop()
        except Exception as e:
            logger.error(f"Dead-letter worker error: {e}")
        finally:
            self.running = False
    
    async def stop(self):
        """Stop the dead-letter worker."""
        self.running = False
    
    async def _worker_loop(self):
        """Main worker loop."""
        while self.running:
            try:
                messages = self.queue.consume(
                    RedisQueues.DEAD_LETTERS,
                    "dead-letter",
                    self.consumer_name,
                    count=5,
                    timeout_ms=5000,
                )
                
                for message_id, payload in messages:
                    try:
                        await self._handle_dead_letter(message_id, payload)
                        self.queue.ack(RedisQueues.DEAD_LETTERS, "dead-letter", message_id)
                    except Exception as e:
                        logger.error(f"Dead-letter handling error: {e}")
            
            except Exception as e:
                logger.error(f"Dead-letter worker loop error: {e}")
    
    async def _handle_dead_letter(self, message_id: str, payload: Dict[str, Any]):
        """
        Handle a dead-letter message.
        
        Flow:
        1. Log the failure
        2. Check dead-letter count for channel in last hour
        3. If >= threshold: disable channel and alert ops
        4. Emit alert event to other channels if available
        
        Args:
            message_id: Redis stream message ID
            payload: Message payload (event + metadata)
        """
        try:
            event_id = payload.get("id")
            channel_id = payload.get("channel_id")
            attempt_count = payload.get("attempt_count", 0)
            
            logger.error(
                f"Dead-letter message: event_id={event_id}, channel_id={channel_id}, "
                f"attempt_count={attempt_count}"
            )
            
            # Store in Redis dead-letter count (with 1-hour TTL)
            dl_count_key = f"semabridge:dl_count:{channel_id}"
            count = self.redis.incr(dl_count_key)
            self.redis.expire(dl_count_key, 3600)  # 1 hour TTL
            
            logger.warning(f"Dead-letter count for {channel_id}: {count}/{self.alert_threshold}")
            
            # Check if threshold exceeded
            if count >= self.alert_threshold:
                await self._auto_disable_channel(channel_id, count)
                
                # Alert other channels
                if self.notification_service:
                    await self._alert_other_channels(channel_id, event_id, count)
        
        except Exception as e:
            logger.error(f"Error handling dead-letter: {e}", exc_info=True)
    
    async def _auto_disable_channel(self, channel_id: str, dead_letter_count: int):
        """
        Auto-disable a channel after repeated failures.
        
        Args:
            channel_id: Channel to disable
            dead_letter_count: Number of dead-letter events
        """
        try:
            # Find and disable the channel
            channel = self.db.query(NotificationChannel).filter(
                NotificationChannel.id == UUID(channel_id)
            ).first()
            
            if channel:
                # Disable the channel
                channel.status = ChannelStatus.DISABLED
                self.db.commit()
                
                logger.error(
                    f"Channel {channel_id} auto-disabled after {dead_letter_count} "
                    f"dead-letter events"
                )
                
                # Log this event
                logger.error(
                    json.dumps({
                        "event": "channel_auto_disabled",
                        "channel_id": str(channel_id),
                        "channel_name": channel.name,
                        "dead_letter_count": dead_letter_count,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                )
        
        except Exception as e:
            logger.error(f"Error disabling channel: {e}", exc_info=True)
    
    async def _alert_other_channels(
        self,
        failed_channel_id: str,
        event_id: str,
        dead_letter_count: int
    ):
        """
        Alert operators via other healthy channels.
        
        Args:
            failed_channel_id: Channel that failed
            event_id: Event that triggered dead-letter
            dead_letter_count: Number of failures
        """
        try:
            # Find the failed channel
            failed_channel = self.db.query(NotificationChannel).filter(
                NotificationChannel.id == UUID(failed_channel_id)
            ).first()
            
            if not failed_channel:
                return
            
            # Get other healthy channels
            other_channels = self.db.query(NotificationChannel).filter(
                NotificationChannel.id != UUID(failed_channel_id),
                NotificationChannel.enabled == True,
                NotificationChannel.status == ChannelStatus.ACTIVE,
            ).all()
            
            if not other_channels:
                logger.warning("No healthy channels to alert ops")
                return
            
            # Create alert event
            alert_event = NotificationEvent(
                correlation_id=f"deadletter_alert_{failed_channel_id}",
                type="channel_failure_alert",
                level=2,  # CRITICAL
                title=f"Channel {failed_channel.name} Auto-Disabled",
                message=(
                    f"Channel '{failed_channel.name}' has been auto-disabled after "
                    f"{dead_letter_count} repeated failures. "
                    f"Event ID: {event_id}"
                ),
                source="dead_letter_worker",
            )
            
            # Emit to notification service (which will route to healthy channels)
            if self.notification_service:
                success = await self.notification_service.emit(alert_event)
                logger.info(f"Alert emitted: {success}")
        
        except Exception as e:
            logger.error(f"Error alerting other channels: {e}", exc_info=True)
        
        try:
            await self._worker_loop()
        except Exception as e:
            logger.error(f"Dead-letter worker error: {e}")
        finally:
            self.running = False
    
    async def stop(self):
        """Stop the dead-letter worker."""
        self.running = False
    
    async def _worker_loop(self):
        """Main worker loop."""
        while self.running:
            try:
                messages = self.queue.consume(
                    RedisQueues.DEAD_LETTERS,
                    "dead-letter",
                    self.consumer_name,
                    count=5,
                    timeout_ms=5000,
                )
                
                for message_id, payload in messages:
                    try:
                        await self._handle_dead_letter(message_id, payload)
                        self.queue.ack(RedisQueues.DEAD_LETTERS, "dead-letter", message_id)
                    except Exception as e:
                        logger.error(f"Dead-letter handling error: {e}")
            
            except Exception as e:
                logger.error(f"Dead-letter worker loop error: {e}")
    
    async def _handle_dead_letter(self, message_id: str, payload: Dict[str, Any]):
        """
        Handle a dead-letter message.
        
        TODO in Phase 2:
        - Store in persistent deadletter database table
        - Check channel consecutive failure count
        - Disable channel if threshold exceeded
        - Alert operators
        
        Args:
            message_id: Redis stream message ID
            payload: Message payload
        """
        logger.warning(
            f"Dead-letter message {message_id}: "
            f"event_id={payload.get('id')}, "
            f"channel_id={payload.get('channel_id')}, "
            f"attempts={payload.get('attempt_count', 0)}"
        )
        
        # Phase 1: Just log
        # Phase 2: Implement persistence and alerting
