"""
Microsoft Teams Adaptive Cards adapter.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any

import aiohttp

from ..models import NotificationEvent
from ..utils.validators import validate_webhook_url
from .base import BaseAdapter, AdapterSendError

logger = logging.getLogger(__name__)

# Connection pool for efficiency
TEAMS_CONNECTOR = None


async def get_teams_connector() -> aiohttp.TCPConnector:
    """Get or create a connection pool for Teams."""
    global TEAMS_CONNECTOR
    if TEAMS_CONNECTOR is None:
        TEAMS_CONNECTOR = aiohttp.TCPConnector(limit=10, limit_per_host=5)
    return TEAMS_CONNECTOR


class TeamsAdapter(BaseAdapter):
    """
    Send notifications to Microsoft Teams via Adaptive Cards.
    Falls back to plaintext POST if Adaptive Card validation fails.
    """
    
    TIMEOUT_SECONDS = 10
    MAX_RETRIES_FOR_429 = 3
    
    async def send(
        self,
        payload: Dict[str, Any],
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send to Teams webhook.
        
        Args:
            payload: Teams Adaptive Card payload from formatter
            channel_config: Channel configuration (contains webhook URL)
        
        Returns:
            Result dict with success, response_code, duration_ms, etc.
        """
        webhook_url = channel_config.get("webhook_url", "")
        if not webhook_url:
            return {
                "success": False,
                "error": "Missing webhook_url in channel configuration",
                "duration_ms": 0,
            }
        
        start_time = time.time()
        attempt = 0
        retry_delay = 1
        
        # Try Adaptive Card format first
        result = await self._send_adaptive_card(webhook_url, payload, attempt, retry_delay, start_time)
        
        if result["success"]:
            return result
        
        # Fall back to plaintext if Adaptive Card fails
        logger.warning(f"Adaptive Card send failed, falling back to plaintext for {webhook_url}")
        plaintext_payload = payload.get("plaintext_fallback", {})
        if plaintext_payload:
            fallback_result = await self._send_plaintext(webhook_url, plaintext_payload, start_time)
            return fallback_result
        
        return result
    
    async def _send_adaptive_card(
        self,
        webhook_url: str,
        payload: Dict[str, Any],
        attempt: int,
        retry_delay: int,
        start_time: float
    ) -> Dict[str, Any]:
        """
        Send Adaptive Card format to Teams.
        
        Args:
            webhook_url: Teams webhook URL
            payload: Adaptive Card payload
            attempt: Current attempt number
            retry_delay: Delay for next retry
            start_time: When the send started
        
        Returns:
            Result dict
        """
        max_retries = self.MAX_RETRIES_FOR_429
        
        while attempt < max_retries:
            try:
                connector = await get_teams_connector()
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.post(
                        webhook_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
                    ) as response:
                        duration_ms = self._measure_duration(start_time)
                        response_body = await response.text()
                        
                        if response.status == 429:
                            # Rate limited
                            retry_after = response.headers.get("Retry-After", str(retry_delay))
                            try:
                                retry_delay = int(retry_after)
                            except ValueError:
                                pass
                            
                            attempt += 1
                            if attempt < max_retries:
                                logger.warning(f"Teams rate limited, retrying in {retry_delay}s")
                                await asyncio.sleep(retry_delay)
                                retry_delay = min(retry_delay * 2, 60)  # Exponential backoff up to 60s
                                continue
                            
                            return {
                                "success": False,
                                "response_code": 429,
                                "response_body": response_body,
                                "duration_ms": duration_ms,
                                "error": f"Rate limited after {attempt} retries",
                            }
                        
                        elif response.status in (200, 201):
                            return {
                                "success": True,
                                "response_code": response.status,
                                "response_body": response_body,
                                "duration_ms": duration_ms,
                            }
                        
                        else:
                            # Other error (including card validation failure)
                            return {
                                "success": False,
                                "response_code": response.status,
                                "response_body": response_body,
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
                logger.error(f"Teams send error: {e}", exc_info=True)
                return {
                    "success": False,
                    "duration_ms": duration_ms,
                    "error": str(e),
                }
    
    async def _send_plaintext(
        self,
        webhook_url: str,
        payload: Dict[str, Any],
        start_time: float
    ) -> Dict[str, Any]:
        """
        Send plaintext message to Teams (fallback).
        
        Args:
            webhook_url: Teams webhook URL
            payload: Plaintext payload
            start_time: When the send started
        
        Returns:
            Result dict
        """
        try:
            connector = await get_teams_connector()
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
                ) as response:
                    duration_ms = self._measure_duration(start_time)
                    response_body = await response.text()
                    
                    if response.status in (200, 201):
                        return {
                            "success": True,
                            "response_code": response.status,
                            "response_body": response_body,
                            "duration_ms": duration_ms,
                        }
                    else:
                        return {
                            "success": False,
                            "response_code": response.status,
                            "response_body": response_body,
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
            logger.error(f"Teams plaintext fallback error: {e}", exc_info=True)
            return {
                "success": False,
                "duration_ms": duration_ms,
                "error": str(e),
            }
    
    async def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate Teams channel configuration.
        
        Args:
            config: Configuration to validate
        
        Returns:
            (is_valid, error_message)
        """
        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            return False, "Missing required field: webhook_url"
        
        # Validate webhook URL (SSRF protection)
        is_valid, error = validate_webhook_url(webhook_url)
        if not is_valid:
            return False, f"Invalid webhook_url: {error}"
        
        return True, ""
