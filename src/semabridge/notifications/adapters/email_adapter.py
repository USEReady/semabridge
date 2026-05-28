"""
Email adapter using aiosmtplib for async SMTP.

Compatible with aiosmtplib >= 5.x.
Credentials and TLS are passed at construction time — the library
handles STARTTLS negotiation and AUTH automatically.
"""

import logging
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Any, List

import aiosmtplib

from ..utils.validators import validate_email_address, validate_smtp_config
from .base import BaseAdapter, AdapterSendError

logger = logging.getLogger(__name__)


def _parse_recipients(raw) -> List[str]:
    """Safely parse comma-separated string or list into a flat list of addresses."""
    if isinstance(raw, str):
        return [e.strip() for e in raw.split(",") if e.strip()]
    if isinstance(raw, list):
        return [str(e).strip() for e in raw if str(e).strip()]
    return []


def _extract_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalise config keys — supports both new schema keys (smtp_host …)
    and legacy keys (host …) transparently.
    """
    smtp_host = config.get("smtp_host") or config.get("host") or ""
    smtp_port_raw = config.get("smtp_port") or config.get("port")
    smtp_port = int(smtp_port_raw) if smtp_port_raw else 587
    smtp_username = config.get("smtp_username") or config.get("username") or ""
    smtp_password = config.get("smtp_password") or config.get("password") or ""
    from_email = config.get("from_email") or config.get("from_address") or ""

    to_emails_raw = config.get("to_emails") or config.get("to_addresses")
    to_emails = _parse_recipients(to_emails_raw)

    # tls_enabled controls whether to attempt STARTTLS on non-465 ports.
    # Defaults to True so Gmail / standard SMTP works out of the box.
    tls_enabled = config.get("tls_enabled")
    if tls_enabled is None:
        tls_enabled = config.get("use_tls", True)
    if isinstance(tls_enabled, str):
        tls_enabled = tls_enabled.lower() not in ("false", "0", "no")

    return {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password,
        "from_email": from_email,
        "to_emails": to_emails,
        "tls_enabled": bool(tls_enabled),
    }


class EmailAdapter(BaseAdapter):
    """
    Send emails via SMTP (async, aiosmtplib >= 5.x).

    TLS strategy:
      Port 465  → implicit SSL/TLS   (use_tls=True,  start_tls=False)
      All else  → STARTTLS           (use_tls=False, start_tls=True)  when tls_enabled=True
    """

    TIMEOUT_SECONDS = 30

    async def send(
        self,
        payload: Dict[str, Any],
        channel_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send email via SMTP."""
        is_valid, error = await self.validate_config(channel_config)
        if not is_valid:
            return {"success": False, "error": f"Invalid SMTP config: {error}"}

        start = time.time()
        cfg = _extract_config(channel_config)

        logger.info({
            "event": "smtp_send_attempt",
            "smtp_host": cfg["smtp_host"],
            "smtp_port": cfg["smtp_port"],
            "tls_enabled": cfg["tls_enabled"],
            "username_present": bool(cfg["smtp_username"]),
            "recipient_count": len(cfg["to_emails"]),
        })

        # STEP 1 — Verify enriched payload reaches email adapter
        logger.info({
            "event": "email_payload_debug",
            "payload_keys": list(payload.keys()),
            "payload": payload,
        })

        try:
            msg = self._build_message(
                subject=payload.get("subject", "SemaBridge Notification"),
                from_email=cfg["from_email"],
                to_emails=cfg["to_emails"],
                plaintext=payload.get("plaintext", ""),
                html_raw=payload.get("html", ""),
            )

            use_tls, start_tls = self._tls_params(cfg["smtp_port"], cfg["tls_enabled"])

            await aiosmtplib.send(
                msg,
                hostname=cfg["smtp_host"],
                port=cfg["smtp_port"],
                username=cfg["smtp_username"] or None,
                password=cfg["smtp_password"] or None,
                use_tls=use_tls,
                start_tls=start_tls,
                timeout=self.TIMEOUT_SECONDS,
            )

            logger.info({
                "event": "email_delivery_success",
                "from_email": cfg["from_email"],
                "recipient_count": len(cfg["to_emails"]),
                "smtp_host": cfg["smtp_host"],
            })

            return {
                "success": True,
                "response_code": 250,
                "response_body": "250 OK",
                "duration_ms": self._measure_duration(start),
            }

        except aiosmtplib.SMTPAuthenticationError as e:
            msg_str = f"SMTP authentication failed — check username/password. ({e})"
            logger.exception("SMTP authentication failure")
            return {"success": False, "error": msg_str, "duration_ms": self._measure_duration(start)}

        except aiosmtplib.SMTPConnectError as e:
            msg_str = f"SMTP connection refused ({cfg['smtp_host']}:{cfg['smtp_port']}). ({e})"
            logger.exception("SMTP connection failure")
            return {"success": False, "error": msg_str, "duration_ms": self._measure_duration(start)}

        except aiosmtplib.SMTPServerDisconnected as e:
            msg_str = f"SMTP server disconnected unexpectedly. ({e})"
            logger.exception("SMTP server disconnected")
            return {"success": False, "error": msg_str, "duration_ms": self._measure_duration(start)}

        except TimeoutError:
            msg_str = f"SMTP connection timed out after {self.TIMEOUT_SECONDS}s"
            logger.error(msg_str)
            return {"success": False, "error": msg_str, "duration_ms": self._measure_duration(start)}

        except Exception as e:
            logger.exception("SMTP delivery failure")
            return {"success": False, "error": str(e), "duration_ms": self._measure_duration(start)}

    async def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """Validate SMTP configuration."""
        return validate_smtp_config(config)

    async def test_connection(self, config: Dict[str, Any] = None) -> tuple[bool, str]:
        """
        Test SMTP by sending a real connection-test email.
        Returns (success, error_message).
        """
        if config is None:
            config = self.config

        is_valid, error = await self.validate_config(config)
        if not is_valid:
            return False, error

        cfg = _extract_config(config)

        logger.info({
            "event": "smtp_test_connection_attempt",
            "smtp_host": cfg["smtp_host"],
            "smtp_port": cfg["smtp_port"],
            "tls_enabled": cfg["tls_enabled"],
            "username_present": bool(cfg["smtp_username"]),
            "recipient_count": len(cfg["to_emails"]),
        })

        try:
            msg = self._build_message(
                subject="SemaBridge SMTP Connection Test",
                from_email=cfg["from_email"],
                to_emails=cfg["to_emails"],
                plaintext="This is a real SMTP connection test email from your SemaBridge configuration.",
                html_raw="<p>This is a real SMTP connection test email from your <strong>SemaBridge</strong> configuration.</p>",
            )

            use_tls, start_tls = self._tls_params(cfg["smtp_port"], cfg["tls_enabled"])

            await aiosmtplib.send(
                msg,
                hostname=cfg["smtp_host"],
                port=cfg["smtp_port"],
                username=cfg["smtp_username"] or None,
                password=cfg["smtp_password"] or None,
                use_tls=use_tls,
                start_tls=start_tls,
                timeout=self.TIMEOUT_SECONDS,
            )

            logger.info({
                "event": "smtp_connection_success",
                "smtp_host": cfg["smtp_host"],
                "smtp_port": cfg["smtp_port"],
                "use_tls": use_tls,
                "start_tls": start_tls,
            })
            logger.info({
                "event": "email_delivery_success",
                "from_email": cfg["from_email"],
                "recipient_count": len(cfg["to_emails"]),
            })
            return True, ""

        except aiosmtplib.SMTPAuthenticationError as e:
            err = f"SMTP authentication failed — check username/password. ({e})"
            logger.exception("SMTP test: authentication failure")
            return False, err

        except aiosmtplib.SMTPConnectError as e:
            err = f"SMTP connection refused ({cfg['smtp_host']}:{cfg['smtp_port']}). ({e})"
            logger.exception("SMTP test: connection failure")
            return False, err

        except aiosmtplib.SMTPServerDisconnected as e:
            err = f"SMTP server disconnected unexpectedly. ({e})"
            logger.exception("SMTP test: server disconnected")
            return False, err

        except TimeoutError:
            err = f"SMTP connection timed out after {self.TIMEOUT_SECONDS}s"
            logger.error(err)
            return False, err

        except Exception as e:
            logger.exception("SMTP test: unexpected failure")
            return False, str(e)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _tls_params(port: int, tls_enabled: bool) -> tuple[bool, bool]:
        """
        Return (use_tls, start_tls) for aiosmtplib.send / SMTP constructor.

        Port 465 → implicit SSL   (use_tls=True,  start_tls=False)
        Others   → STARTTLS       (use_tls=False, start_tls=True)  when tls_enabled
        No TLS   → plain          (use_tls=False, start_tls=False)
        """
        if port == 465:
            return True, False  # implicit SSL
        if tls_enabled:
            return False, True  # STARTTLS
        return False, False  # plain (not recommended)

    @staticmethod
    def _build_message(
        subject: str,
        from_email: str,
        to_emails: List[str],
        plaintext: str,
        html_raw: str,
    ) -> MIMEMultipart:
        """Build a MIME multipart/alternative message with plain and HTML parts.

        The HTML is produced by our internal EmailFormatter which already emits a
        complete <!DOCTYPE> / <head> / <style> document.  Passing it through a
        tag-allowlist sanitizer (bleach) would strip the ``<head>`` and ``<style>``
        elements and destroy CSS rendering.  Because all dynamic user data in the
        HTML is already HTML-escaped by the formatter (html.escape), no additional
        sanitization is required at this layer.

        MIME structure:
            Content-Type: multipart/alternative
              ├── text/plain  (fallback for plain-text clients)
              └── text/html   (rendered by HTML-capable clients)
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = ", ".join(to_emails)

        # Plain-text fallback (shown by clients that don't render HTML)
        msg.attach(MIMEText(plaintext, "plain", "utf-8"))

        # HTML part — preserve full document structure so <style> CSS renders correctly
        msg.attach(MIMEText(html_raw, "html", "utf-8"))

        return msg
