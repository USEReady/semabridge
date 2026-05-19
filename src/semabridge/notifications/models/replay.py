"""
Replay domain models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


@dataclass
class ReplayFilter:
    """
    Filter for selecting notification logs to replay.
    All fields are optional and ANDed together.
    """
    status: List[str] = field(default_factory=lambda: ["failed", "dead"])
    channel_id: Optional[str] = None
    level_mask: Optional[int] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: int = 100  # max 500


@dataclass
class ReplayResult:
    """
    Result of replaying a single event.
    """
    log_id: str
    original_event_id: str
    new_event_id: str
    channels_targeted: List[str] = field(default_factory=list)
    enqueued_at: Optional[datetime] = None


@dataclass
class BulkReplayResult:
    """
    Result of bulk replaying events.
    """
    replayed: int = 0
    skipped: int = 0
    errors: int = 0
    details: List[ReplayResult] = field(default_factory=list)
