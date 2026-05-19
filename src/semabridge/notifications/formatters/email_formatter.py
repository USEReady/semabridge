"""
Email formatter for HTML and plaintext notifications.
"""

from typing import Dict, Any
from html import escape
from ..models import NotificationEvent
from ..constants import level_to_string
from .base import BaseFormatter


class EmailFormatter(BaseFormatter):
    """
    Format NotificationEvent as HTML email with plaintext fallback.
    """
    
    def format(
        self,
        event: NotificationEvent,
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Format event as HTML email.
        
        Args:
            event: Notification event
            channel_config: Email channel config
        
        Returns:
            Email payload with html and plaintext variants
        """
        # HTML version
        html_content = self._build_html(event, channel_config)
        
        # Plaintext version
        plaintext_content = self._build_plaintext(event, channel_config)
        
        return {
            "subject": f"[{level_to_string(event.level)}] {event.title}",
            "html": html_content,
            "plaintext": plaintext_content,
            "from_address": channel_config.get("from_address", "noreply@semabridge.local"),
            "to_addresses": channel_config.get("to_addresses", []),
        }
    
    def _build_html(self, event: NotificationEvent, channel_config: Dict[str, Any]) -> str:
        """Build HTML email content."""
        level_name = level_to_string(event.level)
        escaped_title = escape(event.title)
        escaped_message = escape(event.message).replace("\n", "<br>")
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #f5f5f5; padding: 20px; border-radius: 4px; margin-bottom: 20px; }}
        .title {{ font-size: 24px; font-weight: bold; margin: 0; }}
        .level {{ font-size: 12px; font-weight: 600; text-transform: uppercase; margin: 5px 0 0 0; }}
        .content {{ margin: 20px 0; line-height: 1.5; }}
        .metadata {{ background-color: #f9f9f9; padding: 12px; border-radius: 4px; margin: 20px 0; }}
        .metadata-row {{ margin: 5px 0; font-size: 13px; }}
        .metadata-label {{ font-weight: 600; }}
        .footer {{ font-size: 12px; color: #666; margin-top: 20px; border-top: 1px solid #eee; padding-top: 10px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">{escaped_title}</div>
            <div class="level">{level_name}</div>
        </div>
        <div class="content">
            {escaped_message}
        </div>
"""
        
        # Add metadata
        metadata_items = []
        if event.sync_job_id:
            metadata_items.append(f"<div class='metadata-row'><span class='metadata-label'>Job ID:</span> {escape(event.sync_job_id)}</div>")
        if event.project_id:
            metadata_items.append(f"<div class='metadata-row'><span class='metadata-label'>Project:</span> {escape(event.project_id)}</div>")
        if event.correlation_id:
            metadata_items.append(f"<div class='metadata-row'><span class='metadata-label'>Correlation ID:</span> {escape(event.correlation_id)}</div>")
        
        if metadata_items:
            html += "<div class='metadata'>\n" + "\n".join(metadata_items) + "\n</div>"
        
        html += f"""
        <div class="footer">
            <p>Created: {event.created_at.isoformat()}</p>
            <p>This is an automated notification from SemaBridge.</p>
        </div>
    </div>
</body>
</html>
"""
        return html
    
    def _build_plaintext(self, event: NotificationEvent, channel_config: Dict[str, Any]) -> str:
        """Build plaintext email content."""
        level_name = level_to_string(event.level)
        
        lines = [
            f"{event.title}",
            f"Level: {level_name}",
            "",
            event.message,
            "",
        ]
        
        if event.sync_job_id:
            lines.append(f"Job ID: {event.sync_job_id}")
        if event.project_id:
            lines.append(f"Project: {event.project_id}")
        if event.correlation_id:
            lines.append(f"Correlation ID: {event.correlation_id}")
        
        lines.extend([
            "",
            f"Created: {event.created_at.isoformat()}",
            "",
            "---",
            "This is an automated notification from SemaBridge.",
        ])
        
        return "\n".join(lines)
