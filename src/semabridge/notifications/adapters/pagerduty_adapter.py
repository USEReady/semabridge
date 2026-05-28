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

# Connection pool functions removed to prevent stale global connector issues.


class PagerDutyAdapter(BaseAdapter):
    """
    Send incidents to PagerDuty via Events API v2.
    
    Triggers incidents based on severity and resolves them when conditions clear.
    """
    
    PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
    TIMEOUT_SECONDS = 10

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
    
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
            session = await self._get_session()
            # Robust logging before request
            logger.info(
                "Sending PagerDuty notification",
                extra={
                    "session_closed": session.closed,
                    "webhook_url_present": bool(routing_key),
                }
            )
            async with session.post(
                self.PAGERDUTY_EVENTS_URL,
                json=payload_with_key,
                timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
            ) as response:
                duration_ms = self._measure_duration(start_time)
                response_body = await response.text()
                
                # Robust logging after response
                logger.info(
                    "PagerDuty delivery response received",
                    extra={
                        "session_closed": session.closed,
                        "webhook_url_present": bool(routing_key),
                        "response_status": response.status,
                    }
                )
                
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

    async def test_connection(self, config: Dict[str, Any] = None) -> tuple[bool, str]:
        """
        Test PagerDuty webhook connection.
        
        Args:
            config: Configuration to test (uses self.config if None)
        
        Returns:
            (is_connected, error_message)
        """
        if config is None:
            config = self.config
        
        routing_key = config.get("routing_key", "")
        if not routing_key:
            return False, "routing_key is required"
        
        test_payload = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": "🧪 SemaBridge PagerDuty Connectivity Test",
                "source": "SemaBridge",
                "severity": "info",
                "custom_details": {
                    "message": "This is a test notification to verify PagerDuty connectivity."
                }
            }
        }
        
        try:
            session = await self._get_session()
            async with session.post(
                self.PAGERDUTY_EVENTS_URL,
                json=test_payload,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status in (200, 201, 202):
                    return True, ""
                else:
                    response_body = await response.text()
                    return False, f"PagerDuty returned {response.status}: {response_body[:200]}"
        except Exception as e:
            return False, str(e)

