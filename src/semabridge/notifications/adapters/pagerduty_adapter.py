"""
PagerDuty Events API v2 adapter.
"""

import asyncio
import logging
import time
from typing import Dict, Any

import aiohttp

from ..models import NotificationEvent
from .base import BaseAdapter, AdapterSendError

logger = logging.getLogger(__name__)

# Connection pool for efficiency
PAGERDUTY_CONNECTOR = None


async def get_pagerduty_connector() -> aiohttp.TCPConnector:
    """Get or create a connection pool for PagerDuty."""
    global PAGERDUTY_CONNECTOR
    if PAGERDUTY_CONNECTOR is None:
        PAGERDUTY_CONNECTOR = aiohttp.TCPConnector(limit=10, limit_per_host=5)
    return PAGERDUTY_CONNECTOR


class PagerDutyAdapter(BaseAdapter):
    """
    Send incidents to PagerDuty via Events API v2.
    
    Triggers incidents based on severity and resolves them when conditions clear.
    """
    
    PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
    TIMEOUT_SECONDS = 10
    
    async def send(
        self,
        payload: Dict[str, Any],
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send event to PagerDuty.
        
        Args:
            payload: PagerDuty API payload from formatter
            channel_config: Channel configuration (contains routing_key)
        
        Returns:
            Result dict with success, response_code, duration_ms, etc.
        """
        routing_key = channel_config.get("routing_key", "")
        if not routing_key:
            return {
                "success": False,
                "error": "Missing routing_key in channel configuration",
                "duration_ms": 0,
            }
        
        # Add routing key to payload
        payload_with_key = {
            **payload,
            "routing_key": routing_key,
        }
        
        start_time = time.time()
        
        try:
            connector = await get_pagerduty_connector()
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    self.PAGERDUTY_EVENTS_URL,
                    json=payload_with_key,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
                ) as response:
                    duration_ms = self._measure_duration(start_time)
                    response_body = await response.text()
                    
                    if response.status in (200, 201, 202):
                        return {
                            "success": True,
                            "response_code": response.status,
                            "response_body": response_body[:1000],  # Truncate for logging
                            "duration_ms": duration_ms,
                        }
                    else:
                        return {
                            "success": False,
                            "response_code": response.status,
                            "response_body": response_body[:1000],
                            "duration_ms": duration_ms,
                            "error": f"HTTP {response.status}",
                        }
        
        except asyncio.TimeoutError:
            duration_ms = self._measure_duration(start_time)
            return {
                "success": False,
                "duration_ms": duration_ms,
                "error": f"Timeout after {self.TIMEOUT_SECONDS}s",
            }
        
        except Exception as e:
            duration_ms = self._measure_duration(start_time)
            logger.error(f"PagerDuty send error: {e}", exc_info=True)
            return {
                "success": False,
                "duration_ms": duration_ms,
                "error": str(e),
            }
    
    async def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate PagerDuty channel configuration.
        
        Args:
            config: Configuration to validate
        
        Returns:
            (is_valid, error_message)
        """
        routing_key = config.get("routing_key", "")
        if not routing_key:
            return False, "Missing required field: routing_key"
        
        # Routing key should be a UUID-like string (not strict validation, just presence)
        if len(routing_key) < 20:
            return False, "Invalid routing_key: too short"
        
        return True, ""
