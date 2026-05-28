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

def safe_uuid(val: Any) -> Optional[UUID]:
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    return UUID(str(val))

import redis
from sqlalchemy.orm import Session

from ..constants import (
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
        """Start the dispatcher worker with full hardening."""
        self.running = True

        # STEP 6 — Create consumer group with logging
        created = self.queue.create_consumer_group(
            RedisQueues.NOTIFICATIONS,
            "dispatcher",
            start_id="0"
        )
        logger.info({
            "event": "consumer_group_ready",
            "created_new": created,
            "stream": RedisQueues.NOTIFICATIONS,
            "group": "dispatcher",
        })

        # STEP 1 — Startup heartbeat
        logger.info({
            "event": "dispatcher_started",
            "stream": RedisQueues.NOTIFICATIONS,
            "consumer_group": "dispatcher",
            "worker_count": self.max_workers,
            "consumer_name": self.consumer_name,
        })

        # STEP 7 — Never-exit safeguard: restart the entire pool if all tasks die
        while self.running:
            tasks = []
            for i in range(self.max_workers):
                tasks.append(asyncio.create_task(self._resilient_worker(i)))
            tasks.append(asyncio.create_task(self._reclaim_pending_loop()))
            tasks.append(asyncio.create_task(self._heartbeat_loop()))

            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                # Log any exceptions that bubbled up
                for idx, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Task {idx} exited with error: {result}")
            except Exception as e:
                logger.exception(f"Dispatcher gather error: {e}")

            if self.running:
                logger.warning("All dispatcher tasks exited unexpectedly — restarting worker pool in 3s")
                await asyncio.sleep(3)
            else:
                break

        logger.info("Dispatcher event loop exited.")
    
    async def stop(self):
        """Stop the dispatcher worker."""
        self.running = False
        logger.info("Dispatcher worker stopped")
    
    async def _resilient_worker(self, worker_id: int):
        """
        STEP 3 — Resilient wrapper that auto-restarts a worker on crash.
        
        Args:
            worker_id: Worker identifier
        """
        while self.running:
            try:
                await self._worker_loop(worker_id)
            except Exception:
                logger.exception(f"Worker {worker_id} crashed — restarting in 2s")
                await asyncio.sleep(2)

    async def _worker_loop(self, worker_id: int):
        """
        Main worker loop for a single worker.
        
        Args:
            worker_id: Worker identifier
        """
        logger.info(f"Worker {worker_id} started")
        
        while self.running:
            try:
                # STEP 4 — XREADGROUP logging
                logger.debug({"event": "waiting_for_messages", "worker_id": worker_id})

                # Consume messages — run in thread pool because queue.consume()
                # uses synchronous redis-py with blocking xreadgroup().
                # Without this, the blocking call freezes the entire asyncio
                # event loop, preventing other workers/heartbeat/reclaim.
                messages = await asyncio.to_thread(
                    self.queue.consume,
                    RedisQueues.NOTIFICATIONS,
                    "dispatcher",
                    f"{self.consumer_name}-{worker_id}",
                    1,      # count
                    1000,   # timeout_ms
                )

                if messages:
                    logger.info({
                        "event": "messages_received",
                        "worker_id": worker_id,
                        "count": len(messages),
                    })
                
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
                        logger.error(f"Failed to process message {message_id}: {e}", exc_info=True)
                        # Return to queue for retry
                        self.queue.nack(
                            RedisQueues.NOTIFICATIONS,
                            "dispatcher",
                            f"{self.consumer_name}-{worker_id}",
                            message_id
                        )
            
            except Exception as e:
                logger.exception(f"Worker {worker_id} error: {e}")
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
            
            logger.info({
                "dispatcher_received_event": event.title,
                "level": event.level,
                "source": event.source,
            })
            
            logger.debug(f"Processing event {event.id} ({event.title})")
            
            # Get matching channels (or override for direct target channel)
            if event.payload and event.payload.get("target_channel_id"):
                ch_id = event.payload.get("target_channel_id")
                channel = await self.routing.get_channel_by_id(ch_id)
                channels = [channel] if channel else []
                logger.debug(f"Event {event.id} targets direct channel override: {ch_id}")
            else:
                channels = await self.routing.get_matching_channels(event)
            
            if not channels:
                logger.debug(f"No matching channels for event {event.id}")
                return
            
            # --- Structured fanout log ---
            logger.info({
                "event": "notification_fanout",
                "event_id": str(event.id),
                "event_title": event.title,
                "channel_count": len(channels),
                "channel_types": [ch.get("channel_type") for ch in channels],
                "channel_names": [ch.get("name") for ch in channels],
            })

            # Deliver to ALL matched channels (fanout — no break/short-circuit)
            for channel_info in channels:
                await self._deliver_to_channel(event, channel_info)
        
        except Exception as e:
            logger.exception(f"Message processing error: {e}")
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
        channel_name = channel_info.get("name", channel_id)

        # --- Structured per-channel delivery attempt log ---
        logger.info({
            "event": "channel_delivery_attempt",
            "event_id": str(event.id),
            "channel_type": channel_type,
            "channel_name": channel_name,
            "channel_id": channel_id,
        })

        retry_payload = {
            **event.to_dict(),
            "channel_id": channel_id,
            "attempt_count": 1,
        }
        
        try:
            # Check circuit breaker first
            if await self.circuit_breaker.is_open(channel_id):
                logger.warning(f"Circuit breaker OPEN for channel {channel_id}, skipping")
                await self.delivery_log.log_delivery(
                    safe_uuid(event.id),
                    safe_uuid(channel_id),
                    NotificationStatus.SKIPPED_DEDUPE,  # Use as placeholder for skipped
                    error_message="Circuit breaker open",
                )
                return
            
            # Get full channel object for quiet hours and digest checks
            channel_obj = self.db.query(NotificationChannel).filter(
                NotificationChannel.id == safe_uuid(channel_id)
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
                            safe_uuid(event.id),
                            safe_uuid(channel_id),
                            NotificationStatus.SKIPPED_QUIET,
                        )
                        return
            
            # Generate fingerprint for deduplication
            fingerprint = event.generate_fingerprint(channel_id)
            
            # Check deduplication
            if await self.dedupe.is_duplicate(fingerprint):
                logger.debug(f"Duplicate event {event.id} for channel {channel_id}")
                await self.delivery_log.log_delivery(
                    safe_uuid(event.id),
                    safe_uuid(channel_id),
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
                    safe_uuid(event.id),
                    safe_uuid(channel_id),
                    NotificationStatus.FAILED,
                    error_message=f"Unsupported channel type: {channel_type}",
                )
                return
            
            # Decrypt config_json at runtime using NotificationCrypto
            config_json_raw = channel_info.get("config_json", "")
            if isinstance(config_json_raw, str):
                try:
                    from ..utils.crypto import NotificationCrypto
                    crypto = NotificationCrypto()
                    decrypted_str = crypto.decrypt(config_json_raw)
                    config_json = json.loads(decrypted_str)
                except Exception as decrypt_err:
                    logger.error(f"Failed to decrypt/parse config_json for channel {channel_id}: {decrypt_err}")
                    config_json = {}
            else:
                config_json = config_json_raw or {}
            
            # Resolve template if configured
            from ..services.template_service import TemplateService
            template_svc = TemplateService()
            template = await template_svc.get_template(self.db, channel_id, event.level)
            
            event_for_delivery = event
            if template:
                logger.info({
                    "template_selected": template.name,
                    "event_level": event.level,
                })
                try:
                    rendered_title, rendered_body = template_svc.render(template, event)
                    from dataclasses import replace
                    event_for_delivery = replace(
                        event,
                        title=rendered_title,
                        message=rendered_body
                    )
                    logger.debug(f"Rendered template for event {event.id} on channel {channel_id}")
                except Exception as template_err:
                    logger.error(f"Failed to render template for channel {channel_id}: {template_err}")
            
            # Format event
            formatter = formatter_class()
            formatted = formatter.format(event_for_delivery, config_json)
            
            # Send via adapter
            adapter = adapter_class(config_json)
            try:
                result = await adapter.send(formatted, config_json)
            finally:
                await adapter.close()
            
            # Log result
            status = NotificationStatus.DELIVERED if result.get("success") else NotificationStatus.FAILED
            await self.delivery_log.log_delivery(
                safe_uuid(event.id),
                safe_uuid(channel_id),
                status,
                attempt=1,
                response_code=result.get("response_code"),
                response_body=result.get("response_body"),
                duration_ms=result.get("duration_ms"),
                error_message=result.get("error"),
            )

            logger.debug(f"Event {event.id} -> {channel_id}: {status}")

            # --- Structured per-channel delivery outcome log ---
            if result.get("success"):
                logger.info({
                    "event": "channel_delivery_success",
                    "event_id": str(event.id),
                    "channel_type": channel_type,
                    "channel_name": channel_name,
                    "duration_ms": result.get("duration_ms"),
                })
                await self.circuit_breaker.record_success(channel_id)
            else:
                logger.warning({
                    "event": "channel_delivery_failure",
                    "event_id": str(event.id),
                    "channel_type": channel_type,
                    "channel_name": channel_name,
                    "error": result.get("error"),
                    "response_code": result.get("response_code"),
                })
                await self.circuit_breaker.record_failure(channel_id)

                # On failure, queue for retry
                self.queue.enqueue(RedisQueues.RETRY, retry_payload)
        
        except Exception as e:
            logger.exception(f"Delivery error for channel {channel_id}: {e}")
            logger.warning({
                "event": "channel_delivery_failure",
                "event_id": str(event.id),
                "channel_type": channel_type,
                "channel_name": channel_name,
                "error": str(e),
            })
            await self.delivery_log.log_delivery(
                safe_uuid(event.id),
                safe_uuid(channel_id),
                NotificationStatus.FAILED,
                error_message=str(e),
            )
            await self.circuit_breaker.record_failure(channel_id)
            try:
                self.queue.enqueue(RedisQueues.RETRY, retry_payload)
            except Exception as q_err:
                logger.error(f"Failed to queue retry for channel {channel_id}: {q_err}")

    async def _heartbeat_loop(self):
        """
        STEP 2 — Continuous heartbeat every 30 seconds.
        Logs worker liveness and pending message counts.
        """
        while self.running:
            try:
                await asyncio.sleep(30)
                if not self.running:
                    break

                # STEP 9 — Pending entries debug
                pending_count = self.queue.get_pending_count(
                    RedisQueues.NOTIFICATIONS, "dispatcher"
                )
                pending_summary = self.queue.get_pending_summary(
                    RedisQueues.NOTIFICATIONS, "dispatcher"
                )

                logger.info({
                    "event": "dispatcher_heartbeat",
                    "running": self.running,
                    "active_workers": self.max_workers,
                    "pending_messages": pending_count,
                    "pending_min": pending_summary.get("min"),
                    "pending_max": pending_summary.get("max"),
                })
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}", exc_info=True)

    async def _reclaim_pending_loop(self):
        """
        STEP 5 — Periodic task to scan for stuck pending messages,
        claim them, or move poison messages to dead letter.
        
        Now uses xpending_range() which returns per-message dicts with
        keys: message_id, consumer, time_since_delivered, times_delivered.
        """
        logger.info("Stream pending message reclaimer loop started")
        while self.running:
            try:
                # Wait 30 seconds between scans
                await asyncio.sleep(30)
                if not self.running:
                    break
                
                # Fetch pending messages (now using xpending_range via fixed get_pending_messages)
                pending_info_list = self.queue.get_pending_messages(
                    RedisQueues.NOTIFICATIONS,
                    "dispatcher",
                    count=100
                )
                
                reclaimed_count = 0
                poison_count = 0
                
                if not pending_info_list:
                    logger.debug({"event": "reclaim_scan", "pending": 0, "reclaimed": 0, "poison": 0})
                    continue
                
                for info in pending_info_list:
                    # xpending_range returns dicts with these keys:
                    #   message_id, consumer, time_since_delivered, times_delivered
                    msg_id = info.get("message_id")
                    if isinstance(msg_id, bytes):
                        msg_id = msg_id.decode()
                    if not msg_id:
                        continue
                    
                    time_since_delivered = info.get("time_since_delivered", 0)
                    times_delivered = info.get("times_delivered", 0)
                    
                    try:
                        time_since_delivered = int(time_since_delivered) if time_since_delivered is not None else 0
                        times_delivered = int(times_delivered) if times_delivered is not None else 0
                    except (ValueError, TypeError):
                        time_since_delivered = 0
                        times_delivered = 0
                        
                    # 1. Poison message check (delivered > 5 times)
                    if times_delivered > 5:
                        logger.warning(f"Poison message detected: message {msg_id} delivered {times_delivered} times. Moving to DLQ.")
                        poison_count += 1
                        
                        entry = self.redis.xrange(RedisQueues.NOTIFICATIONS, min=msg_id, max=msg_id, count=1)
                        if entry:
                            _, data = entry[0]
                            # Decode data
                            payload_json = data.get(b"payload" if isinstance(list(data.keys())[0], bytes) else "payload")
                            try:
                                payload = json.loads(payload_json) if payload_json else {}
                            except Exception:
                                payload = {}
                            
                            self.queue.move_to_dead_letter(
                                msg_id,
                                payload,
                                dead_letter_queue=RedisQueues.DEAD_LETTERS
                            )
                        self.queue.ack(RedisQueues.NOTIFICATIONS, "dispatcher", msg_id)
                        continue
                    
                    # 2. Claim stuck messages (> 60 seconds pending/idle)
                    if time_since_delivered > 60000:
                        logger.info(f"Stuck message detected: message {msg_id} pending for {time_since_delivered / 1000}s. Reclaiming and processing.")
                        try:
                            # Claim to worker-0 consumer so it can be processed
                            claimed = await asyncio.to_thread(
                                self.redis.xclaim,
                                RedisQueues.NOTIFICATIONS,
                                "dispatcher",
                                f"{self.consumer_name}-0",
                                60000,
                                [msg_id],
                            )
                            # Process the claimed message directly
                            if claimed:
                                for claimed_id, claimed_data in claimed:
                                    c_id = claimed_id if isinstance(claimed_id, str) else claimed_id.decode()
                                    payload_json = claimed_data.get("payload") or claimed_data.get(b"payload")
                                    if payload_json:
                                        if isinstance(payload_json, bytes):
                                            payload_json = payload_json.decode()
                                        try:
                                            payload = json.loads(payload_json)
                                            await self._process_message(c_id, payload)
                                            self.queue.ack(RedisQueues.NOTIFICATIONS, "dispatcher", c_id)
                                            logger.info(f"Reclaimed message {c_id} processed and ACKed.")
                                        except Exception as proc_err:
                                            logger.error(f"Failed to process reclaimed message {c_id}: {proc_err}", exc_info=True)
                                    else:
                                        # No payload — just ACK to clear it
                                        self.queue.ack(RedisQueues.NOTIFICATIONS, "dispatcher", c_id)
                                        logger.warning(f"Reclaimed message {c_id} had no payload — ACKed to clear.")
                        except Exception as claim_err:
                            logger.error(f"Failed to claim message {msg_id}: {claim_err}", exc_info=True)
                        reclaimed_count += 1
                
                # STEP 5 — Log reclaim scan results
                logger.info({
                    "event": "reclaim_scan",
                    "pending": len(pending_info_list),
                    "reclaimed": reclaimed_count,
                    "poison": poison_count,
                })
                        
            except Exception as e:
                logger.exception(f"Error in stream pending message reclaimer loop: {e}")
