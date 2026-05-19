"""
Analytics domain models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class DeliveryStats:
    """
    Aggregated delivery statistics over a time window.
    """
    total: int = 0
    delivered: int = 0
    failed: int = 0
    retrying: int = 0
    dead: int = 0
    avg_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    by_channel: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_level: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total == 0:
            return 0.0
        return (self.delivered / self.total) * 100.0


@dataclass
class ChannelHealth:
    """
    Health metrics for a single notification channel.
    """
    channel_id: str
    channel_name: str = ""
    success_rate: float = 0.0  # 0.0 - 100.0
    avg_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    consecutive_failures: int = 0
    circuit_state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    last_delivery_at: Optional[datetime] = None
    total_deliveries: int = 0
    total_failures: int = 0
