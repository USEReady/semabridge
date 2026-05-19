"""
Circuit breaker service for channel failure management.

Implements the circuit breaker pattern to gracefully handle repeated failures:
- CLOSED: normal operation
- OPEN: stop calling the service after repeated failures
- HALF_OPEN: allow one probe request to test if service recovered
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import redis

logger = logging.getLogger(__name__)


class CircuitBreakerService:
    """
    Circuit breaker for notification adapters.
    
    Prevents cascading failures and allows time for recovery.
    """
    
    # Circuit breaker thresholds
    THRESHOLD = 3          # consecutive failures to open
    COOLDOWN_SEC = 60      # seconds before half-open probe
    
    def __init__(self, redis_client: redis.Redis):
        """
        Initialize circuit breaker service.
        
        Args:
            redis_client: Redis client for state persistence
        """
        self.redis = redis_client
    
    async def record_success(self, channel_id: str) -> None:
        """
        Record a successful delivery.
        
        Resets failure count and moves circuit to CLOSED state.
        
        Args:
            channel_id: Channel identifier
        """
        # Delete failure counter and half-open flag
        self.redis.delete(f"semabridge:cb:{channel_id}:failures")
        self.redis.delete(f"semabridge:cb:{channel_id}:half_open")
        self.redis.delete(f"semabridge:cb:{channel_id}:cooldown_until")
        
        logger.info(f"Circuit breaker reset for channel {channel_id}")
    
    async def record_failure(self, channel_id: str) -> None:
        """
        Record a failed delivery.
        
        Increments failure count. If count >= THRESHOLD, opens the circuit.
        
        Args:
            channel_id: Channel identifier
        """
        failures_key = f"semabridge:cb:{channel_id}:failures"
        cooldown_key = f"semabridge:cb:{channel_id}:cooldown_until"
        
        # Increment failure count
        failures = self.redis.incr(failures_key)
        
        # Set TTL to 5 minutes (after which counter resets)
        self.redis.expire(failures_key, 300)
        
        logger.warning(f"Channel {channel_id} failure recorded ({failures}/{self.THRESHOLD})")
        
        # If threshold reached, open circuit
        if failures >= self.THRESHOLD:
            cooldown_until = datetime.utcnow() + timedelta(seconds=self.COOLDOWN_SEC)
            self.redis.set(
                cooldown_key,
                int(cooldown_until.timestamp()),
            )
            logger.error(f"Circuit breaker OPENED for channel {channel_id}")
    
    async def is_open(self, channel_id: str) -> bool:
        """
        Check if circuit is open (should skip this channel).
        
        Implements half-open state: if cooldown has expired, allow one probe request.
        
        Args:
            channel_id: Channel identifier
        
        Returns:
            True if circuit is open (don't call adapter)
        """
        cooldown_key = f"semabridge:cb:{channel_id}:cooldown_until"
        half_open_key = f"semabridge:cb:{channel_id}:half_open"
        
        cooldown_until_str = self.redis.get(cooldown_key)
        if not cooldown_until_str:
            # Circuit is closed
            return False
        
        try:
            cooldown_until = datetime.utcfromtimestamp(int(cooldown_until_str))
        except (ValueError, TypeError):
            # Corrupt data, reset
            self.redis.delete(cooldown_key)
            return False
        
        now = datetime.utcnow()
        
        if now < cooldown_until:
            # Still in cooldown, circuit is open
            return True
        
        # Cooldown has expired, go half-open
        # Allow one probe by checking half_open flag
        if self.redis.get(half_open_key):
            # Already attempted probe, wait for result
            return True
        
        # First probe - allow it
        self.redis.set(half_open_key, "true", ex=30)  # 30s timeout for probe
        logger.info(f"Circuit breaker HALF_OPEN for channel {channel_id} - allowing probe")
        
        return False
    
    async def get_status(self, channel_id: str) -> str:
        """
        Get current circuit breaker status.
        
        Args:
            channel_id: Channel identifier
        
        Returns:
            "CLOSED", "OPEN", or "HALF_OPEN"
        """
        cooldown_key = f"semabridge:cb:{channel_id}:cooldown_until"
        half_open_key = f"semabridge:cb:{channel_id}:half_open"
        
        cooldown_until_str = self.redis.get(cooldown_key)
        
        if not cooldown_until_str:
            return "CLOSED"
        
        try:
            cooldown_until = datetime.utcfromtimestamp(int(cooldown_until_str))
        except (ValueError, TypeError):
            return "CLOSED"
        
        now = datetime.utcnow()
        
        if now < cooldown_until:
            return "OPEN"
        
        if self.redis.get(half_open_key):
            return "HALF_OPEN"
        
        return "CLOSED"
    
    async def reset(self, channel_id: str) -> None:
        """
        Manually reset circuit breaker (for admin operations).
        
        Args:
            channel_id: Channel identifier
        """
        await self.record_success(channel_id)
