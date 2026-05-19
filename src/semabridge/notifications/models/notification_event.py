"""
Core domain models for the notifications system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4
import hashlib


@dataclass
class NotificationEvent:
    """
    Canonical event model used throughout the system.
    Never pass channel-specific dicts through business logic.
    """
    id: UUID = field(default_factory=uuid4)
    correlation_id: str = ""
    sync_job_id: Optional[str] = None
    project_id: Optional[str] = None
    type: str = ""
    level: int = 0  # bitmask (see NotificationLevel)
    title: str = ""
    message: str = ""
    payload: dict = field(default_factory=dict)
    source: str = ""  # e.g., "sync_engine", "api", "test"
    created_at: datetime = field(default_factory=datetime.utcnow)
    sequence_number: int = 0
    fingerprint: str = ""  # sha256(sync_job_id+level+title+channel_id)

    def generate_fingerprint(self, channel_id: str) -> str:
        """Generate deduplication fingerprint for this event with a specific channel."""
        content = f"{self.sync_job_id}{self.level}{self.title}{channel_id}"
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": str(self.id),
            "correlation_id": self.correlation_id,
            "sync_job_id": self.sync_job_id,
            "project_id": self.project_id,
            "type": self.type,
            "level": self.level,
            "title": self.title,
            "message": self.message,
            "payload": self.payload,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "sequence_number": self.sequence_number,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NotificationEvent":
        """Reconstruct from dictionary."""
        return cls(
            id=UUID(data.get("id", str(uuid4()))),
            correlation_id=data.get("correlation_id", ""),
            sync_job_id=data.get("sync_job_id"),
            project_id=data.get("project_id"),
            type=data.get("type", ""),
            level=data.get("level", 0),
            title=data.get("title", ""),
            message=data.get("message", ""),
            payload=data.get("payload", {}),
            source=data.get("source", ""),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.utcnow().isoformat())),
            sequence_number=data.get("sequence_number", 0),
            fingerprint=data.get("fingerprint", ""),
        )
