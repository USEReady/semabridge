"""
Generic webhook adapter with SSRF protection.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any

import aiohttp

from ..utils.validators import validate_webhook_url
from .base import BaseAdapter

logger = logging.getLogger(__name__)

# Connection pool
WEBHOOK_CONNECTOR = None


async def get_webhook_connector() -> aiohttp.TCPConnector:
    """Get or create a connection pool for webhooks."""
    global WEBHOOK_CONNECTOR
    if WEBHOOK_CONNECTOR is None:
        WEBHOOK_CONNECTOR = aiohttp.TCPConnector(limit=20, limit_per_host=5)
    return WEBHOOK_CONNECTOR


class WebhookAdapter(BaseAdapter):
    """
    Send notifications to generic webhooks with SSRF protection.
    """
    
    TIMEOUT_SECONDS = 10
    
    async def send(
        self,
        payload: Dict[str, Any],
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        POST JSON payload to webhook URL.
        
        Args:
            payload: JSON payload from formatter
            channel_config: Webhook configuration (contains URL)
        
        Returns:
            Result dict
        """
        webhook_url = channel_config.get("webhook_url", "")
        if not webhook_url:
            return {
                "success": False,
                "error": "Missing webhook_url in channel configuration",
            }
        
        # SSRF validation at send time
        is_valid, error = validate_webhook_url(webhook_url)
        if not is_valid:
            return {
                "success": False,
                "error": f"Webhook URL validation failed: {error}",
            }
        
        start_time = time.time()
        
        try:
            connector = await get_webhook_connector()
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
                ) as response:
                    duration_ms = self._measure_duration(start_time)
                    response_body = await response.text()
                    
                    # Accept 2xx responses
                    if 200 <= response.status < 300:
                        return {
                            "success": True,
                            "response_code": response.status,
                            "response_body": response_body[:1000],  # Truncate
                            "duration_ms": duration_ms,
                        }
                    else:
                        return {
                            "success": False,
                            "response_code": response.status,
                            "response_body": response_body[:1000],
                            "duration_ms": duration_ms,
                            "error": f"Webhook returned {response.status}",
                        }
        
        except asyncio.TimeoutError:
            duration_ms = self._measure_duration(start_time)
            return {
                "success": False,
                "duration_ms": duration_ms,
                "error": f"Request timeout after {self.TIMEOUT_SECONDS}s",
            }
        
        except Exception as e:
            duration_ms = self._measure_duration(start_time)
            logger.error(f"Failed to send webhook: {e}")
            return {
                "success": False,
                "duration_ms": duration_ms,
                "error": str(e),
            }
    
    async def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate webhook configuration.
        
        Args:
            config: Configuration dict
        
        Returns:
            (is_valid, error_message)
        """
        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            return False, "webhook_url is required"
        
        is_valid, error = validate_webhook_url(webhook_url)
        if not is_valid:
            return False, error
        
        return True, ""
    
    async def test_connection(self, config: Dict[str, Any] = None) -> tuple[bool, str]:
        """
        Test webhook connectivity.
        
        Args:
            config: Configuration to test
        
        Returns:
            (is_connected, error_message)
        """
        if config is None:
            config = self.config
        
        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            return False, "webhook_url is required"
        
        # Test payload
        test_payload = {
            "test": True,
            "message": "SemaBridge webhook connectivity test",
        }
        
        try:
            connector = await get_webhook_connector()
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    webhook_url,
                    json=test_payload,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if 200 <= response.status < 300:
                        return True, ""
                    else:
                        return False, f"Webhook returned {response.status}"
        except Exception as e:
            return False, str(e)
