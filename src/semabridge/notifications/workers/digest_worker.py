"""
Digest worker for batching and flushing quiet hours suppressed notifications.

Reads from semabridge:digest-staging and flushes batched events when quiet hours end.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, List
from collections import defaultdict

import redis
from sqlalchemy.orm import Session

from ...constants import (
    NotificationChannelType,
    NotificationStatus,
    RedisQueues,
    level_to_string,
)
from ..models import NotificationEvent, NotificationChannel
from ..queue.redis_streams import RedisStreamsQueue
from ..services.quiet_hours_service import QuietHoursService
from ..services.delivery_log_service import DeliveryLogService
from ..adapters.slack_adapter import SlackAdapter
from ..adapters.email_adapter import EmailAdapter
from ..adapters.webhook_adapter import WebhookAdapter
from ..formatters.slack_formatter import SlackFormatter
from ..formatters.email_formatter import EmailFormatter
from ..formatters.webhook_formatter import WebhookFormatter

logger = logging.getLogger(__name__)


class DigestWorker:
    """
    Processes digest messages for quiet hours suppressed notifications.
    
    On each poll:
    1. Check each channel with digest_enabled=True
    2. If quiet hours have ended: pull staged events
    3. Group by level, count, format digest message
    4. Send via the channel's adapter
    5. ACK messages
    """
    
    # Adapter registry
    ADAPTERS = {
        NotificationChannelType.SLACK: SlackAdapter,
        NotificationChannelType.EMAIL: EmailAdapter,
        NotificationChannelType.WEBHOOK: WebhookAdapter,
    }
    
    # Formatter registry
    FORMATTERS = {
        NotificationChannelType.SLACK: SlackFormatter,
        NotificationChannelType.EMAIL: EmailFormatter,
        NotificationChannelType.WEBHOOK: WebhookFormatter,
    }
    
    def __init__(
        self,
        redis_url: str,
        db_session: Session,
        poll_interval_sec: int = 60
    ):
        """
        Initialize digest worker.
        
        Args:
            redis_url: Redis connection URL
            db_session: SQLAlchemy session
            poll_interval_sec: How often to check for digest flushes
        """
        self.redis = redis.from_url(redis_url)
        self.queue = RedisStreamsQueue(redis_url)
        self.db = db_session
        self.poll_interval_sec = poll_interval_sec
        
        # Initialize services
        self.quiet_hours = QuietHoursService()
        self.delivery_log = DeliveryLogService(db_session)
        
        self.running = False
    
    async def start(self):
        """Start the digest worker loop."""
        logger.info("Digest worker starting")
        self.running = True
        
        try:
            while self.running:
                try:
                    await self.process_digests()
                except Exception as e:
                    logger.error(f"Error processing digests: {e}", exc_info=True)
                
                # Sleep before next poll
                await asyncio.sleep(self.poll_interval_sec)
        
        except asyncio.CancelledError:
            logger.info("Digest worker cancelled")
            self.running = False
    
    async def stop(self):
        """Stop the digest worker."""
        logger.info("Stopping digest worker")
        self.running = False
    
    async def process_digests(self):
        """
        Check all channels and flush digests when quiet hours end.
        """
        # Get all channels with digest enabled
        channels = self.db.query(NotificationChannel).filter(
            NotificationChannel.digest_enabled == True,
            NotificationChannel.enabled == True,
        ).all()
        
        for channel in channels:
            try:
                # Check if quiet hours have ended
                seconds_until_end = self.quiet_hours.seconds_until_window_end(channel)
                
                # If in quiet hours or 0 seconds until end, don't flush yet
                if seconds_until_end > 0:
                    continue
                
                # Quiet hours have ended (or not in quiet hours), flush digest
                await self._flush_digest(channel)
            
            except Exception as e:
                logger.error(f"Error flushing digest for channel {channel.id}: {e}", exc_info=True)
    
    async def _flush_digest(self, channel: NotificationChannel):
        """
        Flush staged events for a channel.
        
        Args:
            channel: NotificationChannel to flush digest for
        """
        digest_staging_key = f"semabridge:digest-staging:{channel.id}"
        
        # Get all staged events
        staged_events = []
        try:
            # Get events from list (LPUSH used by dispatcher)
            count = self.redis.llen(digest_staging_key)
            if count == 0:
                return  # Nothing to flush
            
            # Get all events
            event_strs = self.redis.lrange(digest_staging_key, 0, -1)
            
            for event_str in event_strs:
                try:
                    event_dict = json.loads(event_str)
                    event = NotificationEvent.from_dict(event_dict)
                    staged_events.append(event)
                except Exception as e:
                    logger.error(f"Error parsing staged event: {e}")
            
            if not staged_events:
                return
            
            # Group by level
            events_by_level = defaultdict(list)
            for event in staged_events:
                events_by_level[level_to_string(event.level)].append(event)
            
            # Build digest message
            digest_event = await self._build_digest_event(
                channel,
                events_by_level,
                staged_events
            )
            
            # Format and send
            adapter_class = self.ADAPTERS.get(channel.channel_type.value)
            formatter_class = self.FORMATTERS.get(channel.channel_type.value)
            
            if not adapter_class or not formatter_class:
                logger.warning(f"No adapter/formatter for {channel.channel_type}")
                return
            
            adapter = adapter_class(channel.config_json)
            formatter = formatter_class()
            
            # Decrypt config if needed (in real implementation)
            channel_config = json.loads(channel.config_json)
            
            # Format digest
            payload = formatter.format(digest_event, channel_config)
            
            # Send
            result = await adapter.send(payload, channel_config)
            
            # Log delivery
            await self.delivery_log.log_delivery(
                event_id=digest_event.id,
                channel_id=channel.id,
                status=NotificationStatus.DELIVERED if result.get("success") else NotificationStatus.FAILED,
                attempt=1,
                response_code=result.get("response_code"),
                response_body=result.get("response_body"),
                duration_ms=result.get("duration_ms", 0),
                error_message=result.get("error"),
            )
            
            if result.get("success"):
                # Clear the digest staging queue
                self.redis.delete(digest_staging_key)
                logger.info(f"Digest flushed for channel {channel.id}: {len(staged_events)} events")
            else:
                logger.warning(f"Failed to send digest for channel {channel.id}")
        
        except Exception as e:
            logger.error(f"Error flushing digest: {e}", exc_info=True)
    
    async def _build_digest_event(
        self,
        channel: NotificationChannel,
        events_by_level: Dict[str, List[NotificationEvent]],
        all_events: List[NotificationEvent]
    ) -> NotificationEvent:
        """
        Build a synthetic NotificationEvent for the digest.
        
        Args:
            channel: Channel being flushed
            events_by_level: Events grouped by level
            all_events: All staged events
        
        Returns:
            Synthetic digest event
        """
        # Count by level
        counts = {level: len(events) for level, events in events_by_level.items()}
        
        # Find oldest and newest timestamps
        timestamps = [e.created_at for e in all_events if e.created_at]
        oldest = min(timestamps) if timestamps else datetime.utcnow()
        newest = max(timestamps) if timestamps else datetime.utcnow()
        
        # Build digest lines
        quiet_hours_str = self.quiet_hours.get_quiet_hours_window_str(channel)
        
        lines = [
            f"Digest — quiet hours {quiet_hours_str}",
            "",
        ]
        
        # Add counts
        for level, count in sorted(counts.items()):
            lines.append(f"{count} {level.lower()}")
        
        lines.extend([
            "",
            f"Oldest: {oldest.isoformat()}",
            f"Newest: {newest.isoformat()}",
        ])
        
        message = "\n".join(lines)
        
        # Build synthetic event
        digest_event = NotificationEvent(
            correlation_id=f"digest_{channel.id}",
            project_id=channel.project_scope,
            type="digest",
            level=1,  # SYNC_RESULT
            title=f"Digest — {len(all_events)} notifications",
            message=message,
            payload={
                "digest": True,
                "counts": counts,
                "channel_id": str(channel.id),
                "oldest": oldest.isoformat(),
                "newest": newest.isoformat(),
            },
            source="digest_worker",
        )
        
        return digest_event
    
    async def stage_event(
        self,
        channel_id: str,
        event: NotificationEvent
    ) -> None:
        """
        Stage an event for later digest delivery.
        
        Args:
            channel_id: Channel to stage for
            event: Event to stage
        """
        digest_staging_key = f"semabridge:digest-staging:{channel_id}"
        
        # Push to list with TTL
        event_json = json.dumps(event.to_dict())
        self.redis.lpush(digest_staging_key, event_json)
        
        # Set TTL to 24 hours (events expire if not flushed)
        self.redis.expire(digest_staging_key, 86400)
        
        logger.debug(f"Staged event for digest: {channel_id}")

