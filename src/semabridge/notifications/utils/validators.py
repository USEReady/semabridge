"""
Validators for notification configuration and SSRF protection.
"""

import ipaddress
import logging
import re
from typing import Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)



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
        try:
            parsed_url = urlparse(url)
            logger.info({
                "event": "teams_webhook_validation",
                "hostname": parsed_url.hostname,
                "accepted": False,
            })
        except Exception:
            pass
        return False, error
    
    try:
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname or ""
        hostname_lower = hostname.lower()
        
        # Expanded allowed hostname patterns (STEP 1):
        # Legacy: outlook.webhook.office.com, teams.microsoft.com, webhook.office.com
        # Modern: logic.azure.com, *.logic.azure.com, prod-*.logic.azure.com, powerautomate.microsoft.com
        is_accepted = False
        if hostname_lower == "logic.azure.com" or hostname_lower.endswith(".logic.azure.com"):
            is_accepted = True
        elif hostname_lower == "powerautomate.microsoft.com" or hostname_lower.endswith(".powerautomate.microsoft.com"):
            is_accepted = True
        elif hostname_lower in ("outlook.webhook.office.com", "teams.microsoft.com", "webhook.office.com"):
            is_accepted = True
        elif hostname_lower.endswith(".webhook.office.com") or hostname_lower.endswith(".teams.microsoft.com") or hostname_lower.endswith(".webhook.office.com"):
            is_accepted = True
            
        # STEP 2 — Structured validation logging
        logger.info({
            "event": "teams_webhook_validation",
            "hostname": hostname,
            "accepted": is_accepted,
        })
        
        if not is_accepted:
            return False, "URL does not appear to be a Teams webhook"
            
        return True, ""
    except Exception as e:
        logger.error(f"Error validating Teams webhook: {e}")
        return False, f"Error validating Teams webhook: {e}"


def validate_smtp_config(config: dict) -> Tuple[bool, str]:
    """Validate SMTP configuration."""
    # Map legacy keys if present for backward compatibility
    if "host" in config and "smtp_host" not in config:
        config["smtp_host"] = config["host"]
    if "port" in config and "smtp_port" not in config:
        config["smtp_port"] = config["port"]
    if "username" in config and "smtp_username" not in config:
        config["smtp_username"] = config["username"]
    if "password" in config and "smtp_password" not in config:
        config["smtp_password"] = config["password"]
    if "from_address" in config and "from_email" not in config:
        config["from_email"] = config["from_address"]
    if "to_addresses" in config and "to_emails" not in config:
        to_addr = config["to_addresses"]
        if isinstance(to_addr, list):
            config["to_emails"] = ",".join(to_addr)
        else:
            config["to_emails"] = to_addr

    required = ["smtp_host", "smtp_port", "smtp_username", "smtp_password", "from_email", "to_emails"]
    
    for field in required:
        if field not in config or config[field] is None or str(config[field]).strip() == "":
            return False, f"Missing required SMTP field: {field}"
    
    # Validate port
    try:
        port = int(config["smtp_port"])
        if port < 1 or port > 65535:
            return False, "SMTP port must be between 1 and 65535"
    except (ValueError, TypeError):
        return False, "SMTP port must be an integer"
    
    # Validate from_email
    is_valid, error = validate_email_address(config["from_email"])
    if not is_valid:
        return False, f"Invalid SMTP from_email: {error}"

    # Validate to_emails (can be a comma-separated string or a list)
    to_emails = config["to_emails"]
    if isinstance(to_emails, str):
        emails = [e.strip() for e in to_emails.split(",") if e.strip()]
    elif isinstance(to_emails, list):
        emails = [str(e).strip() for e in to_emails if str(e).strip()]
    else:
        return False, "SMTP to_emails must be a comma-separated string or a list of emails"
    
    if not emails:
        return False, "SMTP to_emails must contain at least one recipient email address"
        
    for email in emails:
        is_valid, error = validate_email_address(email)
        if not is_valid:
            return False, f"Invalid SMTP to_emails recipient: {error}"
    
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