if __name__ == "__main__":
    import asyncio
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO)

    # Load Redis URL
    redis_url = os.getenv(
        "REDIS_URL",
        "redis://127.0.0.1:6379/0"
    )

    # Load DB URL
    database_url = os.getenv("SEMABRIDGE_DATABASE_URL")

    if not database_url:
        raise RuntimeError("SEMABRIDGE_DATABASE_URL environment variable missing")

    # Create DB session
    engine = create_engine(database_url)
    SessionLocal = sessionmaker(bind=engine)
    db_session = SessionLocal()

    # Create dispatcher
    worker = DispatcherWorker(
        redis_url=redis_url,
        db_session=db_session,
    )

    async def _run_with_graceful_shutdown():
        """Run dispatcher and handle shutdown gracefully so in-flight deliveries complete."""
        loop = asyncio.get_running_loop()
        import signal

        shutdown_event = asyncio.Event()

        def _request_shutdown():
            logger.info("Shutdown signal received — stopping dispatcher after current deliveries")
            shutdown_event.set()
            worker.running = False

        # Register SIGINT/SIGTERM (SIGTERM only available on Unix)
        try:
            loop.add_signal_handler(signal.SIGINT, _request_shutdown)
            loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
        except NotImplementedError:
            # Windows proactor does not support add_signal_handler; rely on KeyboardInterrupt
            pass

        start_task = asyncio.create_task(worker.start())
        try:
            await asyncio.wait_for(start_task, timeout=None)
        except (KeyboardInterrupt, asyncio.CancelledError):
            _request_shutdown()
            # Give in-flight deliveries up to 15 s to finish
            try:
                await asyncio.wait_for(asyncio.shield(start_task), timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        finally:
            logger.info("Dispatcher worker stopped.")
            db_session.close()

    try:
        asyncio.run(_run_with_graceful_shutdown())
    except KeyboardInterrupt:
        print("Dispatcher worker stopped.")