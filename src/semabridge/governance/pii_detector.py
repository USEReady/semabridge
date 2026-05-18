"""
Data Governance: PII Detection & Dynamic Masking.

Automatically detects sensitive data and applies masking policies.
"""

import re
import hashlib
from typing import List, Dict, Any, Optional
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class PIIDetector:
    """
    Detects PII in columns based on patterns and samples.
    """
    def __init__(self):
        self.patterns = {
            "email": r'[\w\.-]+@[\w\.-]+\.\w+',
            "ssn": r'\d{3}-\d{2}-\d{4}',
            "phone": r'\d{3}[-.]?\d{3}[-.]?\d{4}',
            "credit_card": r'\d{4}-\d{4}-\d{4}-\d{4}'
        }

    def detect(self, column_name: str, sample_values: List[str]) -> str:
        for pii_type, pattern in self.patterns.items():
            if re.search(pii_type, column_name, re.IGNORECASE):
                return "pii"
            for val in sample_values:
                if re.match(pattern, str(val)):
                    return "pii"
        return "public"

class MaskingEngine:
    """
    Applies masking strategies to sensitive values.
    """
    def apply_mask(self, value: Any, strategy: str = "redact") -> str:
        if strategy == "redact":
            return "***REDACTED***"
        elif strategy == "hash":
            return hashlib.sha256(str(value).encode()).hexdigest()[:16]
        elif strategy == "partial":
            s = str(value)
            return s[:3] + "***" + s[-2:] if len(s) > 5 else "***"
        return str(value)
