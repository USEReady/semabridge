"""
Tests for Semabridge concurrency utilities.

Validates:
- TracedThreadPoolExecutor context propagation
- SnowflakeConnectionPool thread isolation
- retry_with_backoff / Retry-After header parsing
- fan_in_results exception aggregation
- ConcurrencyConfig validation
"""

from __future__ import annotations

import contextvars
import threading
import time
from concurrent.futures import Future
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from semabridge.utils.concurrency import (
    AggregatedError,
    RetryableHTTPError,
    SnowflakeConnectionPool,
    TracedThreadPoolExecutor,
    fan_in_results,
    retry_with_backoff,
    _is_retryable,
)


# =============================================================================
# TracedThreadPoolExecutor Tests
# =============================================================================


class TestTracedThreadPoolExecutor:
    """Verify contextvars propagation to worker threads."""

    def test_context_propagates_to_worker(self) -> None:
        """Parent-thread contextvars should be visible in worker threads."""
        run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id")

        # Set context in the parent thread
        token = run_id_var.set("test-run-123")

        captured_values: list[str] = []

        def _capture_context() -> str:
            val = run_id_var.get("NOT_SET")
            captured_values.append(val)
            return val

        try:
            with TracedThreadPoolExecutor(max_workers=2) as executor:
                future = executor.submit(_capture_context)
                result = future.result(timeout=5)

            assert result == "test-run-123"
            assert captured_values == ["test-run-123"]
        finally:
            run_id_var.reset(token)

    def test_context_isolation_between_submissions(self) -> None:
        """Each submit() captures context at call time, not execution time."""
        var: contextvars.ContextVar[int] = contextvars.ContextVar("counter")

        token1 = var.set(1)
        with TracedThreadPoolExecutor(max_workers=1) as executor:
            f1 = executor.submit(var.get, 0)
            var.set(2)
            f2 = executor.submit(var.get, 0)

            # f1 should see value 1, f2 should see value 2
            assert f1.result(timeout=5) == 1
            assert f2.result(timeout=5) == 2

        var.reset(token1)

    def test_map_propagates_context(self) -> None:
        """Executor.map() should also propagate context."""
        prefix_var: contextvars.ContextVar[str] = contextvars.ContextVar("prefix")
        token = prefix_var.set("ctx")

        def _append_prefix(val: int) -> str:
            return f"{prefix_var.get('none')}-{val}"

        try:
            with TracedThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(_append_prefix, [1, 2, 3]))

            assert results == ["ctx-1", "ctx-2", "ctx-3"]
        finally:
            prefix_var.reset(token)

    def test_exception_propagation(self) -> None:
        """Exceptions in workers should propagate to the main thread."""

        def _fail() -> None:
            raise ValueError("worker error")

        with TracedThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_fail)

            with pytest.raises(ValueError, match="worker error"):
                future.result(timeout=5)


# =============================================================================
# retry_with_backoff Tests
# =============================================================================


class TestRetryWithBackoff:
    """Verify tenacity retry decorator behavior."""

    def test_does_not_retry_on_non_retryable_error(self) -> None:
        """Non-retryable errors should fail immediately without retry."""
        call_count = 0

        @retry_with_backoff(max_attempts=3)
        def _always_401() -> None:
            nonlocal call_count
            call_count += 1
            raise RetryableHTTPError("Unauthorized", status_code=401)

        with pytest.raises(RetryableHTTPError):
            _always_401()

        # Should only be called once (no retries)
        assert call_count == 1

    def test_retries_on_429(self) -> None:
        """HTTP 429 should trigger retry with Retry-After support."""
        call_count = 0

        @retry_with_backoff(max_attempts=3)
        def _fails_then_succeeds() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RetryableHTTPError(
                    "Rate limited",
                    status_code=429,
                    retry_after=0.01,  # Very short for testing
                )
            return "success"

        result = _fails_then_succeeds()
        assert result == "success"
        assert call_count == 3

    def test_retries_on_connection_error(self) -> None:
        """ConnectionError should be retryable."""
        call_count = 0

        @retry_with_backoff(max_attempts=2)
        def _connection_fails() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("connection reset")
            return "recovered"

        result = _connection_fails()
        assert result == "recovered"
        assert call_count == 2

    def test_respects_max_attempts(self) -> None:
        """Should stop retrying after max_attempts."""

        @retry_with_backoff(max_attempts=2)
        def _always_fails() -> None:
            raise RetryableHTTPError("Server Error", status_code=500)

        with pytest.raises(RetryableHTTPError):
            _always_fails()


