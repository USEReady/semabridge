"""
Retry manager with exponential backoff for transient failures.

Wraps callable operations with automatic retry logic, distinguishing
transient errors (retryable) from permanent errors (fail-fast).
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

from semabridge.core.concurrency.error_classifier import ErrorClassifier
from semabridge.core.concurrency.models import RetryConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MaxRetriesExceeded(Exception):
    """All retry attempts exhausted for a transient error."""

    def __init__(self, message: str, last_error: Exception | None = None):
        super().__init__(message)
        self.last_error = last_error


class PermanentError(Exception):
    """Wraps a permanent (non-retryable) error for explicit signalling."""

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


class RetryManager:
    """Execute operations with intelligent retry on transient failures.

    Uses exponential backoff (1s → 2s → 4s → 8s …) and the
    :class:`ErrorClassifier` to distinguish retryable from fatal errors.

    Args:
        config: Retry configuration (max_retries, delays, etc.).
        classifier: Optional custom error classifier instance.
    """

    def __init__(
        self,
        config: RetryConfig | None = None,
        classifier: ErrorClassifier | None = None,
    ) -> None:
        self._config = config or RetryConfig()
        self._classifier = classifier or ErrorClassifier()
        self._last_retry_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_with_retry(
        self,
        operation: Callable[[], T],
        operation_name: str = "operation",
    ) -> T:
        """Execute *operation*, retrying on transient errors.

        Args:
            operation: Zero-argument callable to execute.
            operation_name: Human-readable label for logging.

        Returns:
            The return value of *operation* on success.

        Raises:
            PermanentError: If the operation fails with a non-retryable error.
            MaxRetriesExceeded: If all retry attempts are exhausted.
        """
        attempt = 0
        last_error: Exception | None = None
        self._last_retry_count = 0

        while attempt <= self._config.max_retries:
            try:
                # Back off before retries (not the first attempt)
                if attempt > 0:
                    delay = self.calculate_backoff(attempt)
                    logger.info(
                        "Retry attempt %d/%d for '%s' after %.1fs delay",
                        attempt,
                        self._config.max_retries,
                        operation_name,
                        delay,
                    )
                    time.sleep(delay)

                result = operation()
                self._last_retry_count = attempt
                return result

            except Exception as exc:
                last_error = exc
                error_type = self._classifier.classify(exc)

                if error_type == "permanent":
                    logger.error(
                        "Permanent error in '%s': %s — not retrying.",
                        operation_name,
                        exc,
                    )
                    raise PermanentError(
                        f"Permanent error in '{operation_name}': {exc}",
                        original_error=exc,
                    ) from exc

                # Transient error — advance attempt counter
                attempt += 1
                self._last_retry_count = attempt

                if attempt > self._config.max_retries:
                    logger.error(
                        "Max retries (%d) exceeded for '%s': %s",
                        self._config.max_retries,
                        operation_name,
                        exc,
                    )
                    raise MaxRetriesExceeded(
                        f"Operation '{operation_name}' failed after "
                        f"{self._config.max_retries} retries: {exc}",
                        last_error=exc,
                    ) from exc

                logger.warning(
                    "Transient error in '%s': %s — will retry (%d/%d)",
                    operation_name,
                    exc,
                    attempt,
                    self._config.max_retries,
                )

        # Should not reach here, but guard anyway
        assert last_error is not None
        raise MaxRetriesExceeded(
            f"Operation '{operation_name}' failed after "
            f"{self._config.max_retries} retries",
            last_error=last_error,
        )

    def calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay for a given *attempt*.

        Follows the sequence: base×2⁰, base×2¹, base×2², …
        capped at *max_delay*.

        Args:
            attempt: 1-based attempt number.

        Returns:
            Delay in seconds.
        """
        delay = self._config.base_delay * (
            self._config.exponential_base ** (attempt - 1)
        )
        return min(delay, self._config.max_delay)

    def get_retry_count(self) -> int:
        """Return the retry count from the most recent execution."""
        return self._last_retry_count
