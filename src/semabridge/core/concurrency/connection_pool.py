"""
DuckDB connection pool manager for concurrent workers.

Provides isolated connections per worker with automatic lifecycle
management and queue-based waiting when the pool is exhausted.
"""

from __future__ import annotations

import logging
import queue
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


class ConnectionPoolManager:
    """Manage a pool of DuckDB connections for parallel workers.

    Each worker gets an isolated connection to prevent conflicts.
    When the pool is exhausted, callers block until a connection
    is returned.

    Args:
        db_path: Path to the DuckDB database file.
        pool_size: Number of connections to maintain.
        timeout: Seconds to wait for a connection before raising.
    """

    def __init__(
        self,
        db_path: str,
        pool_size: int = 4,
        timeout: float = 30.0,
    ) -> None:
        if pool_size < 1:
            raise ValueError(f"pool_size must be >= 1, got {pool_size}")

        self._db_path = db_path
        self._pool_size = pool_size
        self._timeout = timeout
        self._pool: queue.Queue = queue.Queue(maxsize=pool_size)
        self._lock = threading.Lock()
        self._all_connections: list = []
        self._closed = False

        self._initialize_pool()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_connection(self):
        """Acquire a connection from the pool (blocks if exhausted).

        Returns:
            A DuckDB connection.

        Raises:
            RuntimeError: If the pool is closed.
            TimeoutError: If no connection is available within timeout.
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        try:
            conn = self._pool.get(timeout=self._timeout)
        except queue.Empty:
            raise TimeoutError(
                f"No DuckDB connection available within {self._timeout}s "
                f"(pool_size={self._pool_size})"
            )

        # Verify connection is still valid
        if not self._is_connection_valid(conn):
            logger.warning("Stale connection detected — creating replacement")
            conn = self._create_connection()

        return conn

    def release_connection(self, conn) -> None:
        """Return a connection to the pool.

        Args:
            conn: The DuckDB connection to release.
        """
        if self._closed:
            self._close_connection(conn)
            return

        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            # Pool is full — discard the extra connection
            self._close_connection(conn)

    @contextmanager
    def connection(self) -> Iterator:
        """Context manager for automatic connection lifecycle.

        Usage::

            with pool.connection() as conn:
                conn.execute("SELECT 1")
        """
        conn = self.get_connection()
        try:
            yield conn
        finally:
            self.release_connection(conn)

    def close_all(self) -> None:
        """Close all connections and mark the pool as closed."""
        self._closed = True

        # Drain the queue
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                self._close_connection(conn)
            except queue.Empty:
                break

        # Close any tracked connections not in the queue
        with self._lock:
            for conn in self._all_connections:
                self._close_connection(conn)
            self._all_connections.clear()

        logger.info("Connection pool closed (pool_size=%d)", self._pool_size)

    @property
    def available(self) -> int:
        """Number of connections currently available in the pool."""
        return self._pool.qsize()

    @property
    def size(self) -> int:
        """Total pool size."""
        return self._pool_size

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _initialize_pool(self) -> None:
        """Create initial connections and add to the pool."""
        for _ in range(self._pool_size):
            conn = self._create_connection()
            self._pool.put(conn)
        logger.info(
            "Connection pool initialized: %d connections for %s",
            self._pool_size,
            self._db_path,
        )

    def _create_connection(self):
        """Create a new DuckDB connection."""
        import duckdb

        conn = duckdb.connect(self._db_path)
        with self._lock:
            self._all_connections.append(conn)
        return conn

    @staticmethod
    def _is_connection_valid(conn) -> bool:
        """Test that a connection is still usable."""
        try:
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    @staticmethod
    def _close_connection(conn) -> None:
        """Safely close a connection."""
        try:
            conn.close()
        except Exception as exc:
            logger.debug("Error closing pooled connection: %s", exc)
