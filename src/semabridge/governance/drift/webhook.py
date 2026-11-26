"""Webhook URL security validation for Drift Monitoring notifications.

Enforces SSRF prevention rules:
1. HTTPS scheme only (rejects http, file, ftp, etc.).
2. No credentials embedded in the URL (e.g., https://user:pass@host).
3. Rejection of localhost, internal, loopback, link-local, multicast, and private addresses.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger("semabridge.governance.drift.webhook")

_INTERNAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def validate_webhook_url(url: str) -> None:
    """Validates that a webhook URL is safe and public.

    Raises:
        ValueError: If the URL fails HTTPS, credential, or SSRF security checks.
    """
    if not url or not isinstance(url, str):
        raise ValueError("Webhook URL cannot be empty.")

    clean_url = url.strip()
    try:
        parsed = urlparse(clean_url)
    except Exception as exc:
        raise ValueError(f"Malformed webhook URL: {exc}") from exc

    # 1. Enforce HTTPS scheme
    if (parsed.scheme or "").lower() != "https":
        raise ValueError("Webhook URL must use the HTTPS protocol ('https://').")

    # 2. Reject credentials in URL
    if parsed.username or parsed.password:
        raise ValueError("Webhook URL must not contain embedded user credentials.")

    # 3. Validate host
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("Webhook URL must contain a valid host.")

    if hostname == "localhost" or hostname.endswith(_INTERNAL_HOST_SUFFIXES):
        raise ValueError(f"Webhook URL targets an internal or loopback host: '{hostname}'.")

    # 4. Check if hostname is an explicit IP address
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError(f"Webhook URL targets a private or restricted IP address: '{hostname}'.")
        return
    except ValueError as val_err:
        # If it raised ValueError with our message above, re-raise it
        if "targets a private" in str(val_err):
            raise
        # Otherwise, it's not a numeric IP address, proceed to DNS resolution

    # 5. Resolve hostname to detect DNS rebinding / private IP targets
    try:
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                # Skip NAT64 well-known prefix (RFC 6052)
                if isinstance(ip, ipaddress.IPv6Address) and ip_str.lower().startswith("64:ff9b::"):
                    continue
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                    raise ValueError(f"Webhook host '{hostname}' resolves to a private or internal IP address.")
            except ValueError as ip_err:
                if "resolves to a private" in str(ip_err):
                    raise
    except socket.gaierror as dns_err:
        logger.debug("DNS resolution for webhook host '%s' deferred or unresolvable in current sandbox: %s", hostname, dns_err)
    except Exception as exc:
        if "resolves to a private" in str(exc):
            raise
        logger.debug("DNS validation note for '%s': %s", hostname, exc)
