"""
Concurrency utilities for Semabridge parallel processing.

Provides thread-safe execution primitives, connection pooling, and
retry logic with Retry-After header parsing for API rate limiting.

Architecture:
- TracedThreadPoolExecutor: contextvars-aware executor for structured logging
- SnowflakeConnectionPool: per-thread connection isolation via contextmanager
- retry_with_backoff: tenacity decorator for HTTP 429 / transient error resilience
- fan_in_results: parallel task submission with exception aggregation
"""

from __future__ import annotations

import contextvars
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Callable, Generator, Iterable, List, Optional, TypeVar

from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


# =============================================================================
# Custom Exceptions
# =============================================================================


class AggregatedError(Exception):
    """Raised when multiple concurrent tasks fail.

    Collects all individual exceptions for unified error reporting.

    Attributes:
        exceptions: List of individual exceptions from failed workers.
        failed_count: Number of failed tasks.
        total_count: Total number of submitted tasks.
    """

    def __init__(
        self,
        message: str,
        exceptions: List[Exception],
        total_count: int,
    ) -> None:
        self.exceptions = exceptions
        self.failed_count = len(exceptions)
        self.total_count = total_count
        detail = "; ".join(str(e) for e in exceptions[:5])
        if len(exceptions) > 5:
            detail += f" ... and {len(exceptions) - 5} more"
        super().__init__(f"{message} ({self.failed_count}/{total_count} failed): {detail}")


class RetryableHTTPError(Exception):
    """Wraps HTTP errors that carry retry metadata.

    Attributes:
        status_code: The HTTP status code.
        retry_after: Seconds to wait before retrying (from Retry-After header).
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        retry_after: Optional[float] = None,
    ) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)


# =============================================================================
# TracedThreadPoolExecutor
# =============================================================================


class TracedThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that propagates contextvars to worker threads.

    Standard ThreadPoolExecutor does NOT copy contextvars from the parent
    thread. This subclass captures the parent context at submit() time
    and wraps the callable in ctx.run() so that RunID, ProjectID, and
    other structured logging variables are available in worker threads.

    Args:
        max_workers: Maximum number of worker threads.
        thread_name_prefix: Prefix for worker thread names.
    """

    def __init__(
        self,
        max_workers: int = 5,
        thread_name_prefix: str = "semabridge-worker",
    ) -> None:
        super().__init__(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )

    def submit(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[T]:
        """Submit a callable with parent context propagation.

        Args:
            fn: Callable to execute in a worker thread.
            *args: Positional arguments for the callable.
            **kwargs: Keyword arguments for the callable.

        Returns:
            Future representing the pending result.
        """
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)

    def map(
        self,
        fn: Callable[..., T],
        *iterables: Iterable[Any],
        timeout: Optional[float] = None,
        chunksize: int = 1,
    ) -> Iterable[T]:
        """Map with context propagation.

        Wraps each invocation in the parent's context snapshot.

        Args:
            fn: Callable to map across iterables.
            *iterables: Input iterables.
            timeout: Maximum seconds to wait for results.
            chunksize: Ignored (kept for API compat).

        Returns:
            Iterator of results.
        """
        ctx = contextvars.copy_context()

        def _wrapped(*call_args: Any) -> T:
            return ctx.run(fn, *call_args)

        return super().map(_wrapped, *iterables, timeout=timeout, chunksize=chunksize)


# =============================================================================
# SnowflakeConnectionPool
# =============================================================================


class SnowflakeConnectionPool:
    """Thread-safe Snowflake connection pool.

    Each worker thread receives its own dedicated connection via a
    contextmanager. Connections are created lazily and stored in
    threading.local() to prevent cross-thread cursor sharing.

    The pool enforces a strict max_size matching the TracedThreadPoolExecutor
    worker count to prevent connection exhaustion.

    Args:
        config: SnowflakeConfig with connection parameters.
        max_size: Maximum number of connections (should match max_workers).
    """

    def __init__(self, config: Any, max_size: int = 5) -> None:
        self._config = config
        self._max_size = max_size
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: List[Any] = []

    @contextmanager
    def get_connection(self) -> Generator[Any, None, None]:
        """Get a thread-local Snowflake connection.

        Each thread gets its own connection. The connection is created
        on first access and reused for the thread's lifetime.

        Yields:
            A Snowflake connection object dedicated to the calling thread.

        Raises:
            ConnectionError: If pool capacity is exceeded.
        """
        import snowflake.connector

        conn = getattr(self._local, "connection", None)
        if conn is None:
            with self._lock:
                if len(self._connections) >= self._max_size:
                    raise ConnectionError(
                        f"Snowflake connection pool exhausted "
                        f"(max_size={self._max_size}). "
                        f"Reduce MAX_WORKERS or increase pool capacity."
                    )

            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
            kwargs = get_snowflake_connect_kwargs(self._config)
            kwargs["session_parameters"] = {
                "QUERY_TAG": "Semabridge_Parallel_Worker",
            }
            conn = snowflake.connector.connect(**kwargs)
            self._local.connection = conn
            with self._lock:
                self._connections.append(conn)
            logger.debug(
                f"Created new Snowflake connection for thread "
                f"{threading.current_thread().name} "
                f"(pool: {len(self._connections)}/{self._max_size})"
            )

        try:
            yield conn
        except Exception:
            # On error, invalidate the thread-local connection
            # so the next call creates a fresh one
            self._local.connection = None
            with self._lock:
                if conn in self._connections:
                    self._connections.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
            raise

    def close_all(self) -> None:
        """Close all connections in the pool.

        Should be called during shutdown to release Snowflake sessions.
        """
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"Error closing pooled connection: {e}")
            self._connections.clear()
            self._local = threading.local()
            logger.debug("Snowflake connection pool closed")