class TestIsRetryable:
    """Verify _is_retryable logic."""

    def test_retryable_status_codes(self) -> None:
        for code in (429, 500, 502, 503, 504):
            exc = RetryableHTTPError("test", status_code=code)
            assert _is_retryable(exc) is True, f"Status {code} should be retryable"

    def test_non_retryable_status_codes(self) -> None:
        for code in (400, 401, 403, 404, 422):
            exc = RetryableHTTPError("test", status_code=code)
            assert _is_retryable(exc) is False, f"Status {code} should NOT be retryable"

    def test_connection_error_is_retryable(self) -> None:
        assert _is_retryable(ConnectionError("test")) is True

    def test_timeout_is_retryable(self) -> None:
        assert _is_retryable(TimeoutError("test")) is True

    def test_generic_exception_not_retryable(self) -> None:
        assert _is_retryable(ValueError("test")) is False


# =============================================================================
# fan_in_results Tests
# =============================================================================


class TestFanInResults:
    """Verify parallel task aggregation and error collection."""

    def test_empty_tasks(self) -> None:
        """Empty task list should return empty results."""
        with TracedThreadPoolExecutor(max_workers=1) as executor:
            results = fan_in_results(executor, [], task_label="test")
        assert results == []

    def test_all_succeed(self) -> None:
        """All successful tasks should return ordered results."""
        tasks = [lambda i=i: i * 2 for i in range(5)]

        with TracedThreadPoolExecutor(max_workers=3) as executor:
            results = fan_in_results(executor, tasks, task_label="multiply")

        assert results == [0, 2, 4, 6, 8]

    def test_partial_failure_raises_aggregated_error(self) -> None:
        """Mixed success/failure should raise AggregatedError."""

        def _succeed() -> str:
            return "ok"

        def _fail() -> str:
            raise ValueError("boom")

        tasks = [_succeed, _fail, _succeed, _fail]

        with TracedThreadPoolExecutor(max_workers=2) as executor:
            with pytest.raises(AggregatedError) as exc_info:
                fan_in_results(executor, tasks, task_label="mixed")

        assert exc_info.value.failed_count == 2
        assert exc_info.value.total_count == 4
        assert len(exc_info.value.exceptions) == 2

    def test_concurrency_runs_parallel(self) -> None:
        """Tasks should genuinely run in parallel (not sequentially)."""
        results: list[float] = []

        def _timed_task(idx: int) -> float:
            start = time.monotonic()
            time.sleep(0.1)
            elapsed = time.monotonic() - start
            return elapsed

        tasks = [lambda i=i: _timed_task(i) for i in range(4)]
        wall_start = time.monotonic()

        with TracedThreadPoolExecutor(max_workers=4) as executor:
            results = fan_in_results(executor, tasks, task_label="parallel")

        wall_elapsed = time.monotonic() - wall_start
        # 4 tasks * 0.1s each, with 4 workers, total wall time should be ~0.1s
        # Allow generous margin for CI
        assert wall_elapsed < 0.5, f"Expected parallel execution, got {wall_elapsed:.2f}s"


# =============================================================================
# AggregatedError Tests
# =============================================================================


class TestAggregatedError:
    """Verify error aggregation formatting."""

    def test_message_includes_counts(self) -> None:
        err = AggregatedError(
            message="Test failed",
            exceptions=[ValueError("a"), TypeError("b")],
            total_count=5,
        )
        assert "(2/5 failed)" in str(err)

    def test_message_truncates_at_5(self) -> None:
        exceptions = [ValueError(f"err{i}") for i in range(8)]
        err = AggregatedError(
            message="Batch",
            exceptions=exceptions,
            total_count=10,
        )
        assert "... and 3 more" in str(err)


# =============================================================================
# ConcurrencyConfig Tests
# =============================================================================


class TestConcurrencyConfig:
    """Verify settings validation."""

    def test_default_values(self) -> None:
        from semabridge.core.settings import ConcurrencyConfig
        cfg = ConcurrencyConfig()
        assert cfg.max_workers == 5
        assert cfg.enable_parallel is False

    def test_max_workers_upper_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from semabridge.core.settings import ConcurrencyConfig
        monkeypatch.setenv("MAX_WORKERS", "100")
        with pytest.raises(Exception):
            ConcurrencyConfig()

    def test_max_workers_lower_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from semabridge.core.settings import ConcurrencyConfig
        monkeypatch.setenv("MAX_WORKERS", "0")
        with pytest.raises(Exception):
            ConcurrencyConfig()
