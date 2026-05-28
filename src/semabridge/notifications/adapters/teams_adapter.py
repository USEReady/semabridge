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


class TeamsAdapter(BaseAdapter):
    """
    Send notifications to Microsoft Teams via Adaptive Cards or Message Cards.
    Supports legacy webhooks and modern Power Automate / Logic Apps workflow webhooks
    via multi-stage fallback payloads.
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
        Send to Teams webhook using modern sequential fallback strategy (STEPS 3 & 10).
        
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
        
        # STEP 2 — ADD RAW URL DEBUG LOGGING
        logger.warning({
            "event": "teams_raw_webhook_debug",
            "webhook_url": webhook_url,
        })
        
        # STEP 3 — VERIFY QUERY PARAMETERS ARE PRESENT
        # Verify raw query parameters are present in database-retrieved string
        assert "api-version=" in webhook_url, "Missing required api-version in Teams webhook URL query parameters"
        
        from urllib.parse import urlparse
        parsed_url = urlparse(webhook_url)
        query_keys = []
        if parsed_url.query:
            try:
                for q in parsed_url.query.split("&"):
                    if "=" in q:
                        query_keys.append(q.split("=", 1)[0])
            except Exception:
                pass
                
        logger.info({
            "event": "teams_url_diagnostics",
            "webhook_length": len(webhook_url),
            "hostname": parsed_url.hostname or "unknown",
            "query_keys": query_keys,
        })
        
        # Reconstruct fallback payloads for schema hardening (STEP 10)
        # Extract plaintext fallback string
        plaintext_content = ""
        fallback_attachments = payload.get("plaintext_fallback", {}).get("attachments", [])
        if fallback_attachments and isinstance(fallback_attachments, list):
            plaintext_content = fallback_attachments[0].get("content", "")
        if not plaintext_content:
            plaintext_content = "SemaBridge Notification"
            
        # 1. Level 1: Universal simple text (STEP 5 & 6)
        simple_text = {
            "text": f"✅ **SemaBridge Notification**\n\n{plaintext_content}"
        }
        
        # 2. Level 2: MessageCard
        message_card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": "SemaBridge Notification",
            "themeColor": "36A64F",
            "title": payload.get("attachments", [{}])[0].get("content", {}).get("body", [{}])[0].get("text", "SemaBridge Notification") if payload.get("attachments") else "SemaBridge Notification",
            "text": plaintext_content.replace("\n", "\n\n") # Markdown newline conversion
        }
        
        # 3. Level 3: Root-level Adaptive Card (modern workflows)
        root_card = None
        attachments = payload.get("attachments", [])
        if attachments and isinstance(attachments, list):
            root_card = attachments[0].get("content")
            
        # 4. Level 4: Standard legacy attachment Adaptive Card
        legacy_card = payload
        
        # Sequential try loop progressive order: simple text -> MessageCard -> Adaptive Card (STEP 5 & 6)
        attempts = [
            ("Universal Plaintext Schema", simple_text),
            ("MessageCard Schema", message_card),
            ("Root-level Adaptive Card", root_card),
            ("Legacy Attachment Adaptive Card", legacy_card)
        ]
        
        last_error = "Failed to deliver payload"
        
        for name, payload_to_try in attempts:
            if not payload_to_try:
                continue
                
            logger.info({
                "event": "teams_delivery_attempt",
                "webhook_url": webhook_url,
                "schema": name,
            })
            
            try:
                # STEP 4 — VERIFY REQUEST CONTENT TYPE
                headers = {
                    "Content-Type": "application/json"
                }
                
                session = await self._get_session()
                # Pass webhook_url raw string directly to prevent any aiohttp/yarl query parameter reconstruction or decoding
                async with session.post(
                    webhook_url,
                    json=payload_to_try,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
                ) as response:
                    duration_ms = self._measure_duration(start_time)
                    response_body = await response.text()
                    
                    # STEP 4 — Structured validation / delivery response logging
                    logger.info({
                        "event": "teams_delivery_response",
                        "status": response.status,
                        "schema": name,
                        "response_body": response_body[:200],
                        "workflow_acceptance": "accepted" if response.status in (200, 201, 202) else "rejected",
                        "duration_ms": duration_ms
                    })
                    
                    if response.status in (200, 201, 202):
                        return {
                            "success": True,
                            "response_code": response.status,
                            "response_body": response_body,
                            "duration_ms": duration_ms,
                            "schema_used": name
                        }
                    else:
                        last_error = f"HTTP {response.status}: {response_body[:100]}"
            except Exception as e:
                logger.warning(f"Teams delivery failed with schema {name}: {e}")
                last_error = str(e)
                
        return {
            "success": False,
            "error": last_error,
            "duration_ms": self._measure_duration(start_time)
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

    async def test_connection(self, config: Dict[str, Any] = None) -> tuple[bool, str]:
        """
        Test Microsoft Teams webhook connection with logging.
        
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
        
        test_payload = {
            "text": "🧪 **SemaBridge Teams Notification Test**\n\nThis is a test message to verify Teams webhook connectivity."
        }
        
        start_time = time.time()
        logger.info({
            "event": "teams_test_connection_attempt",
            "webhook_url": webhook_url
        })
        try:
            headers = {
                "Content-Type": "application/json"
            }
            
            session = await self._get_session()
            # Pass webhook_url raw string directly to prevent any aiohttp/yarl query parameter reconstruction or decoding
            async with session.post(
                webhook_url,
                json=test_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                duration_ms = self._measure_duration(start_time)
                response_body = await response.text()
                
                logger.info({
                    "event": "teams_test_connection_response",
                    "status": response.status,
                    "response_body": response_body[:200],
                    "workflow_acceptance": "accepted" if response.status in (200, 201, 202) else "rejected",
                    "duration_ms": duration_ms
                })
                
                if response.status in (200, 201, 202):
                    return True, ""
                else:
                    return False, f"Teams returned {response.status}: {response_body[:100]}"
        except Exception as e:
            logger.error(f"Teams test connection error: {e}", exc_info=True)
            return False, str(e)
