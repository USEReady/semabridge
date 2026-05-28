"""
Slack incoming webhook adapter.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any

import aiohttp

from ..models import NotificationEvent
from ..utils.validators import validate_slack_webhook
from .base import BaseAdapter, AdapterSendError

logger = logging.getLogger(__name__)

# Connection pool functions removed to prevent stale global connector issues.


class SlackAdapter(BaseAdapter):
    """
    Send notifications to Slack via incoming webhooks.
    """
    
    TIMEOUT_SECONDS = 10
    MAX_RETRIES_FOR_429 = 3

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
        Send to Slack webhook.
        
        Args:
            payload: Slack Block Kit payload from formatter
            channel_config: Channel configuration (contains webhook URL)
        
        Returns:
            Result dict with success, response_code, duration_ms, etc.
        """
        webhook_url = channel_config.get("webhook_url", "")
        if not webhook_url:
            return {
                "success": False,
                "error": "Missing webhook_url in channel configuration",
            }
        
        start_time = time.time()
        attempt = 0
        retry_delay = 1
        
        while attempt < self.MAX_RETRIES_FOR_429:
            try:
                session = await self._get_session()
                # Temporarily log rendered payload size and top-level structure (Step 4)
                print(f"SLACK_VERIFY_PAYLOAD: keys={list(payload.keys())}, has_blocks={'blocks' in payload}")
                logger.info({
                    "payload_keys": list(payload.keys()),
                    "has_blocks": "blocks" in payload,
                })

                # Robust logging before request
                print(f"SLACK_VERIFY_SEND: sending Slack notification to {webhook_url[:30]}...")
                logger.info(
                    "Sending Slack notification",
                    extra={
                        "session_closed": session.closed,
                        "webhook_url_present": bool(webhook_url),
                    }
                )
                async with session.post(
                    webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
                ) as response:
                    duration_ms = self._measure_duration(start_time)
                    response_body = await response.text()
                    
                    # Robust logging after response (Step 1)
                    from urllib.parse import urlparse
                    parsed_url = urlparse(webhook_url)
                    webhook_hostname = parsed_url.hostname or "unknown"
                    
                    print(f"SLACK_VERIFY_STATUS: status={response.status}, success={response.status == 200}, hostname={webhook_hostname}")
                    print(f"SLACK_VERIFY_BODY: {response_body}")
                    
                    logger.info({
                        "slack_delivery_status": response.status,
                        "slack_delivery_success": response.status == 200,
                        "slack_webhook_hostname": webhook_hostname,
                    })
                    
                    logger.info({
                        "slack_response_body": response_body,
                    })
                    
                    if response.status == 429:
                        # Rate limited
                        retry_after = response.headers.get("Retry-After", str(retry_delay))
                        try:
                            retry_delay = int(retry_after)
                        except ValueError:
                            pass
                        
                        attempt += 1
                        if attempt < self.MAX_RETRIES_FOR_429:
                            logger.warning(f"Slack rate limited, retrying in {retry_delay}s")
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
                    
                    elif response.status == 200:
                        print("SLACK_VERIFY_SUCCESS: Slack notification delivered successfully")
                        logger.info("Slack notification delivered successfully")
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
                            "error": f"Slack returned {response.status}",
                        }
            
            except asyncio.TimeoutError:
                duration_ms = self._measure_duration(start_time)
                print("SLACK_VERIFY_ERROR: Request timeout")
                return {
                    "success": False,
                    "duration_ms": duration_ms,
                    "error": f"Request timeout after {self.TIMEOUT_SECONDS}s",
                }
            
            except Exception as e:
                duration_ms = self._measure_duration(start_time)
                print(f"SLACK_VERIFY_ERROR: exception={e}")
                logger.exception("Slack delivery failure")
                return {
                    "success": False,
                    "duration_ms": duration_ms,
                    "error": str(e),
                }
        
        return {
            "success": False,
            "error": "Max retries exceeded",
        }
    
    async def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate Slack channel configuration.
        
        Args:
            config: Configuration dict
        
        Returns:
            (is_valid, error_message)
        """
        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            return False, "webhook_url is required"
        
        is_valid, error = validate_slack_webhook(webhook_url)
        if not is_valid:
            return False, error
        
        return True, ""
    
    async def test_connection(self, config: Dict[str, Any] = None) -> tuple[bool, str]:
        """
        Test Slack webhook connection.
        
        Args:
            config: Configuration to test (uses self.config if None)
        
        Returns:
            (is_connected, error_message)
        """
        if config is None:
            config = self.config
        
        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            return False, "webhook_url is required"
        
        # Send a simple test payload
        test_payload = {
            "text": "🧪 SemaBridge Notification Test",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*🧪 SemaBridge Notification Test*\n_This is a test message to verify webhook connectivity._"
                    }
                }
            ]
        }
        
        try:
            session = await self._get_session()
            async with session.post(
                webhook_url,
                json=test_payload,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    return True, ""
                else:
                    return False, f"Slack returned {response.status}"
        except Exception as e:
            return False, str(e)

