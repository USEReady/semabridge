"""
Routing rule models.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class RoutingConditions:
    """
    Conditions for routing rule matching.
    All fields are optional and ANDed together.
    """
    level_mask: Optional[int] = None
    project_ids: Optional[List[str]] = field(default_factory=list)
    source_pattern: Optional[str] = None  # fnmatch pattern
    title_contains: Optional[str] = None
    payload_key_exists: Optional[str] = None
    payload_value_matches: Optional[Dict[str, Any]] = None


@dataclass
class NotificationRoutingRule:
    """
    Rule for routing notifications to channels.
    """
    id: str
    name: str
    priority: int  # Lower = higher priority
    conditions: RoutingConditions
    channel_ids: List[str] = field(default_factory=list)
    stop_on_match: bool = False  # Stop evaluating rules after this matches
    enabled: bool = True
    
    def matches(self, event: Any) -> bool:
        """
        Check if this rule matches an event.
        
        Args:
            event: NotificationEvent to check
        
        Returns:
            True if all conditions match
        """
        from fnmatch import fnmatch
        
        # Check level mask
        if self.conditions.level_mask is not None:
            if not (event.level & self.conditions.level_mask):
                return False
        
        # Check project IDs
        if self.conditions.project_ids:
            if event.project_id not in self.conditions.project_ids:
                return False
        
        # Check source pattern
        if self.conditions.source_pattern:
            if not fnmatch(event.source, self.conditions.source_pattern):
                return False
        
        # Check title contains
        if self.conditions.title_contains:
            if self.conditions.title_contains.lower() not in event.title.lower():
                return False
        
        # Check payload key exists
        if self.conditions.payload_key_exists:
            if self.conditions.payload_key_exists not in (event.payload or {}):
                return False
        
        # Check payload value matches
        if self.conditions.payload_value_matches:
            key = self.conditions.payload_value_matches.get("key")
            value = self.conditions.payload_value_matches.get("value")
            if key and event.payload and event.payload.get(key) != value:
                return False
        
        return True