# =============================================================================
# Retry Logic with Retry-After Header Parsing
# =============================================================================


def _is_retryable(exc: BaseException) -> bool:
    """Determine if an exception is retryable.

    Retryable:
    - RetryableHTTPError (HTTP 429, 5xx)
    - ConnectionError, TimeoutError
    - requests.exceptions.ConnectionError

    NOT retryable:
    - HTTP 401, 403, 404 (auth/not-found — fail fast)
    - Any other exception
    """
    if isinstance(exc, RetryableHTTPError):
        return exc.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    # Handle requests library errors
    try:
        from requests.exceptions import ConnectionError as ReqConnError
        from requests.exceptions import Timeout as ReqTimeout

        if isinstance(exc, (ReqConnError, ReqTimeout)):
            return True
    except ImportError:
        pass
    return False


def _wait_for_retry_after(retry_state: RetryCallState) -> float:
    """Custom tenacity wait callback that respects Retry-After headers.

    If the exception carries a retry_after value (from HTTP 429),
    use that exact duration. Otherwise fall back to exponential backoff.

    Args:
        retry_state: Tenacity retry state with exception info.

    Returns:
        Number of seconds to wait before the next attempt.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RetryableHTTPError) and exc.retry_after is not None:
        wait_seconds = exc.retry_after
        logger.info(
            f"Respecting Retry-After header: waiting {wait_seconds}s "
            f"(attempt {retry_state.attempt_number})"
        )
        return wait_seconds

    # Exponential backoff fallback: 1s, 2s, 4s, 8s, ... capped at 60s
    return wait_exponential(min=1, max=60)(retry_state)


def retry_with_backoff(
    max_attempts: int = 5,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Create a tenacity retry decorator for network operations.

    Handles:
    - HTTP 429 (Too Many Requests) with Retry-After header parsing
    - Transient 5xx gateway errors with exponential backoff
    - Connection/timeout errors

    Does NOT retry:
    - HTTP 401 (Unauthorized), 403 (Forbidden), 404 (Not Found)
    - Application-level errors

    Args:
        max_attempts: Maximum number of retry attempts.

    Returns:
        A decorator that wraps the function with retry logic.
    """
    return retry(
        retry=retry_if_exception(_is_retryable),
        wait=_wait_for_retry_after,
        stop=stop_after_attempt(max_attempts),
        reraise=True,
        before_sleep=lambda rs: logger.warning(
            f"Retrying after error (attempt {rs.attempt_number}/{max_attempts}): "
            f"{rs.outcome.exception() if rs.outcome else 'unknown'}"
        ),
    )


# =============================================================================
# Fan-In Result Aggregation
# =============================================================================


def fan_in_results(
    executor: TracedThreadPoolExecutor,
    tasks: List[Callable[[], T]],
    task_label: str = "task",
) -> List[T]:
    """Submit tasks to executor and aggregate results with error collection.

    All tasks are submitted concurrently. Results are collected in
    submission order. If any tasks fail, their exceptions are aggregated
    into a single AggregatedError.

    Args:
        executor: TracedThreadPoolExecutor to submit tasks to.
        tasks: List of zero-arg callables to execute concurrently.
        task_label: Human-readable label for error messages.

    Returns:
        List of results in the same order as the input tasks.

    Raises:
        AggregatedError: If one or more tasks failed.
    """
    if not tasks:
        return []

    futures: List[Future[T]] = []
    for task in tasks:
        futures.append(executor.submit(task))

    results: List[T] = []
    errors: List[Exception] = []

    for i, future in enumerate(futures):
        try:
            results.append(future.result())
        except Exception as e:
            logger.error(f"Concurrent {task_label} #{i} failed: {e}")
            errors.append(e)

    if errors:
        raise AggregatedError(
            message=f"Concurrent {task_label} execution failed",
            exceptions=errors,
            total_count=len(tasks),
        )


    return results

