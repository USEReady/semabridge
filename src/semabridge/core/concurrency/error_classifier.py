"""
Error classification for the concurrent processing system.

Distinguishes transient (retryable) errors from permanent (fail-fast)
errors to drive intelligent retry logic.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Transient error indicators — these may succeed on retry
# ---------------------------------------------------------------------------
_TRANSIENT_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionRefusedError",
        "TimeoutError",
        "ConnectTimeoutError",
        "ReadTimeout",
        "WriteTimeout",
        "BrokenPipeError",
        "OperationalError",  # DB temporary lock
        "TemporaryError",
        "RetryError",
    }
)

_TRANSIENT_HTTP_STATUS_CODES: frozenset[int] = frozenset(
    {
        408,  # Request Timeout
        429,  # Too Many Requests / Rate Limit
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    }
)

_TRANSIENT_MESSAGE_FRAGMENTS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "rate limit",
    "throttl",
    "too many requests",
    "service unavailable",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "broken pipe",
    "network",
    "temporary",
    "try again",
)

# ---------------------------------------------------------------------------
# Permanent error indicators — do NOT retry
# ---------------------------------------------------------------------------
_PERMANENT_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "AuthenticationError",
        "PermissionError",
        "ValidationError",
        "SchemaError",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "NotImplementedError",
        "FileNotFoundError",
        "MissingCredentialError",
        "ConversionError",
    }
)

_PERMANENT_HTTP_STATUS_CODES: frozenset[int] = frozenset(
    {
        400,  # Bad Request
        401,  # Unauthorized
        403,  # Forbidden
        404,  # Not Found
        405,  # Method Not Allowed
        409,  # Conflict
        422,  # Unprocessable Entity
    }
)


class ErrorClassifier:
    """Classifies exceptions as *transient* or *permanent*.

    Transient errors are retryable (network timeouts, rate limits).
    Permanent errors should fail fast (auth, validation, schema).
    """

    def classify(self, error: Exception) -> str:
        """Classify an exception as ``"transient"`` or ``"permanent"``.

        The classification is determined by (in priority order):
        1. HTTP status code (if the error carries one).
        2. Exception type name.
        3. Error message substring matching.
        4. Default → ``"permanent"`` (fail fast).
        """
        # 1. Check HTTP status code
        status = self._extract_status_code(error)
        if status is not None:
            if status in _TRANSIENT_HTTP_STATUS_CODES:
                return "transient"
            if status in _PERMANENT_HTTP_STATUS_CODES:
                return "permanent"

        # 2. Check exception type name (walk MRO)
        for cls in type(error).__mro__:
            name = cls.__name__
            if name in _TRANSIENT_EXCEPTION_NAMES:
                return "transient"
            if name in _PERMANENT_EXCEPTION_NAMES:
                return "permanent"

        # 3. Check error message
        msg = str(error).lower()
        for fragment in _TRANSIENT_MESSAGE_FRAGMENTS:
            if fragment in msg:
                return "transient"

        # 4. Default: permanent (fail fast for unknown errors)
        logger.debug(
            "Unknown error type %s classified as permanent: %s",
            type(error).__name__,
            error,
        )
        return "permanent"

    def is_transient(self, error: Exception) -> bool:
        """Return ``True`` if the error is transient (retryable)."""
        return self.classify(error) == "transient"

    def is_permanent(self, error: Exception) -> bool:
        """Return ``True`` if the error is permanent (not retryable)."""
        return self.classify(error) == "permanent"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_status_code(error: Exception) -> Optional[int]:
        """Try to pull an HTTP status code from the exception."""
        # requests.HTTPError
        if hasattr(error, "response") and hasattr(error.response, "status_code"):
            return int(error.response.status_code)
        # Generic status_code attribute
        if hasattr(error, "status_code"):
            return int(error.status_code)
        # aiohttp style
        if hasattr(error, "status"):
            try:
                return int(error.status)
            except (ValueError, TypeError):
                pass
        return None
