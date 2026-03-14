"""
Output Sanitization Layer.

Provides reusable logic for masking secrets and credentials in logs and console output.
"""

import re
from typing import Any, Dict, List, Union

# Patterns for secrets that should be masked
SECRET_PATTERNS = [
    r"password",
    r"token",
    r"key",
    r"secret",
    r"credential",
    r"account_id",
]

class OutputSanitizer:
    """
    Sanitizes data by masking sensitive information.
    """
    
    @staticmethod
    def mask_secrets(data: Union[str, Dict[str, Any], List[Any]]) -> Union[str, Dict[str, Any], List[Any]]:
        """
        Recursively mask sensitive keys in a dictionary or string.
        """
        if isinstance(data, dict):
            return {
                str(k): OutputSanitizer.mask_secrets(v) if not any(re.search(p, str(k)) for p in SECRET_PATTERNS)
                else "********"
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [OutputSanitizer.mask_secrets(item) for item in data]
        elif isinstance(data, str):
            # Also try to mask pattern matches in strings (e.g., in URLs or CLI commands)
            # This is a bit more complex, for now we just handle key=value patterns
            masked_str = data
            for pattern in SECRET_PATTERNS:
                # Look for patterns like "password=xyz" or "token: abc"
                masked_str = re.sub(
                    rf"({pattern}[:=]\s*)([^\s&,]+)",
                    r"\1********",
                    masked_str,
                    flags=re.IGNORECASE
                )
            return masked_str
        return data

    @staticmethod
    def normalize_json(data: Dict[str, Any]) -> str:
        """Normalize JSON output with sanitization."""
        import json
        sanitized = OutputSanitizer.mask_secrets(data)
        return json.dumps(sanitized, indent=2, sort_keys=True)

    @staticmethod
    def normalize_yaml(data: Dict[str, Any]) -> str:
        """Normalize YAML output with sanitization."""
        import yaml
        sanitized = OutputSanitizer.mask_secrets(data)
        return yaml.dump(sanitized, default_flow_style=False, sort_keys=True)
