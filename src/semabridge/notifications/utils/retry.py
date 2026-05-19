"""
Retry logic and exponential backoff utilities.
"""

from datetime import datetime, timedelta
from typing import Dict, Any


class RetryConfig:
    """Configuration for retry behavior."""
    
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_BACKOFF_DELAYS = [5, 30, 120]  # seconds: 5s, 30s, 2min
    
    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_delays: list = None
    ):
        self.max_retries = max_retries
        self.backoff_delays = backoff_delays or self.DEFAULT_BACKOFF_DELAYS


class RetryMetadata:
    """Metadata for tracking retry attempts."""
    
    def __init__(
        self,
        attempt_count: int = 0,
        last_attempt: datetime = None,
        next_attempt: datetime = None,
    ):
        self.attempt_count = attempt_count
        self.last_attempt = last_attempt
        self.next_attempt = next_attempt
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "attempt_count": self.attempt_count,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
            "next_attempt": self.next_attempt.isoformat() if self.next_attempt else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetryMetadata":
        """Reconstruct from dictionary."""
        return cls(
            attempt_count=data.get("attempt_count", 0),
            last_attempt=datetime.fromisoformat(data["last_attempt"])
            if data.get("last_attempt")
            else None,
            next_attempt=datetime.fromisoformat(data["next_attempt"])
            if data.get("next_attempt")
            else None,
        )


def calculate_next_retry_time(
    attempt_count: int,
    config: RetryConfig,
) -> datetime:
    """
    Calculate when the next retry should occur using exponential backoff.
    
    Args:
        attempt_count: Current attempt number (0-indexed)
        config: Retry configuration
    
    Returns:
        Datetime for next retry
    """
    if attempt_count >= len(config.backoff_delays):
        # Max retries exceeded
        return None
    
    delay_seconds = config.backoff_delays[attempt_count]
    return datetime.utcnow() + timedelta(seconds=delay_seconds)


def should_retry(
    attempt_count: int,
    config: RetryConfig,
) -> bool:
    """Check if we should retry based on attempt count."""
    return attempt_count < config.max_retries


def get_delay_until_next_retry(next_attempt: datetime) -> int:
    """
    Get the number of seconds until next retry.
    
    Returns:
        Seconds until retry, or 0 if retry time has passed
    """
    if next_attempt is None:
        return 0
    
    now = datetime.utcnow()
    if next_attempt <= now:
        return 0
    
    delta = next_attempt - now
    return max(0, int(delta.total_seconds()))


def is_ready_to_retry(next_attempt: datetime) -> bool:
    """Check if it's time to retry."""
    if next_attempt is None:
        return False
    
    return datetime.utcnow() >= next_attempt
