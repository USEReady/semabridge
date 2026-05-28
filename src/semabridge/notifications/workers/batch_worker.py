"""
Batch worker for flushing aggregated warning and error notifications.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

import redis
from sqlalchemy.orm import Session

from ..constants import RedisQueues, NotificationLevel
from ..models import NotificationEvent
from ..queue.redis_streams import RedisStreamsQueue
from ..services.batching_service import BatchingService

logger = logging.getLogger(__name__)


class BatchingFlushWorker:
    """
    Background worker that flushes staged notification batches.
    
    Flushes when:
    1. Time threshold is exceeded (batch_window_sec has elapsed since first add).
    2. Count threshold is exceeded (consecutive warnings/errors >= count_threshold).
    3. Shutdown is requested (flush all remaining).
    """

    def __init__(
        self,
        redis_url: str,
        db_session: Session,
        poll_interval_sec: int = 5,
        count_threshold: int = 50,
    ):
        """
        Initialize batch flush worker.
        
        Args:
            redis_url: Redis connection URL
            db_session: SQLAlchemy session
            poll_interval_sec: Interval in seconds to poll for active batches
            count_threshold: Maximum items in a batch before triggering immediate flush
        """
        self.redis = redis.from_url(redis_url)
        self.queue = RedisStreamsQueue(redis_url)
        self.db = db_session
        self.poll_interval_sec = poll_interval_sec
        self.count_threshold = count_threshold
        
        self.batching_service = BatchingService(self.redis)
        self.running = False

    async def start(self):
        """Start the batching flush worker loop."""
        logger.info("Batching flush worker starting")
        self.running = True
        
        try:
            while self.running:
                try:
                    await self.poll_and_flush_batches()
                except Exception as e:
                    logger.error(f"Error in batch flush worker loop: {e}", exc_info=True)
                
                await asyncio.sleep(self.poll_interval_sec)
                
        except asyncio.CancelledError:
            logger.info("Batching flush worker cancelled")
            self.running = False
        finally:
            # Perform graceful shutdown flush
            await self.flush_all_active_batches()

    async def stop(self):
        """Stop the batching flush worker."""
        logger.info("Stopping batching flush worker")
        self.running = False

    async def poll_and_flush_batches(self):
        """Poll the active batches set and flush eligible ones."""
        active_batches = self.redis.smembers("semabridge:active_batches")
        if not active_batches:
            return

        for batch_desc in active_batches:
            if isinstance(batch_desc, bytes):
                batch_desc = batch_desc.decode()

            try:
                sync_job_id, channel_id = batch_desc.split(":", 1)
            except ValueError:
                logger.error(f"Malformed batch descriptor in Redis active_batches: {batch_desc}")
                self.redis.srem("semabridge:active_batches", batch_desc)
                continue

            # Load metadata
            meta_key = f"semabridge:batch_meta:{sync_job_id}:{channel_id}"
            meta_raw = self.redis.get(meta_key)
            
            should_flush = False
            
            # Check count threshold
            batch_list_key = f"semabridge:batch:{sync_job_id}:{channel_id}"
            current_count = self.redis.llen(batch_list_key)
            if current_count >= self.count_threshold:
                logger.info(f"Count threshold reached for batch {batch_desc} ({current_count} items), flushing immediately")
                should_flush = True
                
            # Check time threshold
            elif meta_raw:
                try:
                    meta_data = json.loads(meta_raw)
                    flush_at = datetime.fromisoformat(meta_data["flush_at"])
                    if datetime.utcnow() >= flush_at:
                        logger.debug(f"Time threshold reached for batch {batch_desc}, flushing")
                        should_flush = True
                except Exception as parse_err:
                    logger.error(f"Failed to parse batch metadata {meta_key}: {parse_err}")
                    should_flush = True  # Flush as fallback
            else:
                # Meta expired or missing, but list exists: flush as safety fallback
                if current_count > 0:
                    logger.debug(f"Metadata missing for active batch {batch_desc}, forcing flush fallback")
                    should_flush = True
                else:
                    # Clean up empty active batch descriptor
                    self.redis.srem("semabridge:active_batches", batch_desc)

            if should_flush:
                await self.flush_batch(sync_job_id, channel_id)

    async def flush_batch(self, sync_job_id: str, channel_id: str):
        """Flush a single batch, build synthetic event, and requeue."""
        try:
            batch = await self.batching_service.get_batch(sync_job_id, channel_id)
            if not batch or not batch.get("items"):
                return

            items = batch["items"]
            warning_count = batch.get("warning_count", 0)
            error_count = batch.get("error_count", 0)
            total_count = len(items)

            # Aggregate levels: default to WARNING, elevate to ERROR if there are errors
            level = NotificationLevel.ERROR if error_count > 0 else NotificationLevel.WARNING

            # Build detailed message list
            lines = [
                f"SemaBridge Notification Batch Report",
                f"Job ID: {sync_job_id}",
                f"Aggregated {total_count} events ({error_count} errors, {warning_count} warnings)",
                "",
                "Summary of events in this batch:",
            ]
            
            # Format unique event summaries
            summaries = {}
            for item in items:
                title = item.get("title", "No Title")
                summaries[title] = summaries.get(title, 0) + 1

            for title, count in summaries.items():
                lines.append(f"- {title} (x{count})")

            message = "\n".join(lines)

            # Build synthetic aggregated event
            synthetic_event = NotificationEvent(
                id=uuid4(),
                correlation_id=f"batch_{sync_job_id}_{channel_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                sync_job_id=sync_job_id,
                project_id=None,  # Handled at routing override or payload
                type="batch_aggregate",
                level=level,
                title=f"Batch: {error_count} Errors, {warning_count} Warnings for Job {sync_job_id}",
                message=message,
                payload={
                    "bypass_batching": True,  # Prevent infinite batching loop!
                    "target_channel_id": channel_id,  # Direct delivery override
                    "batch_details": {
                        "sync_job_id": sync_job_id,
                        "total_count": total_count,
                        "error_count": error_count,
                        "warning_count": warning_count,
                    }
                },
                source="batching_flush_worker",
                created_at=datetime.utcnow()
            )

            # Enqueue back to the main queue for direct delivery
            payload = synthetic_event.to_dict()
            self.queue.enqueue(RedisQueues.NOTIFICATIONS, payload)
            logger.info(f"Successfully flushed batch {sync_job_id}:{channel_id} with {total_count} events as synthetic event {synthetic_event.id}")

        except Exception as e:
            logger.error(f"Failed to flush batch {sync_job_id}:{channel_id}: {e}", exc_info=True)

    async def flush_all_active_batches(self):
        """Flush all active batches immediately (used for graceful shutdown)."""
        active_batches = self.redis.smembers("semabridge:active_batches")
        if not active_batches:
            return

        logger.info(f"Graceful shutdown: flushing {len(active_batches)} active batches immediately")
        for batch_desc in active_batches:
            if isinstance(batch_desc, bytes):
                batch_desc = batch_desc.decode()

            try:
                sync_job_id, channel_id = batch_desc.split(":", 1)
                await self.flush_batch(sync_job_id, channel_id)
            except Exception as e:
                logger.error(f"Failed to flush batch {batch_desc} during shutdown: {e}")
