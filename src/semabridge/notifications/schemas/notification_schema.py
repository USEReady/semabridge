"""
Pydantic schemas for notification API endpoints.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, time
from pydantic import BaseModel, Field, validator, EmailStr

from ..constants import NotificationChannelType, ChannelStatus


# ===== Request/Response Models =====

class NotificationChannelCreateRequest(BaseModel):
    """Create notification channel request."""
    name: str = Field(..., min_length=1, max_length=255)
    channel_type: str = Field(..., description="slack, teams, email, webhook, pagerduty")
    config_json: Dict[str, Any]
    level_mask: int = Field(default=63)
    enabled: bool = True
    project_scope: Optional[str] = None
    quiet_hours_enabled: bool = False
    quiet_hours_start: Optional[time] = None
    quiet_hours_end: Optional[time] = None
    timezone: str = "UTC"
    digest_enabled: bool = False
    
    @validator("channel_type")
    def validate_channel_type(cls, v):
        valid = [
            NotificationChannelType.SLACK,
            NotificationChannelType.TEAMS,
            NotificationChannelType.EMAIL,
            NotificationChannelType.WEBHOOK,
            NotificationChannelType.PAGERDUTY,
        ]
        if v not in valid:
            raise ValueError(f"Invalid channel_type: {v}")
        return v


class NotificationChannelUpdateRequest(BaseModel):
    """Update notification channel request."""
    name: Optional[str] = None
    enabled: Optional[bool] = None
    config_json: Optional[Dict[str, Any]] = None
    level_mask: Optional[int] = None
    project_scope: Optional[str] = None
    quiet_hours_enabled: Optional[bool] = None
    quiet_hours_start: Optional[time] = None
    quiet_hours_end: Optional[time] = None
    timezone: Optional[str] = None
    digest_enabled: Optional[bool] = None


class NotificationChannelResponse(BaseModel):
    """Notification channel response (secrets masked)."""
    id: str
    name: str
    channel_type: str
    enabled: bool
    config_json: Dict[str, Any]  # Masked
    level_mask: int
    project_scope: Optional[str]
    quiet_hours_enabled: bool
    quiet_hours_start: Optional[time]
    quiet_hours_end: Optional[time]
    timezone: str
    digest_enabled: bool
    status: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class NotificationLogResponse(BaseModel):
    """Notification delivery log response."""
    id: str
    event_id: str
    channel_id: str
    status: str
    attempt: int
    response_code: Optional[int]
    response_body: Optional[str]
    duration_ms: Optional[int]
    error_message: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class NotificationTestRequest(BaseModel):
    """Test notification request."""
    pass


class NotificationRetryRequest(BaseModel):
    """Retry failed notification request."""
    log_id: str


class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""
    items: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


# ===== Errors =====

class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: Optional[str] = None
    code: str = "UNKNOWN"


class ValidationErrorResponse(BaseModel):
    """Validation error response."""
    error: str
    fields: Dict[str, List[str]]
