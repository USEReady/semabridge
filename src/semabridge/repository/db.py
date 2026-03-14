"""
Repository Database Abstraction Layer.

Provides a unified interface for database operations, defaulting to DuckDB
as the embedded OLAP engine while supporting ORM-based providers via
dependency injection.

Architecture:
    - DuckDB (default): High-performance columnar engine for analytical queries.
    - ORM fallback: SQLAlchemy/SQLModel for teams requiring traditional RDBMS.

The abstraction uses a Protocol-based contract so that the concrete backend
can be swapped without changes to calling code.
"""

from __future__ import annotations

import os
import threading
import warnings
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Protocol, Sequence, Tuple

from semabridge.utils.logger import get_logger

warnings.warn(
    "semabridge.repository.db (DuckDBBackend / ORMBackend) is deprecated and will be "
    "removed in a future release. Use semabridge.repository.orm.session_factory and "
    "semabridge.repository.model_repository.ModelRepository instead.",
    DeprecationWarning,
    stacklevel=2,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Repository Protocol — the contract every backend must satisfy
# ---------------------------------------------------------------------------

class RepositoryBackend(Protocol):
    """Protocol defining the database backend contract.

    Any backend (DuckDB, SQLAlchemy, etc.) must implement these methods
    to be used as a SemaBridge repository store.
    """

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> Any:
        """Execute a SQL statement with optional parameters."""
        ...

    def fetchone(self, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Tuple[Any, ...]]:
        """Execute and return a single row."""
        ...

    def fetchall(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[Tuple[Any, ...]]:
        """Execute and return all rows."""
        ...

    def begin_transaction(self) -> None:
        """Begin an explicit transaction."""
        ...

    def commit(self) -> None:
        """Commit the active transaction."""
        ...

    def rollback(self) -> None:
        """Roll back the active transaction."""
        ...

    def close(self) -> None:
        """Release the connection back to pool or close it."""
        ...


# ---------------------------------------------------------------------------
# DuckDB Backend — the recommended default
# ---------------------------------------------------------------------------

class DuckDBBackend:
    """DuckDB-backed repository with single-writer thread safety.

    .. deprecated::
        Prefer ``ModelRepository`` (SQLAlchemy ORM) for new code.
        ``DuckDBBackend`` is retained for backward compatibility only.

    DuckDB supports MVCC within a single writer process. This backend
    funnels all write operations through a dedicated writer lock to
    prevent file-lock exceptions when multiple threads attempt writes.

    Args:
        db_path: Filesystem path to the DuckDB database file.
    """

    def __init__(self, db_path: str) -> None:
        import duckdb

        self._db_path = db_path
        self._write_lock = threading.Lock()
        self._conn = duckdb.connect(db_path)
        logger.info(f"DuckDB backend initialized at {db_path}")

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> Any:
        """Execute SQL with the writer lock for mutations."""
        with self._write_lock:
            if params:
                return self._conn.execute(sql, list(params))
            return self._conn.execute(sql)

    def fetchone(self, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Tuple[Any, ...]]:
        """Execute and return a single row (read-only, no lock needed)."""
        if params:
            return self._conn.execute(sql, list(params)).fetchone()
        return self._conn.execute(sql).fetchone()

    def fetchall(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[Tuple[Any, ...]]:
        """Execute and return all rows (read-only, no lock needed)."""
        if params:
            return self._conn.execute(sql, list(params)).fetchall()
        return self._conn.execute(sql).fetchall()

    def begin_transaction(self) -> None:
        """Begin an explicit DuckDB transaction."""
        self._write_lock.acquire()
        self._conn.begin()

    def commit(self) -> None:
        """Commit the active transaction and release the writer lock."""
        try:
            self._conn.commit()
        finally:
            self._write_lock.release()

    def rollback(self) -> None:
        """Roll back the active transaction and release the writer lock."""
        try:
            self._conn.rollback()
        finally:
            self._write_lock.release()

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._conn.close()

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """Context manager for ACID transactional blocks.

        Usage:
            with backend.transaction():
                backend.execute("INSERT INTO ...")
                backend.execute("UPDATE ...")
        """
        self.begin_transaction()
        try:
            yield
            self.commit()
        except Exception:
            self.rollback()
            raise


# ---------------------------------------------------------------------------
# ORM Backend — optional fallback for traditional RDBMS
# ---------------------------------------------------------------------------

class ORMBackend:
    """SQLAlchemy/SQLModel-backed repository fallback.

    Accepts a SQLAlchemy engine URL and provides the same interface
    as DuckDBBackend using SQLAlchemy Core for query execution.

    Args:
        connection_url: SQLAlchemy connection string (e.g. 'sqlite:///app.db',
                        'postgresql://user:pass@host/db').
    """

    def __init__(self, connection_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            self._text = text
        except ImportError as exc:
            raise ImportError(
                "SQLAlchemy is required for the ORM backend. "
                "Install it with: pip install sqlalchemy"
            ) from exc

        self._engine = create_engine(connection_url, echo=False)
        self._conn = self._engine.connect()
        self._in_transaction = False
        self._txn = None
        logger.info(f"ORM backend initialized with {connection_url.split('://')[0]} engine")

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> Any:
        """Execute SQL via SQLAlchemy.

        Positional ``?`` placeholders (DuckDB / SQLite style) are
        automatically rewritten to SQLAlchemy named bindings
        (``:p0``, ``:p1``, …) so the same SQL works with any dialect.
        """
        if params:
            # Convert positional '?' → named ':p0', ':p1', ...
            named_sql = sql
            named_params: dict[str, Any] = {}
            for idx, val in enumerate(params):
                named_params[f"p{idx}"] = val
            # Replace each '?' in order with ':p0', ':p1', ...
            parts = named_sql.split("?")
            if len(parts) - 1 == len(params):
                named_sql = "".join(
                    part + (f":p{i}" if i < len(params) else "")
                    for i, part in enumerate(parts)
                )
            else:
                # If placeholders don't match, fall back to raw execution
                named_params = {}  # type: ignore[assignment]
                named_sql = sql
            stmt = self._text(named_sql)
            return self._conn.execute(stmt, named_params) if named_params else self._conn.execute(stmt)
        stmt = self._text(sql)
        return self._conn.execute(stmt)

    def fetchone(self, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Tuple[Any, ...]]:
        """Execute and fetch one row via SQLAlchemy."""
        result = self.execute(sql, params)
        row = result.fetchone()
        return tuple(row) if row else None

    def fetchall(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[Tuple[Any, ...]]:
        """Execute and fetch all rows via SQLAlchemy."""
        result = self.execute(sql, params)
        return [tuple(r) for r in result.fetchall()]

    def begin_transaction(self) -> None:
        """Begin a SQLAlchemy transaction."""
        self._txn = self._conn.begin()

    def commit(self) -> None:
        """Commit the active SQLAlchemy transaction."""
        if self._txn:
            self._txn.commit()
            self._txn = None

    def rollback(self) -> None:
        """Roll back the active SQLAlchemy transaction."""
        if self._txn:
            self._txn.rollback()
            self._txn = None

    def close(self) -> None:
        """Close the SQLAlchemy connection."""
        self._conn.close()
        self._engine.dispose()


# ---------------------------------------------------------------------------
# Factory — creates the appropriate backend from configuration
# ---------------------------------------------------------------------------

def create_repository_backend(
    backend_type: Optional[str] = None,
    db_path: Optional[str] = None,
    connection_url: Optional[str] = None,
) -> DuckDBBackend | ORMBackend:
    """Factory function to create the appropriate repository backend.

    When called with no arguments the backend type, DB path, and
    connection URL are resolved centrally via
    :func:`semabridge.core.db_resolver.resolve_db_config`.  Explicit
    arguments take precedence.

    Args:
        backend_type: ``"duckdb"`` or ``"orm"``.  Defaults to the
            value from global config / env.
        db_path: Path to the DuckDB file (used when backend == duckdb).
        connection_url: SQLAlchemy connection string (used when
            backend == orm).  **Prefer** setting ``DATABASE_URL`` or
            ``database.connection_url_env`` in the global config instead.

    Returns:
        A configured backend instance.

    Raises:
        ValueError: If required parameters are missing for the chosen backend.
    """
    from semabridge.core.db_resolver import resolve_db_config

    # Infer backend from explicit args when backend_type is not given:
    # - db_path provided        → duckdb
    # - connection_url provided → orm
    inferred_type = backend_type
    if inferred_type is None:
        if db_path is not None:
            inferred_type = "duckdb"
        elif connection_url is not None:
            inferred_type = "orm"

    # --- Short-circuit for ORM when connection_url is provided explicitly ---
    # resolve_db_config would raise ValueError if the backend is 'orm' but no
    # env-var is set, even though the caller gave us a URL directly.
    if inferred_type == "orm" and connection_url:
        return ORMBackend(connection_url)

    # --- Short-circuit for unknown type (give a clear error before resolver) ---
    if inferred_type is not None and inferred_type not in {"duckdb", "orm", "sqlite"}:
        raise ValueError(
            f"Unknown backend_type '{backend_type}'. "
            "Supported values: 'duckdb', 'orm', 'sqlite'."
        )
    # Normalise 'sqlite' alias to 'orm'
    if inferred_type == "sqlite":
        inferred_type = "orm"

    cfg = resolve_db_config(
        backend_override=inferred_type,
        db_path_override=db_path,
    )
    resolved_type = inferred_type or cfg.backend

    if resolved_type == "duckdb":
        resolved_path = db_path or cfg.db_path
        if not resolved_path:
            resolved_path = str(Path.home() / ".semabridge" / "semabridge_state.db")
        return DuckDBBackend(resolved_path)

    elif resolved_type == "orm":
        resolved_url = connection_url or cfg.connection_url
        if not resolved_url:
            raise ValueError(
                "ORM backend requires a connection URL. "
                "Set DATABASE_URL or database.connection_url_env in "
                "~/.semabridge/config.yaml."
            )
        return ORMBackend(resolved_url)

    else:
        raise ValueError(
            f"Unknown backend_type '{backend_type}'. "
            "Supported values: 'duckdb', 'orm'."
        )


# ---------------------------------------------------------------------------
# SQLAlchemy 2.0 ORM Factory — setup_database()
# ---------------------------------------------------------------------------

def _default_sqlite_url() -> str:
    """Return the default DuckDB connection URL.

    Always returns a DuckDB URL using ``duckdb://``.  Creates
    ``~/.semabridge/`` if it does not exist.  The name is kept for
    backward compatibility — this function never falls back to SQLite.
    """
    semabridge_dir = Path.home() / ".semabridge"
    semabridge_dir.mkdir(parents=True, exist_ok=True)
    db_file = semabridge_dir / "semabridge_state.db"
    return f"duckdb:///{db_file}"


# Keep old name as alias for backward compatibility
_default_duckdb_url = _default_sqlite_url


def setup_database(
    url: Optional[str] = None,
) -> "Tuple[Engine, sessionmaker[Session]]":
    """Create a SQLAlchemy 2.0 Engine + sessionmaker from a database URL.

    Resolution order for the connection URL:

    1. Explicit *url* argument (highest priority).
    2. ``DATABASE_URL`` environment variable.
    3. Local DuckDB file at ``~/.semabridge/app.duckdb`` (auto-created).

    The function returns a ``(engine, SessionLocal)`` tuple.  Callers
    should run ``Base.metadata.create_all(bind=engine)`` once to emit
    DDL, then use ``SessionLocal()`` as a context manager for sessions.

    Args:
        url: Optional connection string.  When *None*, the env-var /
             DuckDB fallback chain is used.

    Returns:
        A 2-tuple of ``(Engine, sessionmaker[Session])``.

    Raises:
        ImportError: If ``sqlalchemy`` (and ``duckdb_engine`` for the
                     DuckDB driver) is not installed.

    Examples::

        # DuckDB fallback (no env var set):
        engine, Session = setup_database()

        # Explicit Postgres:
        engine, Session = setup_database("postgresql://user:pw@host/db")

        # Via environment variable:
        #   export DATABASE_URL=mysql+pymysql://user:pw@host/db
        engine, Session = setup_database()
    """
    import warnings
    warnings.warn(
        "setup_database() is deprecated and will be removed in a future release. "
        "Use semabridge.repository.orm.session_factory.db_manager instead: "
        "  engine = db_manager.get_engine(url)  "
        "  factory = db_manager.get_session_factory(url)",
        DeprecationWarning,
        stacklevel=2,
    )

    from semabridge.repository.orm.session_factory import db_manager as _dm
    from sqlalchemy.orm import sessionmaker

    resolved_url = url or None  # DatabaseManager.create_db_engine handles None via fetch
    if resolved_url is None:
        resolved_url = _dm.fetch_latest_database_url()

    engine = _dm.create_db_engine(resolved_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory

