"""
Deduplication service for preventing duplicate notifications.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import redis

from ...constants import DEFAULT_DEDUPE_TTL_SEC
from ..models import NotificationEvent

logger = logging.getLogger(__name__)


class DedupeService:
    """
    Tracks and prevents duplicate notifications within a TTL window.
    
    Uses Redis for distributed deduplication.
    """
    
    def __init__(self, redis_client: redis.Redis, ttl_sec: int = DEFAULT_DEDUPE_TTL_SEC):
        """
        Initialize deduplication service.
        
        Args:
            redis_client: Redis client
            ttl_sec: Time-to-live for fingerprints in seconds
        """
        self.redis = redis_client
        self.ttl_sec = ttl_sec
        self.prefix = "semabridge:dedupe:"
    
    async def is_duplicate(self, fingerprint: str) -> bool:
        """
        Check if a fingerprint has been seen recently.
        
        Args:
            fingerprint: Event fingerprint (SHA256 hash)
        
        Returns:
            True if duplicate, False if new
        """
        key = f"{self.prefix}{fingerprint}"
        
        try:
            exists = self.redis.exists(key)
            return bool(exists)
        except redis.RedisError as e:
            logger.error(f"Redis error checking duplicate: {e}")
            # Fail open - let it through if Redis is down
            return False
    
    async def mark_processed(self, fingerprint: str) -> bool:
        """
        Mark a fingerprint as processed with TTL expiry.
        
        Args:
            fingerprint: Event fingerprint
        
        Returns:
            True if successful
        """
        key = f"{self.prefix}{fingerprint}"
        
        try:
            self.redis.setex(
                key,
                self.ttl_sec,
                "1"
            )
            logger.debug(f"Marked fingerprint {fingerprint} as processed")
            return True
        except redis.RedisError as e:
            logger.error(f"Redis error marking processed: {e}")
            return False
    
    async def cleanup_expired(self) -> int:
        """
        Clean up expired fingerprints.
        
        Redis handles TTL automatically with SETEX, so this is mainly
        for administrative purposes (e.g., full scan cleanup).
        
        Returns:
            Number of keys cleaned up
        """
        # Redis automatically cleans up expired keys, so this is a no-op
        # This method exists for API consistency
        return 0
