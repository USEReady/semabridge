"""
Generic webhook formatter.
"""

from typing import Dict, Any
from datetime import datetime
from ..models import NotificationEvent
from ..constants import level_to_string
from .base import BaseFormatter


class WebhookFormatter(BaseFormatter):
    """
    Format NotificationEvent as generic JSON webhook payload.
    """
    
    def format(
        self,
        event: NotificationEvent,
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Format event as JSON webhook payload.
        
        Args:
            event: Notification event
            channel_config: Webhook channel config
        
        Returns:
            JSON payload dict
        """
        return {
            "event_id": str(event.id),
            "correlation_id": event.correlation_id,
            "sync_job_id": event.sync_job_id,
            "project_id": event.project_id,
            "level": level_to_string(event.level),
            "level_numeric": event.level,
            "title": event.title,
            "message": event.message,
            "payload": event.payload,
            "source": event.source,
            "timestamp": event.created_at.isoformat(),
            "type": event.type,
        }
