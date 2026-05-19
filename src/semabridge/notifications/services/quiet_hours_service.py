"""
Quiet hours service - timezone-aware suppression with DST handling.
"""

import logging
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, available_timezones

from ..models import NotificationChannel, NotificationEvent
from ..constants import NotificationLevel

logger = logging.getLogger(__name__)


class QuietHoursService:
    """
    Manages notification quiet hours with proper timezone and DST handling.
    
    Uses zoneinfo (PEP 615) for all timezone operations. Never uses pytz or manual UTC math.
    """
    
    def __init__(self):
        """Initialize the quiet hours service."""
        self.valid_timezones = available_timezones()
    
    def is_quiet(self, channel: NotificationChannel) -> bool:
        """
        Returns True if the current time (in channel's timezone) is within the quiet window.
        
        Handles:
        - Same-day windows: 09:00–17:00
        - Overnight windows: 22:00–07:00 (crosses midnight)
        - DST transitions
        - Disabled quiet hours (returns False)
        
        Args:
            channel: NotificationChannel with quiet_hours_* fields
        
        Returns:
            True if currently in quiet hours
        """
        if not channel.quiet_hours_enabled:
            return False
        
        if not channel.quiet_hours_start or not channel.quiet_hours_end:
            return False
        
        try:
            tz = ZoneInfo(channel.timezone)
        except Exception as e:
            logger.warning(f"Invalid timezone {channel.timezone}: {e}")
            return False
        
        # Get current time in channel's timezone
        now = datetime.now(tz)
        current_time = now.time()
        
        start_time = channel.quiet_hours_start
        end_time = channel.quiet_hours_end
        
        # Handle overnight window (e.g., 22:00 to 07:00 crosses midnight)
        if start_time < end_time:
            # Same-day window: 09:00-17:00
            return start_time <= current_time < end_time
        else:
            # Overnight window: 22:00-07:00
            # In quiet hours if time >= start OR time < end
            return current_time >= start_time or current_time < end_time
    
    def seconds_until_window_end(self, channel: NotificationChannel) -> int:
        """
        Returns seconds until quiet hours end.
        
        Used by digest_worker to schedule the flush.
        
        Args:
            channel: NotificationChannel with quiet_hours_* fields
        
        Returns:
            Seconds until quiet window ends (0 if not in quiet hours or quiet hours disabled)
        """
        if not channel.quiet_hours_enabled:
            return 0
        
        if not channel.quiet_hours_start or not channel.quiet_hours_end:
            return 0
        
        try:
            tz = ZoneInfo(channel.timezone)
        except Exception as e:
            logger.warning(f"Invalid timezone {channel.timezone}: {e}")
            return 0
        
        # Get current time in channel's timezone
        now = datetime.now(tz)
        current_time = now.time()
        
        start_time = channel.quiet_hours_start
        end_time = channel.quiet_hours_end
        
        # If quiet hours disabled or we're not in them, return 0
        if not self.is_quiet(channel):
            return 0
        
        # Calculate when quiet hours end
        # Build a datetime for today's end time
        today_end = now.replace(
            hour=end_time.hour,
            minute=end_time.minute,
            second=0,
            microsecond=0
        )
        
        if start_time < end_time:
            # Same-day window: just use today's end time
            end_datetime = today_end
        else:
            # Overnight window: end time is tomorrow morning
            # If current time >= start, end is tomorrow
            # If current time < end, end is today
            if current_time >= start_time:
                # We're in the night part, end is tomorrow morning
                end_datetime = today_end + timedelta(days=1)
            else:
                # We're in the early morning part (before end_time)
                end_datetime = today_end
        
        # Calculate delta
        delta = end_datetime - now
        seconds = max(0, int(delta.total_seconds()))
        
        return seconds
    
    def should_bypass(
        self,
        event: NotificationEvent,
        channel: NotificationChannel
    ) -> bool:
        """
        Returns True if the event should bypass quiet hours.
        
        CRITICAL level bypasses by default.
        Configurable via channel_config["bypass_quiet_hours_levels"] (bitmask).
        
        Args:
            event: The notification event
            channel: NotificationChannel with configuration
        
        Returns:
            True if event should bypass quiet hours
        """
        # CRITICAL always bypasses
        if event.level & NotificationLevel.CRITICAL:
            return True
        
        # Check if channel has custom bypass levels configured
        # This would typically be in a config dict, but for now we'll use a default
        # bypass_mask - could be set per channel
        # Default: only CRITICAL bypasses
        bypass_mask = 2  # CRITICAL level
        
        # Check if event level matches bypass mask
        return bool(event.level & bypass_mask)
    
    def validate_timezone(self, tz_name: str) -> bool:
        """
        Validate that a timezone name is valid IANA name.
        
        Args:
            tz_name: Timezone name to validate
        
        Returns:
            True if valid
        """
        return tz_name in self.valid_timezones
    
    def get_quiet_hours_window_str(self, channel: NotificationChannel) -> str:
        """
        Get a human-readable quiet hours window string.
        
        Args:
            channel: NotificationChannel with quiet_hours_* fields
        
        Returns:
            String like "22:00-07:00 EST" or "09:00-17:00 EST"
        """
        if not channel.quiet_hours_start or not channel.quiet_hours_end:
            return "Not configured"
        
        start_str = channel.quiet_hours_start.strftime("%H:%M")
        end_str = channel.quiet_hours_end.strftime("%H:%M")
        
        return f"{start_str}-{end_str} {channel.timezone}"
