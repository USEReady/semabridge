"""
Base classes for notification formatters.
All formatters must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any

from ..models import NotificationEvent


class BaseFormatter(ABC):
    """
    Base class for all notification formatters.
    
    Formatters transform canonical NotificationEvent objects into
    channel-specific payload formats. They must not contain business logic.
    """
    
    @abstractmethod
    def format(
        self,
        event: NotificationEvent,
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Transform a NotificationEvent into a channel-ready payload.
        
        Must be a pure function with no side effects.
        
        Args:
            event: The notification event to format
            channel_config: Channel configuration (for channel-specific details)
        
        Returns:
            Channel-specific payload dict
            (structure depends on the channel type)
        """
        pass
    
    def _truncate_text(self, text: str, max_length: int, suffix: str = "...") -> str:
        """Truncate text to maximum length with optional suffix."""
        if len(text) <= max_length:
            return text
        
        available = max_length - len(suffix)
        if available < 0:
            return text[:max_length]
        
        return text[:available] + suffix
    
    def _sanitize_markdown(self, text: str) -> str:
        """Sanitize text for safe markdown rendering."""
        # Basic escaping of problematic characters
        replacements = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text
