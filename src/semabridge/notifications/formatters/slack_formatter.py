"""
Slack Block Kit formatter for rich Slack notifications.
"""

from typing import Dict, Any, List
from ..models import NotificationEvent
from ..constants import NotificationLevel, level_to_string
from .base import BaseFormatter


class SlackFormatter(BaseFormatter):
    """
    Format NotificationEvent into Slack Block Kit payload.
    """
    
    # Severity color mapping
    SEVERITY_COLORS = {
        NotificationLevel.CRITICAL: "#FF0000",  # Red
        NotificationLevel.ERROR: "#FF6600",     # Orange
        NotificationLevel.WARNING: "#FFCC00",   # Yellow
        NotificationLevel.INFO: "#36A64F",      # Green
        NotificationLevel.DEBUG: "#808080",     # Gray
        NotificationLevel.SYNC_RESULT: "#36A64F",  # Green
    }
    
    def format(
        self,
        event: NotificationEvent,
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Format event as Slack Block Kit message.
        
        Args:
            event: Notification event
            channel_config: Slack channel config
        
        Returns:
            Slack webhook payload with Block Kit formatting
        """
        # Determine severity color
        color = self._get_color(event.level)
        
        # Truncate message if needed
        message_text = event.message
        full_message = message_text
        truncated = False
        
        if len(message_text) > 3000:
            message_text = self._truncate_text(message_text, 3000 - 50)
            truncated = True
        
        # Build blocks
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{self._sanitize_markdown(event.title)}*\n_{level_to_string(event.level)}_"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message_text
                }
            },
        ]
        
        # Add "View Full Report" button if truncated
        if truncated:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_Message truncated. View full report in SemaBridge dashboard._"
                }
            })
        
        # Add context with metadata
        context_fields = []
        if event.sync_job_id:
            context_fields.append(f"*Job:* `{event.sync_job_id}`")
        if event.project_id:
            context_fields.append(f"*Project:* `{event.project_id}`")
        if event.correlation_id:
            context_fields.append(f"*Correlation:* `{event.correlation_id}`")
        
        if context_fields:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": " | ".join(context_fields)
                }
            })
        
        # Add timestamp
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_Created: {event.created_at.isoformat()}_"
                }
            ]
        })
        
        # Build payload
        payload = {
            "blocks": blocks,
            "attachments": [
                {
                    "color": color,
                    "fallback": event.title,
                }
            ]
        }
        
        # Add text fallback for older clients
        payload["text"] = f"{event.title} ({level_to_string(event.level)}): {event.message[:100]}"
        
        return payload
    
    def _get_color(self, level: int) -> str:
        """Get Slack color for severity level."""
        # Check each level bit from highest priority
        for bit_level in sorted(self.SEVERITY_COLORS.keys(), reverse=True):
            if level & bit_level:
                return self.SEVERITY_COLORS[bit_level]
        
        return self.SEVERITY_COLORS[NotificationLevel.INFO]
