"""
Validators for notification configuration and SSRF protection.
"""

import ipaddress
import re
from typing import Tuple
from urllib.parse import urlparse


def is_private_ip(ip_str: str) -> bool:
    """Check if IP address is in private/reserved ranges."""
    try:
        ip = ipaddress.ip_address(ip_str)
        
        # Check if IP is in any private or reserved range
        if ip.is_private:
            return True
        if ip.is_loopback:
            return True
        if ip.is_link_local:
            return True
        if ip.is_multicast:
            return True
        if ip.is_reserved:
            return True
        
        # AWS metadata endpoint
        if str(ip) == "169.254.169.254":
            return True
        
        return False
    except ValueError:
        # Not a valid IP address
        return False


def is_reserved_hostname(hostname: str) -> bool:
    """Check if hostname is reserved/internal."""
    hostname_lower = hostname.lower()
    
    # Local hostnames
    if hostname_lower in ("localhost", "127.0.0.1", "::1"):
        return True
    
    # Reserved TLDs and suffixes
    reserved_suffixes = (
        ".local",
        ".localhost",
        ".internal",
        ".test",
        ".invalid",
        ".example",
        ".onion",
    )
    
    if any(hostname_lower.endswith(suffix) for suffix in reserved_suffixes):
        return True
    
    return False


def validate_webhook_url(url: str) -> Tuple[bool, str]:
    """
    Validate webhook URL for SSRF vulnerabilities.
    
    Returns:
        (is_valid, error_message)
    """
    if not url:
        return False, "URL cannot be empty"
    
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL format: {e}"
    
    # Scheme validation
    if parsed.scheme not in ("http", "https"):
        return False, f"Only http and https schemes are allowed, got: {parsed.scheme}"
    
    # HTTPS is recommended
    if parsed.scheme == "http":
        # Allow HTTP for localhost/dev, but warn
        pass
    
    hostname = parsed.hostname
    if not hostname:
        return False, "URL must have a hostname"
    
    # Check for reserved hostnames
    if is_reserved_hostname(hostname):
        return False, f"Hostname {hostname} is reserved/internal and not allowed"
    
    # Try to resolve hostname and check IP
    try:
        import socket
        try:
            ip_addresses = socket.getaddrinfo(hostname, parsed.port or 443)
            for _, _, _, _, sockaddr in ip_addresses:
                ip = sockaddr[0]
                if is_private_ip(ip):
                    return False, f"Webhook URL resolves to private IP: {ip}"
        except socket.gaierror:
            # Hostname doesn't resolve - might be okay for setup, but risky
            # Allow it but it will likely fail at send time
            pass
    except Exception:
        # If we can't resolve, allow it to proceed (may fail at send time)
        pass
    
    return True, ""


def validate_email_address(email: str) -> Tuple[bool, str]:
    """Validate email address format."""
    # Simple regex pattern
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    
    if not re.match(pattern, email):
        return False, f"Invalid email format: {email}"
    
    if len(email) > 254:
        return False, "Email address too long (max 254 characters)"
    
    return True, ""


def validate_slack_webhook(url: str) -> Tuple[bool, str]:
    """Validate Slack webhook URL format."""
    is_valid, error = validate_webhook_url(url)
    if not is_valid:
        return False, error
    
    # Slack webhooks should be from hooks.slack.com
    if "hooks.slack.com" not in url:
        return False, "URL does not appear to be a Slack webhook (expected hooks.slack.com)"
    
    return True, ""


def validate_teams_webhook(url: str) -> Tuple[bool, str]:
    """Validate Teams webhook URL format."""
    is_valid, error = validate_webhook_url(url)
    if not is_valid:
        return False, error
    
    # Teams webhooks should be from outlook.webhook.office.com or teams.microsoft.com
    if not ("outlook.webhook.office.com" in url or "teams.microsoft.com" in url):
        return False, "URL does not appear to be a Teams webhook"
    
    return True, ""


def validate_smtp_config(config: dict) -> Tuple[bool, str]:
    """Validate SMTP configuration."""
    required = ["host", "port", "username", "password", "from_address"]
    
    for field in required:
        if field not in config or not config[field]:
            return False, f"Missing required SMTP field: {field}"
    
    # Validate port
    try:
        port = int(config["port"])
        if port < 1 or port > 65535:
            return False, "SMTP port must be between 1 and 65535"
    except (ValueError, TypeError):
        return False, "SMTP port must be an integer"
    
    # Validate from_address
    is_valid, error = validate_email_address(config["from_address"])
    if not is_valid:
        return False, f"Invalid SMTP from_address: {error}"
    
    return True, ""


def validate_pagerduty_config(config: dict) -> Tuple[bool, str]:
    """Validate PagerDuty configuration."""
    if "routing_key" not in config or not config["routing_key"]:
        return False, "Missing required PagerDuty routing_key"
    
    if "integration_url" not in config:
        config["integration_url"] = "https://events.pagerduty.com/v2/enqueue"
    
    is_valid, error = validate_webhook_url(config["integration_url"])
    if not is_valid:
        return False, f"Invalid PagerDuty integration URL: {error}"
    
    return True, ""
