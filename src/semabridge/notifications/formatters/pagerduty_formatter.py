"""
PagerDuty Events API v2 formatter.
"""

from typing import Dict, Any
from ..models import NotificationEvent
from ..constants import NotificationLevel, level_to_string
from .base import BaseFormatter


class PagerDutyFormatter(BaseFormatter):
    """
    Format NotificationEvent into PagerDuty Events API v2 payload.
    """
    
    # Level to PagerDuty action mapping
    LEVEL_TO_ACTION = {
        NotificationLevel.CRITICAL: "trigger",
        NotificationLevel.ERROR: "trigger",
        NotificationLevel.WARNING: "trigger",
        NotificationLevel.INFO: "trigger",
        NotificationLevel.DEBUG: "trigger",
        NotificationLevel.SYNC_RESULT: "trigger",
    }
    
    # Level to PagerDuty severity mapping
    LEVEL_TO_SEVERITY = {
        NotificationLevel.CRITICAL: "critical",
        NotificationLevel.ERROR: "error",
        NotificationLevel.WARNING: "warning",
        NotificationLevel.INFO: "info",
        NotificationLevel.DEBUG: "info",
        NotificationLevel.SYNC_RESULT: "info",
    }
    
    def format(
        self,
        event: NotificationEvent,
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Format event as PagerDuty Events API v2 payload.
        
        Uses dedup_key to prevent duplicate incidents and allow incident resolution.
        
        Args:
            event: Notification event
            channel_config: PagerDuty channel config
        
        Returns:
            PagerDuty Events API v2 payload
        """
        level_str = level_to_string(event.level)
        action = self._get_action(event.level)
        severity = self._get_severity(event.level)
        
        # Check if this is a resolution event (SYNC_RESULT with clean status)
        # Resolution happens when event is SYNC_RESULT (level includes SYNC_RESULT bit)
        # and message indicates success or no errors
        is_resolution = self._is_resolution_event(event)
        
        if is_resolution:
            action = "resolve"
        
        # Truncate message for PagerDuty
        message = event.message
        if len(message) > 1000:
            message = message[:997] + "..."
        
        # Build custom details
        custom_details = {
            "message": message,
            "project_id": event.project_id or "unknown",
            "correlation_id": event.correlation_id or "unknown",
        }
        
        if event.sync_job_id:
            custom_details["sync_job_id"] = event.sync_job_id
        
        # Generate fingerprint for deduplication key
        dedup_key = event.fingerprint or self._generate_dedup_key(event)
        
        # Build PagerDuty payload
        payload = {
            "event_action": action,
            "dedup_key": dedup_key,
            "payload": {
                "summary": event.title,
                "severity": severity,
                "source": event.source or "semabridge",
                "custom_details": custom_details,
            }
        }
        
        # Add timestamp if available
        if event.created_at:
            payload["payload"]["timestamp"] = event.created_at.isoformat()
        
        return payload
    
    def _get_action(self, level: int) -> str:
        """Get PagerDuty action for severity level."""
        for bit_value in sorted(self.LEVEL_TO_ACTION.keys(), reverse=True):
            if level & bit_value:
                return self.LEVEL_TO_ACTION[bit_value]
        return "trigger"
    
    def _get_severity(self, level: int) -> str:
        """Get PagerDuty severity for notification level."""
        for bit_value in sorted(self.LEVEL_TO_SEVERITY.keys(), reverse=True):
            if level & bit_value:
                return self.LEVEL_TO_SEVERITY[bit_value]
        return "info"
    
    def _is_resolution_event(self, event: NotificationEvent) -> bool:
        """
        Determine if this event should resolve an incident.
        
        Resolution happens when:
        - Event is SYNC_RESULT type
        - Message indicates success (no errors/warnings) OR explicitly marked as resolved
        """
        if event.type != "sync_completion":
            return False
        
        # Check if message indicates success
        message_lower = (event.message or "").lower()
        
        # Keywords that indicate success/resolution
        success_keywords = [
            "success",
            "complete",
            "finished",
            "0 errors",
            "no errors",
            "clean",
            "resolved",
            "fixed",
        ]
        
        for keyword in success_keywords:
            if keyword in message_lower:
                return True
        
        # Keywords that indicate failures/problems (don't resolve)
        failure_keywords = [
            "error",
            "failed",
            "failed",
            "failure",
            "warning",
            "critical",
        ]
        
        for keyword in failure_keywords:
            if keyword in message_lower:
                return False
        
        return False
    
    def _generate_dedup_key(self, event: NotificationEvent) -> str:
        """
        Generate deduplication key for incident tracking.
        
        Combines sync_job_id + title + project to create unique incident ID.
        """
        import hashlib
        
        content = f"{event.sync_job_id or 'unknown'}{event.title}{event.project_id or 'all'}"
        return hashlib.sha256(content.encode()).hexdigest()
