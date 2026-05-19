"""
Constants for the notifications system.
"""

from typing import Dict


class NotificationLevel:
    """Bitmask-based notification levels."""
    SYNC_RESULT = 1
    CRITICAL = 2
    ERROR = 4
    WARNING = 8
    INFO = 16
    DEBUG = 32
    ALL = 63

    _NAMES: Dict[int, str] = {
        SYNC_RESULT: "SYNC_RESULT",
        CRITICAL: "CRITICAL",
        ERROR: "ERROR",
        WARNING: "WARNING",
        INFO: "INFO",
        DEBUG: "DEBUG",
    }


class NotificationChannelType:
    """Supported channel types."""
    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"
    WEBHOOK = "webhook"
    PAGERDUTY = "pagerduty"
    SNOWFLAKE = "snowflake"  # Phase 3


class NotificationStatus:
    """Status of a notification delivery."""
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"
    SKIPPED_DEDUPE = "skipped_dedupe"
    SKIPPED_QUIET = "skipped_quiet"


class ChannelStatus:
    """Status of a notification channel."""
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


class RedisQueues:
    """Redis queue names."""
    NOTIFICATIONS = "semabridge:notifications"
    RETRY = "semabridge:retry"
    DEAD_LETTERS = "semabridge:dead-letters"
    DIGEST_STAGING = "semabridge:digest-staging"


# Default configuration values
DEFAULT_DEDUPE_TTL_SEC = 300  # 5 minutes
DEFAULT_BATCH_WINDOW_SEC = 30  # 30 seconds
DEFAULT_MAX_RETRIES = 3
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 3
DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SEC = 60
WEBHOOK_TIMEOUT_SEC = 10
SMTP_POOL_SIZE = 5


def matches_level(event_level: int, channel_mask: int) -> bool:
    """Check if event level matches channel mask using bitwise AND."""
    return bool(event_level & channel_mask)


def level_to_string(level: int) -> str:
    """Convert notification level to string representation."""
    # Handle combinations by finding the highest set bit
    for bit_value in sorted(NotificationLevel._NAMES.keys(), reverse=True):
        if level & bit_value:
            return NotificationLevel._NAMES[bit_value]
    return "UNKNOWN"


def parse_level_mask(value: str | int) -> int:
    """Parse level mask from string or int."""
    if isinstance(value, int):
        return value
    
    value_upper = value.upper()
    if value_upper == "ALL":
        return NotificationLevel.ALL
    
    # Try to find matching level name
    for bit_value, name in NotificationLevel._NAMES.items():
        if name == value_upper:
            return bit_value
    
    # Try comma-separated values
    mask = 0
    for part in value_upper.split(","):
        part = part.strip()
        for bit_value, name in NotificationLevel._NAMES.items():
            if name == part:
                mask |= bit_value
                break
    
    return mask if mask else NotificationLevel.INFO
