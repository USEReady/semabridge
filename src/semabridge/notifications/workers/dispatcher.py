"""
Dispatcher worker for processing notification delivery.

Main orchestrator that:
1. Consumes from Redis Streams
2. Routes to channels
3. Applies deduplication, batching, quiet hours, circuit breaker
4. Formats and sends via adapters
5. Handles failures and retries
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from uuid import UUID

import redis
from sqlalchemy.orm import Session

from ...constants import (
    NotificationChannelType,
    NotificationStatus,
    ChannelStatus,
    RedisQueues,
    matches_level,
)
from ..models import NotificationEvent, NotificationChannel
from ..queue.redis_streams import RedisStreamsQueue
from ..adapters.slack_adapter import SlackAdapter
from ..adapters.email_adapter import EmailAdapter
from ..adapters.webhook_adapter import WebhookAdapter
from ..adapters.teams_adapter import TeamsAdapter
from ..adapters.pagerduty_adapter import PagerDutyAdapter
from ..formatters.slack_formatter import SlackFormatter
from ..formatters.email_formatter import EmailFormatter
from ..formatters.webhook_formatter import WebhookFormatter
from ..formatters.teams_formatter import TeamsFormatter
from ..formatters.pagerduty_formatter import PagerDutyFormatter
from .retry_worker import RetryWorker
from ..services.routing_service import RoutingService
from ..services.dedupe_service import DedupeService
from ..services.batching_service import BatchingService
from ..services.delivery_log_service import DeliveryLogService
from ..services.circuit_breaker_service import CircuitBreakerService
from ..services.quiet_hours_service import QuietHoursService
from ..services.delivery_log_service import DeliveryLogService
from ..utils.masking import sanitize_for_logging

logger = logging.getLogger(__name__)


class DispatcherWorker:
    """
    Main notification dispatcher worker.
    
    Processes events from queue and orchestrates delivery pipeline.
    """
    
    # Adapter registry
    ADAPTERS = {
        NotificationChannelType.SLACK: SlackAdapter,
        NotificationChannelType.EMAIL: EmailAdapter,
        NotificationChannelType.WEBHOOK: WebhookAdapter,
        NotificationChannelType.TEAMS: TeamsAdapter,
        NotificationChannelType.PAGERDUTY: PagerDutyAdapter,
    }
    
    # Formatter registry
    FORMATTERS = {
        NotificationChannelType.SLACK: SlackFormatter,
        NotificationChannelType.EMAIL: EmailFormatter,
        NotificationChannelType.WEBHOOK: WebhookFormatter,
        NotificationChannelType.TEAMS: TeamsFormatter,
        NotificationChannelType.PAGERDUTY: PagerDutyFormatter,
    }
    
    def __init__(
        self,
        redis_url: str,
        db_session: Session,
        max_workers: int = 5,
        consumer_name: str = "dispatcher"
    ):
        """
        Initialize dispatcher worker.
        
        Args:
            redis_url: Redis connection URL
            db_session: SQLAlchemy session
            max_workers: Number of concurrent workers
            consumer_name: Consumer name for group tracking
        """
        self.redis = redis.from_url(redis_url)
        self.queue = RedisStreamsQueue(redis_url)
        self.db = db_session
        self.max_workers = max_workers
        self.consumer_name = consumer_name
        
        # Initialize services
        self.routing = RoutingService(db_session)
        self.dedupe = DedupeService(self.redis)
        self.batching = BatchingService(self.redis)
        self.delivery_log = DeliveryLogService(db_session)
        self.circuit_breaker = CircuitBreakerService(self.redis)
        self.quiet_hours = QuietHoursService()
        self.retry_worker = RetryWorker(redis_url, db_session)
        
        self.running = False
    
    async def start(self):
        """Start the dispatcher worker."""
        self.running = True
        logger.info("Dispatcher worker started")
        
        # Create consumer group
        self.queue.create_consumer_group(
            RedisQueues.NOTIFICATIONS,
            "dispatcher",
            start_id="0"
        )
        
        # Start worker pool
        tasks = [
            self._worker_loop(i) for i in range(self.max_workers)
        ]
        
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Dispatcher error: {e}")
        finally:
            self.running = False
    
    async def stop(self):
        """Stop the dispatcher worker."""
        self.running = False
        logger.info("Dispatcher worker stopped")
    
    async def _worker_loop(self, worker_id: int):
        """
        Main worker loop for a single worker.
        
        Args:
            worker_id: Worker identifier
        """
        logger.info(f"Worker {worker_id} started")
        
        while self.running:
            try:
                # Consume messages
                messages = self.queue.consume(
                    RedisQueues.NOTIFICATIONS,
                    "dispatcher",
                    f"{self.consumer_name}-{worker_id}",
                    count=1,
                    timeout_ms=1000,
                )
                
                for message_id, payload in messages:
                    try:
                        await self._process_message(message_id, payload)
                        
                        # Acknowledge after successful processing
                        self.queue.ack(
                            RedisQueues.NOTIFICATIONS,
                            "dispatcher",
                            message_id
                        )
                    except Exception as e:
                        logger.error(f"Failed to process message {message_id}: {e}")
                        # Return to queue for retry
                        self.queue.nack(
                            RedisQueues.NOTIFICATIONS,
                            "dispatcher",
                            f"{self.consumer_name}-{worker_id}",
                            message_id
                        )
            
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                await asyncio.sleep(1)
    
    async def _process_message(self, message_id: str, payload: Dict[str, Any]):
        """
        Process a single message from queue.
        
        Orchestrates the full delivery pipeline:
        1. Deserialize event
        2. Route to channels
        3. Deduplicate
        4. Batch check
        5. Format and send
        
        Args:
            message_id: Redis stream message ID
            payload: Message payload
        """
        try:
            # Deserialize event
            event = NotificationEvent.from_dict(payload)
            
            logger.debug(f"Processing event {event.id} ({event.title})")
            
            # Get matching channels
            channels = await self.routing.get_matching_channels(event)
            
            if not channels:
                logger.debug(f"No matching channels for event {event.id}")
                return
            
            # Process each channel
            for channel_info in channels:
                await self._deliver_to_channel(event, channel_info)
        
        except Exception as e:
            logger.error(f"Message processing error: {e}")
            raise
    
    async def _deliver_to_channel(
        self,
        event: NotificationEvent,
        channel_info: Dict[str, Any]
    ):
        """
        Deliver event to a specific channel.
        
        Orchestrates:
        1. Circuit breaker checks
        2. Quiet hours handling with digest staging
        3. Deduplication
        4. Batching
        5. Format and send
        6. Failure recovery
        
        Args:
            event: Notification event
            channel_info: Channel configuration
        """
        channel_id = channel_info["id"]
        channel_type = channel_info["channel_type"]
        
        try:
            # Check circuit breaker first
            if await self.circuit_breaker.is_open(channel_id):
                logger.warning(f"Circuit breaker OPEN for channel {channel_id}, skipping")
                await self.delivery_log.log_delivery(
                    UUID(event.id),
                    UUID(channel_id),
                    NotificationStatus.SKIPPED_DEDUPE,  # Use as placeholder for skipped
                    error_message="Circuit breaker open",
                )
                return
            
            # Get full channel object for quiet hours and digest checks
            channel_obj = self.db.query(NotificationChannel).filter(
                NotificationChannel.id == UUID(channel_id)
            ).first()
            
            # Check quiet hours
            if channel_info.get("quiet_hours_enabled") and channel_obj:
                if self.quiet_hours.is_quiet(channel_obj):
                    # Check if should bypass
                    if not self.quiet_hours.should_bypass(event, channel_obj):
                        # Stage for digest
                        logger.debug(f"Staging event {event.id} for digest: {channel_id}")
                        
                        # Stage using digest worker's method (or inline)
                        from .digest_worker import DigestWorker
                        digest_worker = DigestWorker("redis://localhost:6379", self.db)
                        await digest_worker.stage_event(channel_id, event)
                        
                        await self.delivery_log.log_delivery(
                            UUID(event.id),
                            UUID(channel_id),
                            NotificationStatus.SKIPPED_QUIET,
                        )
                        return
            
            # Generate fingerprint for deduplication
            fingerprint = event.generate_fingerprint(channel_id)
            
            # Check deduplication
            if await self.dedupe.is_duplicate(fingerprint):
                logger.debug(f"Duplicate event {event.id} for channel {channel_id}")
                await self.delivery_log.log_delivery(
                    UUID(event.id),
                    UUID(channel_id),
                    NotificationStatus.SKIPPED_DEDUPE,
                )
                return
            
            # Check batching
            if await self.batching.should_batch(event):
                logger.debug(f"Batching event {event.id} for channel {channel_id}")
                await self.batching.add_to_batch(event, channel_id)
                return
            
            # Mark as processed to prevent duplicates
            await self.dedupe.mark_processed(fingerprint)
            
            # Get adapter and formatter
            adapter_class = self.ADAPTERS.get(channel_type)
            formatter_class = self.FORMATTERS.get(channel_type)
            
            if not adapter_class or not formatter_class:
                logger.error(f"No adapter/formatter for channel type {channel_type}")
                await self.delivery_log.log_delivery(
                    UUID(event.id),
                    UUID(channel_id),
                    NotificationStatus.FAILED,
                    error_message=f"Unsupported channel type: {channel_type}",
                )
                return
            
            # Decrypt config_json (placeholder - would use encryption library in production)
            config_json = channel_info.get("config_json", {})
            if isinstance(config_json, str):
                try:
                    config_json = json.loads(config_json)
                except json.JSONDecodeError:
                    config_json = {}
            
            # Format event
            formatter = formatter_class()
            formatted = formatter.format(event, config_json)
            
            # Send via adapter
            adapter = adapter_class(config_json)
            result = await adapter.send(formatted, config_json)
            
            # Log result
            status = NotificationStatus.DELIVERED if result.get("success") else NotificationStatus.FAILED
            await self.delivery_log.log_delivery(
                UUID(event.id),
                UUID(channel_id),
                status,
                attempt=1,
                response_code=result.get("response_code"),
                response_body=result.get("response_body"),
                duration_ms=result.get("duration_ms"),
                error_message=result.get("error"),
            )
            
            logger.debug(f"Event {event.id} -> {channel_id}: {status}")
            
            # Handle circuit breaker
            if result.get("success"):
                await self.circuit_breaker.record_success(channel_id)
            else:
                await self.circuit_breaker.record_failure(channel_id)
                
                # On failure, queue for retry
                retry_payload = {
                    **event.to_dict(),
                    "channel_id": channel_id,
                    "attempt_count": 1,
                }
                self.queue.enqueue(RedisQueues.RETRY, retry_payload)
        
        except Exception as e:
                self.queue.enqueue(RedisQueues.RETRY, retry_payload)
        
        except Exception as e:
            logger.error(f"Delivery error for channel {channel_id}: {e}")
            await self.delivery_log.log_delivery(
                UUID(event.id),
                UUID(channel_id),
                NotificationStatus.FAILED,
                error_message=str(e),
            )
