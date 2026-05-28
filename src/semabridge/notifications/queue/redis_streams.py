"""
Redis Streams-based queue implementation for reliable message processing.

Uses XADD/XREADGROUP/XACK for guaranteed delivery with ACK semantics.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class RedisStreamsQueue:
    """
    Redis Streams-based queue with ACK semantics and reliable delivery.
    """
    
    def __init__(self, redis_url: str, decode_responses: bool = True):
        """
        Initialize Redis Streams queue.
        
        Args:
            redis_url: Redis connection URL
            decode_responses: Whether to decode responses as strings
        """
        self.redis = redis.from_url(redis_url, decode_responses=decode_responses)
        self.decode_responses = decode_responses
    
    def enqueue(self, queue_name: str, payload: Dict, message_id: str = "*") -> str:
        """
        Add a message to the stream.
        
        Args:
            queue_name: Name of the stream/queue
            payload: Message payload (dict)
            message_id: Redis stream ID (default: auto-generate)
        
        Returns:
            The stream entry ID
        """
        try:
            # Serialize payload to JSON
            data = {
                "payload": json.dumps(payload),
                "enqueued_at": datetime.utcnow().isoformat(),
            }
            
            entry_id = self.redis.xadd(queue_name, data, id=message_id)
            logger.debug(f"Enqueued message {entry_id} to {queue_name}")
            return entry_id if isinstance(entry_id, str) else entry_id.decode() if entry_id else None
        except RedisError as e:
            try:
                conn_kwargs = self.redis.connection_pool.connection_kwargs
                host = conn_kwargs.get("host", "localhost")
                port = conn_kwargs.get("port", 6379)
                db = conn_kwargs.get("db", 0)
                timeout = conn_kwargs.get("socket_timeout", "default")
                redis_info = f"redis://{host}:{port}/{db} (timeout: {timeout}s)"
            except Exception:
                import os
                redis_info = os.getenv("REDIS_URL", "redis://localhost:6379")
            
            logger.error(
                f"Failed to enqueue message to stream '{queue_name}': "
                f"Exception={type(e).__name__}, Message={e}, RedisURL={redis_info}"
            )
            raise
    
    def create_consumer_group(
        self,
        queue_name: str,
        group_name: str,
        start_id: str = "0"
    ) -> bool:
        """
        Create a consumer group if it doesn't exist.
        
        Args:
            queue_name: Stream name
            group_name: Consumer group name
            start_id: Start ID for processing (default: 0 = from beginning)
        
        Returns:
            True if created, False if already exists
        """
        try:
            self.redis.xgroup_create(queue_name, group_name, id=start_id, mkstream=True)
            logger.info(f"Created consumer group {group_name} on stream {queue_name}")
            return True
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                # Group already exists
                return False
            raise
    
    def consume(
        self,
        queue_name: str,
        group_name: str,
        consumer_name: str,
        count: int = 1,
        timeout_ms: int = 1000,
    ) -> List[Tuple[str, Dict]]:
        """
        Consume messages from a stream as a consumer group.
        
        Uses XREADGROUP to maintain per-consumer tracking.
        
        Args:
            queue_name: Stream name
            group_name: Consumer group name
            consumer_name: Consumer name within the group
            count: Number of messages to consume
            timeout_ms: Timeout for blocking read
        
        Returns:
            List of (message_id, payload) tuples
        """
        try:
            messages = self.redis.xreadgroup(
                groupname=group_name,
                consumername=consumer_name,
                streams={queue_name: ">"},  # Read new messages
                count=count,
                block=timeout_ms,
            )
            
            results = []
            if messages:
                for stream_name, stream_messages in messages:
                    for message_id, data in stream_messages:
                        message_id_str = (
                            message_id if isinstance(message_id, str)
                            else message_id.decode()
                        )
                        
                        # Decode payload
                        try:
                            payload_json = data.get(b"payload" if not self.decode_responses else "payload")
                            payload = json.loads(payload_json)
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.warning(f"Failed to decode payload: {e}")
                            payload = {}
                        
                        results.append((message_id_str, payload))
            
            return results
        except RedisError as e:
            logger.error(f"Failed to consume messages: {e}")
            return []
    
    def ack(self, queue_name: str, group_name: str, message_id: str) -> bool:
        """
        Acknowledge a message (mark as processed).
        
        Args:
            queue_name: Stream name
            group_name: Consumer group name
            message_id: Message ID to acknowledge
        
        Returns:
            True if successful
        """
        try:
            self.redis.xack(queue_name, group_name, message_id)
            logger.debug(f"Acknowledged message {message_id} in group {group_name}")
            return True
        except RedisError as e:
            logger.error(f"Failed to acknowledge message: {e}")
            return False
    
    def nack(
        self,
        queue_name: str,
        group_name: str,
        consumer_name: str,
        message_id: str,
    ) -> bool:
        """
        Negative acknowledge (return message to pending for retry).
        
        In Redis Streams, this is done by claiming the message back.
        
        Args:
            queue_name: Stream name
            group_name: Consumer group name
            consumer_name: Consumer name
            message_id: Message ID to nack
        
        Returns:
            True if successful
        """
        try:
            # Claim the message with 0 timeout (mark as idle)
            self.redis.xclaim(queue_name, group_name, consumer_name, 0, [message_id])
            logger.debug(f"Returned message {message_id} for retry")
            return True
        except RedisError as e:
            logger.error(f"Failed to return message for retry: {e}")
            return False
    
    def move_to_dead_letter(
        self,
        message_id: str,
        payload: Dict,
        dead_letter_queue: str = "semabridge:dead-letters",
    ) -> bool:
        """
        Move a message to the dead-letter queue.
        
        Args:
            message_id: Original message ID
            payload: Message payload
            dead_letter_queue: Dead-letter queue name
        
        Returns:
            True if successful
        """
        try:
            # Add to dead-letter queue with additional metadata
            dl_payload = {
                **payload,
                "original_message_id": message_id,
                "moved_to_dead_letter_at": datetime.utcnow().isoformat(),
            }
            self.enqueue(dead_letter_queue, dl_payload)
            logger.info(f"Moved message {message_id} to dead-letter queue")
            return True
        except Exception as e:
            logger.error(f"Failed to move message to dead-letter: {e}")
            return False
    
    def get_pending_count(
        self,
        queue_name: str,
        group_name: str,
    ) -> int:
        """
        Get the count of pending (unacknowledged) messages.
        
        Args:
            queue_name: Stream name
            group_name: Consumer group name
        
        Returns:
            Number of pending messages
        """
        try:
            info = self.redis.xinfo_groups(queue_name)
            for group_info in info:
                group_name_in_info = group_info.get(b"name" if not self.decode_responses else "name")
                if (isinstance(group_name_in_info, bytes) and group_name_in_info.decode() == group_name) or \
                   (isinstance(group_name_in_info, str) and group_name_in_info == group_name):
                    return group_info.get(b"pending" if not self.decode_responses else "pending", 0)
            return 0
        except RedisError as e:
            logger.warning(f"Failed to get pending count: {e}")
            return 0
    
    def get_pending_messages(
        self,
        queue_name: str,
        group_name: str,
        start: str = "-",
        end: str = "+",
        count: int = 10,
    ) -> List[Dict]:
        """
        Get list of pending messages with per-message detail.
        
        Uses XPENDING with range args (xpending_range) to return individual
        message info including message_id, consumer, time_since_delivered,
        and times_delivered.
        
        Args:
            queue_name: Stream name
            group_name: Consumer group name
            start: Start ID (default "-" = earliest)
            end: End ID (default "+" = latest)
            count: Maximum to return
        
        Returns:
            List of pending message info dicts with keys:
            message_id, consumer, time_since_delivered, times_delivered
        """
        try:
            pending = self.redis.xpending_range(
                queue_name, group_name, min=start, max=end, count=count,
            )
            return pending if pending else []
        except RedisError as e:
            logger.warning(f"Failed to get pending messages: {e}")
            return []

    def get_pending_summary(
        self,
        queue_name: str,
        group_name: str,
    ) -> Dict:
        """
        Get aggregate pending summary (total pending, min/max IDs, consumers).
        
        Uses the XPENDING summary form.
        
        Args:
            queue_name: Stream name
            group_name: Consumer group name
        
        Returns:
            Dict with pending count, min, max, and consumers info
        """
        try:
            return self.redis.xpending(queue_name, group_name) or {}
        except RedisError as e:
            logger.warning(f"Failed to get pending summary: {e}")
            return {}
    
    def cleanup_stream(self, queue_name: str, max_len: int = 10000) -> bool:
        """
        Trim a stream to a maximum length to prevent unbounded growth.
        
        Args:
            queue_name: Stream name
            max_len: Maximum number of entries to keep
        
        Returns:
            True if successful
        """
        try:
            self.redis.xtrim(queue_name, maxlen=max_len, approximate=True)
            logger.debug(f"Trimmed stream {queue_name} to ~{max_len} entries")
            return True
        except RedisError as e:
            logger.warning(f"Failed to trim stream: {e}")
            return False
    
    def health_check(self) -> bool:
        """Check Redis connectivity."""
        try:
            self.redis.ping()
            return True
        except RedisError:
            return False
