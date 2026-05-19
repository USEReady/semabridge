"""Adapters for various notification channels."""

from .base import BaseAdapter, AdapterError, AdapterSendError, AdapterConfigError
from .slack_adapter import SlackAdapter
from .email_adapter import EmailAdapter
from .webhook_adapter import WebhookAdapter
from .teams_adapter import TeamsAdapter
from .pagerduty_adapter import PagerDutyAdapter
from .snowflake_adapter import SnowflakeAdapter

__all__ = [
    "BaseAdapter",
    "AdapterError",
    "AdapterSendError",
    "AdapterConfigError",
    "SlackAdapter",
    "EmailAdapter",
    "WebhookAdapter",
    "TeamsAdapter",
    "PagerDutyAdapter",
    "SnowflakeAdapter",
]

