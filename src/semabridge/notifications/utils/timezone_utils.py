"""
Timezone utilities for handling quiet hours and digest timing.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, available_timezones
from typing import Tuple, Optional


def is_in_quiet_hours(
    current_time: Optional[datetime] = None,
    start_time: time = None,
    end_time: time = None,
    timezone: str = "UTC",
) -> bool:
    """
    Check if current time is within quiet hours in a specific timezone.
    
    Handles overnight windows correctly (e.g., 22:00 → 07:00 crosses midnight).
    
    Args:
        current_time: Datetime to check (defaults to now)
        start_time: Quiet hours start time (HH:MM)
        end_time: Quiet hours end time (HH:MM)
        timezone: IANA timezone name (e.g., "America/New_York")
    
    Returns:
        True if current_time is within quiet hours
    """
    if start_time is None or end_time is None:
        return False
    
    if current_time is None:
        current_time = datetime.now()
    
    # Convert to target timezone
    try:
        tz = ZoneInfo(timezone)
    except (KeyError, ValueError):
        # Invalid timezone, use UTC
        tz = ZoneInfo("UTC")
    
    # Make current_time timezone-aware if needed
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=tz)
    else:
        current_time = current_time.astimezone(tz)
    
    current_time_obj = current_time.time()
    
    # Handle overnight windows (e.g., 22:00 → 07:00)
    if start_time < end_time:
        # Normal case: within a single day
        return start_time <= current_time_obj < end_time
    else:
        # Overnight case: either >= start_time OR < end_time
        return current_time_obj >= start_time or current_time_obj < end_time


def get_quiet_hours_end_time(
    current_time: Optional[datetime] = None,
    start_time: time = None,
    end_time: time = None,
    timezone: str = "UTC",
) -> Optional[datetime]:
    """
    Get the datetime when quiet hours end.
    
    Args:
        current_time: Reference time (defaults to now)
        start_time: Quiet hours start time
        end_time: Quiet hours end time
        timezone: IANA timezone name
    
    Returns:
        Datetime when quiet hours end, or None if not in quiet hours
    """
    if start_time is None or end_time is None:
        return None
    
    if current_time is None:
        current_time = datetime.now()
    
    try:
        tz = ZoneInfo(timezone)
    except (KeyError, ValueError):
        tz = ZoneInfo("UTC")
    
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=tz)
    else:
        current_time = current_time.astimezone(tz)
    
    # Build end_time datetime for today
    end_datetime = current_time.replace(
        hour=end_time.hour,
        minute=end_time.minute,
        second=0,
        microsecond=0
    )
    
    if start_time < end_time:
        # Normal case: if we're in quiet hours, return today's end time
        # if after end time, return tomorrow's end time
        if current_time.time() >= start_time and current_time.time() < end_time:
            return end_datetime
        elif current_time.time() < start_time:
            # Quiet hours haven't started yet
            return None
        else:
            # After quiet hours, next one is tomorrow
            return end_datetime + timedelta(days=1)
    else:
        # Overnight case
        if current_time.time() >= start_time:
            # We're in the "night" part, end is tomorrow morning
            return end_datetime + timedelta(days=1)
        elif current_time.time() < end_time:
            # We're in the morning part before end_time
            return end_datetime
        else:
            # We're between end_time and start_time (not in quiet hours)
            return None


def validate_timezone(tz_name: str) -> Tuple[bool, str]:
    """
    Validate timezone name.
    
    Returns:
        (is_valid, error_message)
    """
    if tz_name == "UTC":
        return True, ""
    
    try:
        ZoneInfo(tz_name)
        return True, ""
    except (KeyError, ValueError):
        available = sorted(list(available_timezones())[:10])
        return False, f"Invalid timezone: {tz_name}. Examples: {', '.join(available)}"


def convert_to_timezone(
    dt: datetime,
    timezone: str = "UTC"
) -> datetime:
    """Convert datetime to specific timezone."""
    try:
        tz = ZoneInfo(timezone)
    except (KeyError, ValueError):
        tz = ZoneInfo("UTC")
    
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    
    return dt.astimezone(tz)


def format_quiet_hours_window(
    start_time: time,
    end_time: time,
    timezone: str = "UTC",
) -> str:
    """Format quiet hours for display."""
    start_str = start_time.strftime("%H:%M")
    end_str = end_time.strftime("%H:%M")
    
    if start_time < end_time:
        return f"{start_str} – {end_str} {timezone}"
    else:
        return f"{start_str} – {end_str} (next day) {timezone}"
