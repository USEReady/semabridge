"""
Retry worker for handling failed delivery attempts.

Implements exponential backoff and poison message handling.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from uuid import UUID

import redis
from sqlalchemy.orm import Session

from ...constants import RedisQueues, NotificationStatus
from ..queue.redis_streams import RedisStreamsQueue
from ..services.delivery_log_service import DeliveryLogService
from ..utils.retry import RetryConfig, RetryMetadata, should_retry, is_ready_to_retry

logger = logging.getLogger(__name__)


class RetryWorker:
    """
    Process failed deliveries from retry queue with exponential backoff.
    """
    
    def __init__(
        self,
        redis_url: str,
        db_session: Session,
        retry_config: RetryConfig = None,
        consumer_name: str = "retry-worker"
    ):
        """
        Initialize retry worker.
        
        Args:
            redis_url: Redis connection URL
            db_session: SQLAlchemy session
            retry_config: Retry configuration
            consumer_name: Consumer name for group tracking
        """
        self.redis = redis.from_url(redis_url)
        self.queue = RedisStreamsQueue(redis_url)
        self.db = db_session
        self.retry_config = retry_config or RetryConfig()
        self.consumer_name = consumer_name
        self.delivery_log = DeliveryLogService(db_session)
        self.running = False
    
    async def start(self):
        """Start the retry worker."""
        self.running = True
        logger.info("Retry worker started")
        
        # Create consumer group
        self.queue.create_consumer_group(
            RedisQueues.RETRY,
            "retry",
            start_id="0"
        )
        
        try:
            await self._worker_loop()
        except Exception as e:
            logger.error(f"Retry worker error: {e}")
        finally:
            self.running = False
    
    async def stop(self):
        """Stop the retry worker."""
        self.running = False
    
    async def _worker_loop(self):
        """Main worker loop."""
        while self.running:
            try:
                messages = self.queue.consume(
                    RedisQueues.RETRY,
                    "retry",
                    self.consumer_name,
                    count=1,
                    timeout_ms=1000,
                )
                
                for message_id, payload in messages:
                    try:
                        await self._process_retry(message_id, payload)
                        
                        # Acknowledge
                        self.queue.ack(RedisQueues.RETRY, "retry", message_id)
                    
                    except Exception as e:
                        logger.error(f"Retry processing error: {e}")
                        self.queue.nack(
                            RedisQueues.RETRY,
                            "retry",
                            self.consumer_name,
                            message_id
                        )
            
            except Exception as e:
                logger.error(f"Retry worker loop error: {e}")
                await asyncio.sleep(1)
    
    async def _process_retry(self, message_id: str, payload: Dict[str, Any]):
        """
        Process a retry message.
        
        Args:
            message_id: Redis stream message ID
            payload: Message payload with retry metadata
        """
        try:
            # Extract retry metadata
            attempt_count = payload.get("attempt_count", 0)
            next_attempt_iso = payload.get("next_attempt")
            
            # Parse datetime
            if next_attempt_iso:
                next_attempt = datetime.fromisoformat(next_attempt_iso)
            else:
                next_attempt = datetime.utcnow()
            
            # Check if it's time to retry
            if not is_ready_to_retry(next_attempt):
                delay_sec = int((next_attempt - datetime.utcnow()).total_seconds())
                logger.debug(f"Message {message_id} not ready for {delay_sec}s, returning to queue")
                self.queue.nack(
                    RedisQueues.RETRY,
                    "retry",
                    self.consumer_name,
                    message_id
                )
                return
            
            # Check if max retries exceeded
            if not should_retry(attempt_count, self.retry_config):
                logger.warning(f"Max retries exceeded for message {message_id}, moving to dead-letter")
                await self.queue.move_to_dead_letter(message_id, payload)
                await self.delivery_log.log_delivery(
                    UUID(payload.get("id", "00000000-0000-0000-0000-000000000000")),
                    UUID(payload.get("channel_id", "00000000-0000-0000-0000-000000000000")),
                    NotificationStatus.DEAD,
                    attempt=attempt_count,
                    error_message=f"Max retries ({self.retry_config.max_retries}) exceeded",
                )
                return
            
            # Re-queue to main dispatcher for retry
            retry_payload = {
                **payload,
                "attempt_count": attempt_count + 1,
                "last_attempt": datetime.utcnow().isoformat(),
            }
            self.queue.enqueue(RedisQueues.NOTIFICATIONS, retry_payload)
            
            logger.info(f"Requeued message {message_id} for retry (attempt {attempt_count + 1})")
        
        except Exception as e:
            logger.error(f"Failed to process retry: {e}")
            raise
