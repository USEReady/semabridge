"""
SQLAlchemy models for notification channels, logs, and deduplication.
"""

from datetime import datetime, time
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    UUID,
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    Time,
    Text,
    ForeignKey,
    Enum,
    JSON,
    Index,
)
from sqlalchemy.orm import relationship
import enum

from semabridge.repository.orm.base import Base


class ChannelTypeEnum(str, enum.Enum):
    """Channel type enumeration."""
    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"
    WEBHOOK = "webhook"
    PAGERDUTY = "pagerduty"
    SNOWFLAKE = "snowflake"


class ChannelStatusEnum(str, enum.Enum):
    """Channel status enumeration."""
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


class DeliveryStatusEnum(str, enum.Enum):
    """Delivery status enumeration."""
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"
    SKIPPED_DEDUPE = "skipped_dedupe"
    SKIPPED_QUIET = "skipped_quiet"


class NotificationChannel(Base):
    """
    Notification channel configuration.
    config_json stores secrets encrypted at rest.
    """
    __tablename__ = "notification_channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False, index=True)
    channel_type = Column(Enum(ChannelTypeEnum), nullable=False, index=True)
    enabled = Column(Boolean, default=True, nullable=False)
    config_json = Column(Text, nullable=False)  # Encrypted JSON: webhook URLs, keys, creds
    level_mask = Column(Integer, default=63, nullable=False)  # Bitmask of notification levels
    project_scope = Column(String(255), nullable=True)  # None = all projects
    quiet_hours_enabled = Column(Boolean, default=False, nullable=False)
    quiet_hours_start = Column(Time, nullable=True)  # HH:MM
    quiet_hours_end = Column(Time, nullable=True)  # HH:MM
    timezone = Column(String(63), default="UTC", nullable=False)  # IANA name
    digest_enabled = Column(Boolean, default=False, nullable=False)
    status = Column(Enum(ChannelStatusEnum), default=ChannelStatusEnum.ACTIVE, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    logs = relationship("NotificationLog", back_populates="channel", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_channel_type_enabled", "channel_type", "enabled"),
        Index("idx_channel_project_scope", "project_scope"),
    )


class NotificationLog(Base):
    """
    Delivery attempt log for auditing and retry.
    """
    __tablename__ = "notification_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("notification_channels.id"), nullable=False, index=True)
    status = Column(Enum(DeliveryStatusEnum), nullable=False, index=True)
    attempt = Column(Integer, default=1, nullable=False)
    response_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    channel = relationship("NotificationChannel", back_populates="logs")

    __table_args__ = (
        Index("idx_log_event_channel", "event_id", "channel_id"),
        Index("idx_log_status_created", "status", "created_at"),
    )


class NotificationDedupe(Base):
    """
    Deduplication fingerprints with TTL.
    fingerprint is the primary key (sha256 hash).
    """
    __tablename__ = "notification_dedupes"

    fingerprint = Column(String(64), primary_key=True)  # SHA256 hex
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class NotificationTemplate(Base):
    """Per-channel notification title/body template."""
    __tablename__ = "notification_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False, index=True)
    level_mask = Column(Integer, nullable=False)
    title_template = Column(String(4000), nullable=False)
    body_template = Column(String(8000), nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_template_channel_level", "channel_id", "level_mask"),
        Index("idx_template_channel_is_default", "channel_id", "is_default"),
    )


class NotificationRoutingRuleRow(Base):
    """Persisted rule for routing notifications to channels."""
    __tablename__ = "notification_routing_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False)
    priority = Column(Integer, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    conditions = Column(JSON, nullable=False)
    channel_ids = Column(JSON, nullable=False)
    stop_on_match = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_routing_priority", "priority"),
        Index("idx_routing_enabled", "enabled"),
        Index("idx_routing_enabled_priority", "enabled", "priority"),
    )
