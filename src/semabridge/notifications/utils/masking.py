"""
Secret masking utilities for API responses and logging.
"""

import re
from typing import Any, Dict


def mask_secret(secret: str, visible_chars: int = 4, redaction_style: str = "ellipsis") -> str:
    """
    Mask a secret string, showing only the last N characters.
    
    Args:
        secret: The secret to mask
        visible_chars: Number of trailing characters to show
        redaction_style: "ellipsis" (•••) or "brackets" ([REDACTED])
    
    Returns:
        Masked secret string
    """
    if not secret or len(secret) <= visible_chars:
        if redaction_style == "brackets":
            return "[REDACTED]"
        return "•" * (len(secret) - visible_chars + 2) if len(secret) > 0 else secret
    
    visible_part = secret[-visible_chars:]
    mask_char = "•" if redaction_style == "ellipsis" else "*"
    masked_part = mask_char * (len(secret) - visible_chars)
    
    return f"{masked_part}{visible_part}"


def mask_url(url: str) -> str:
    """
    Mask webhook URLs, showing only last 4 characters.
    
    Example:
        https://hooks.slack.com/services/ABCD/EFGH/ijklmnop -> https://hooks.slack.com/...ijkl
    """
    if not url:
        return url
    
    try:
        # Extract everything after the last slash
        parts = url.rsplit("/", 1)
        if len(parts) == 2:
            base, path = parts
            if len(path) > 4:
                return f"{base}/...{path[-4:]}"
        return url
    except Exception:
        return url


def mask_api_key(key: str) -> str:
    """Mask API key showing only last 4 characters."""
    return mask_secret(key, visible_chars=4, redaction_style="ellipsis")


def mask_password(password: str) -> str:
    """Fully redact passwords."""
    return "[REDACTED]"


def mask_config_json(config_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mask secrets in config JSON for API responses.
    
    Recognizes patterns like:
    - webhook_url, webhook_urls
    - api_key, api_keys, key
    - password, passwd, pwd
    - secret, secrets
    - token, tokens
    """
    if not config_json:
        return config_json
    
    masked = config_json.copy()
    
    # Pattern definitions for different secret types
    url_patterns = ["webhook_url", "webhook_urls", "url", "urls", "endpoint", "hook_url"]
    key_patterns = ["api_key", "api_keys", "key", "slack_token", "teams_token"]
    password_patterns = ["password", "passwd", "pwd", "smtp_password"]
    secret_patterns = ["secret", "secrets", "private_key", "client_secret"]
    token_patterns = ["token", "tokens", "access_token", "refresh_token"]
    
    for field_key, field_value in masked.items():
        if isinstance(field_value, str):
            field_key_lower = field_key.lower()
            
            # Check which pattern matches
            if any(pattern in field_key_lower for pattern in url_patterns):
                masked[field_key] = mask_url(field_value)
            elif any(pattern in field_key_lower for pattern in password_patterns):
                masked[field_key] = mask_password(field_value)
            elif any(pattern in field_key_lower for pattern in secret_patterns):
                masked[field_key] = mask_api_key(field_value)
            elif any(pattern in field_key_lower for pattern in token_patterns):
                masked[field_key] = mask_api_key(field_value)
            elif any(pattern in field_key_lower for pattern in key_patterns):
                masked[field_key] = mask_api_key(field_value)
    
    return masked


def sanitize_for_logging(data: Any, redact_keys: list = None) -> Any:
    """
    Recursively sanitize data structure for logging, removing secrets.
    """
    if redact_keys is None:
        redact_keys = [
            "password", "pwd", "secret", "key", "token", "credential",
            "apikey", "api_key", "webhook_url", "webhook_urls"
        ]
    
    if isinstance(data, dict):
        return {
            k: "[REDACTED]" if any(kk in k.lower() for kk in redact_keys) else sanitize_for_logging(v, redact_keys)
            for k, v in data.items()
        }
    elif isinstance(data, (list, tuple)):
        return type(data)(sanitize_for_logging(item, redact_keys) for item in data)
    else:
        return data
