"""
Snowflake formatter - converts NotificationEvent to flat dict for table insertion.
"""

import json
from typing import Dict, Any
from ..models import NotificationEvent
from ..constants import level_to_string
from .base import BaseFormatter


class SnowflakeFormatter(BaseFormatter):
    """
    Format NotificationEvent as a flat dict for Snowflake table row.
    """
    
    def format(
        self,
        event: NotificationEvent,
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Format event as Snowflake table row.
        
        Args:
            event: Notification event
            channel_config: Snowflake channel config (unused for formatting)
        
        Returns:
            Dict with columns: event_id, correlation_id, sync_job_id, project_id,
            level, title, message, source, created_at, payload_json
        """
        return {
            "event_id": str(event.id),
            "correlation_id": event.correlation_id,
            "sync_job_id": event.sync_job_id,
            "project_id": event.project_id,
            "level": level_to_string(event.level),
            "level_numeric": event.level,
            "title": event.title[:1000] if event.title else None,  # Truncate to reasonable limit
            "message": event.message[:4000] if event.message else None,
            "source": event.source,
            "type": event.type,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "payload_json": json.dumps(event.payload),
            "sequence_number": event.sequence_number,
        }
