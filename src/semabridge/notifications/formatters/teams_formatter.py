"""
Microsoft Teams Adaptive Cards formatter.
"""

from typing import Dict, Any
from ..models import NotificationEvent
from ..constants import NotificationLevel, level_to_string
from .base import BaseFormatter


class TeamsFormatter(BaseFormatter):
    """
    Format NotificationEvent into Teams Adaptive Card payload.
    """
    
    # Severity color mapping (hex)
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
        Format event as Teams Adaptive Card message.
        
        Args:
            event: Notification event
            channel_config: Teams channel config
        
        Returns:
            Teams webhook payload with Adaptive Card + plaintext fallback
        """
        # Determine severity color
        color = self._get_color(event.level)
        level_str = level_to_string(event.level)
        
        # Truncate message if needed
        message_text = event.message
        truncated = False
        
        if len(message_text) > 2000:
            message_text = self._truncate_text(message_text, 2000 - 50, suffix="...")
            truncated = True
        
        # Build Adaptive Card
        adaptive_card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": event.title,
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": self._color_to_teams_color(color),
                            },
                            {
                                "type": "TextBlock",
                                "text": f"**Level:** {level_str}",
                                "size": "Small",
                                "spacing": "Small",
                            },
                            {
                                "type": "TextBlock",
                                "text": message_text,
                                "wrap": True,
                                "spacing": "Medium",
                            },
                        ]
                    }
                }
            ]
        }
        
        # Add facts if needed
        facts = []
        if event.sync_job_id:
            facts.append({"name": "Job ID", "value": event.sync_job_id})
        if event.project_id:
            facts.append({"name": "Project", "value": event.project_id})
        if event.correlation_id:
            facts.append({"name": "Correlation", "value": event.correlation_id})
        if event.created_at:
            facts.append({"name": "Time", "value": event.created_at.isoformat()})
        
        if facts:
            adaptive_card["attachments"][0]["content"]["body"].append({
                "type": "FactSet",
                "facts": facts,
                "spacing": "Medium",
            })
        
        if truncated:
            adaptive_card["attachments"][0]["content"]["body"].append({
                "type": "TextBlock",
                "text": "_Message truncated. View full report in SemaBridge dashboard._",
                "size": "Small",
                "color": "accent",
            })
        
        # Build plaintext fallback
        plaintext_lines = [
            f"[{level_str}] {event.title}",
            "",
            message_text,
            "",
            f"Time: {event.created_at.isoformat()}",
        ]
        
        if event.sync_job_id:
            plaintext_lines.append(f"Job: {event.sync_job_id}")
        if event.project_id:
            plaintext_lines.append(f"Project: {event.project_id}")
        if event.correlation_id:
            plaintext_lines.append(f"Correlation: {event.correlation_id}")
        
        plaintext_fallback = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "text/plain",
                    "content": "\n".join(plaintext_lines),
                }
            ]
        }
        
        # Return both formats for adaptive card to try first, then fallback
        adaptive_card["plaintext_fallback"] = plaintext_fallback
        
        return adaptive_card
    
    def _get_color(self, level: int) -> str:
        """Get hex color for severity level."""
        # Find the highest severity bit set
        for bit_value in sorted(self.SEVERITY_COLORS.keys(), reverse=True):
            if level & bit_value:
                return self.SEVERITY_COLORS[bit_value]
        return self.SEVERITY_COLORS[NotificationLevel.INFO]
    
    def _color_to_teams_color(self, hex_color: str) -> str:
        """
        Convert hex color to Teams adaptive card color name.
        Teams uses predefined colors in Adaptive Cards.
        """
        color_map = {
            "#FF0000": "Attention",  # Red
            "#FF6600": "Warning",    # Orange
            "#FFCC00": "Warning",    # Yellow
            "#36A64F": "Good",       # Green
            "#808080": "Accent",     # Gray
        }
        return color_map.get(hex_color, "Accent")
