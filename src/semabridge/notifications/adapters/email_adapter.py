"""
Email adapter using aiosmtplib for async SMTP.
"""

import logging
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Any

import aiosmtplib
import bleach

from ..utils.validators import validate_email_address, validate_smtp_config
from .base import BaseAdapter, AdapterSendError

logger = logging.getLogger(__name__)


class EmailAdapter(BaseAdapter):
    """
    Send emails via SMTP (async).
    """
    
    TIMEOUT_SECONDS = 30
    
    async def send(
        self,
        payload: Dict[str, Any],
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send email via SMTP.
        
        Args:
            payload: Email payload from formatter
            channel_config: SMTP configuration
        
        Returns:
            Result dict
        """
        # Validate config
        is_valid, error = await self.validate_config(channel_config)
        if not is_valid:
            return {
                "success": False,
                "error": f"Invalid SMTP config: {error}",
            }
        
        start_time = time.time()
        
        try:
            # Sanitize HTML
            html_content = payload.get("html", "")
            sanitized_html = bleach.clean(
                html_content,
                tags=["p", "br", "div", "span", "h1", "h2", "h3", "strong", "b", "i", "em", "ul", "ol", "li", "table", "tr", "td", "th"],
                strip=True
            )
            
            # Build MIME message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = payload.get("subject", "SemaBridge Notification")
            msg["From"] = payload.get("from_address", "")
            msg["To"] = ", ".join(payload.get("to_addresses", []))
            
            # Attach plaintext and HTML parts
            plaintext_part = MIMEText(payload.get("plaintext", ""), "plain")
            html_part = MIMEText(sanitized_html, "html")
            
            msg.attach(plaintext_part)
            msg.attach(html_part)
            
            # Connect and send
            async with aiosmtplib.SMTP(
                hostname=channel_config.get("host"),
                port=int(channel_config.get("port", 587)),
                timeout=self.TIMEOUT_SECONDS,
            ) as smtp:
                if channel_config.get("use_tls", True):
                    await smtp.starttls()
                
                await smtp.login(
                    channel_config.get("username"),
                    channel_config.get("password")
                )
                
                await smtp.send_message(msg)
            
            duration_ms = self._measure_duration(start_time)
            return {
                "success": True,
                "response_code": 250,  # SMTP success code
                "duration_ms": duration_ms,
            }
        
        except TimeoutError:
            duration_ms = self._measure_duration(start_time)
            return {
                "success": False,
                "duration_ms": duration_ms,
                "error": f"SMTP timeout after {self.TIMEOUT_SECONDS}s",
            }
        
        except Exception as e:
            duration_ms = self._measure_duration(start_time)
            logger.error(f"Failed to send email: {e}")
            return {
                "success": False,
                "duration_ms": duration_ms,
                "error": str(e),
            }
    
    async def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate SMTP configuration.
        
        Args:
            config: Configuration dict
        
        Returns:
            (is_valid, error_message)
        """
        return validate_smtp_config(config)
    
    async def test_connection(self, config: Dict[str, Any] = None) -> tuple[bool, str]:
        """
        Test SMTP connection.
        
        Args:
            config: Configuration to test
        
        Returns:
            (is_connected, error_message)
        """
        if config is None:
            config = self.config
        
        is_valid, error = await self.validate_config(config)
        if not is_valid:
            return False, error
        
        try:
            async with aiosmtplib.SMTP(
                hostname=config.get("host"),
                port=int(config.get("port", 587)),
                timeout=10,
            ) as smtp:
                if config.get("use_tls", True):
                    await smtp.starttls()
                
                await smtp.login(
                    config.get("username"),
                    config.get("password")
                )
                
                # Successfully connected and authenticated
                return True, ""
        
        except Exception as e:
            logger.error(f"SMTP connection test failed: {e}")
            return False, str(e)
