"""
SQLAlchemy Engine & Session Factory with Database Rotation Support.

Provides a thread-safe ``DatabaseManager`` that can detect connection URL
changes at runtime, dispose stale engine connections, and recreate a fresh
engine transparently -- enabling zero-downtime database credential rotation.

Primary API (preferred)::

    from semabridge.repository.orm.session_factory import db_manager

    engine = db_manager.get_engine()          # always returns the current engine
    with db_manager.get_session() as session: # per-request scoped session
        ...

Backward-compatible API (still supported)::

    from semabridge.repository.orm.session_factory import get_engine, get_session_factory, reset_engine

    engine = get_engine()                     # delegates to db_manager
    SessionLocal = get_session_factory()      # delegates to db_manager
    reset_engine()                            # delegates to db_manager.reset()

Supported Dialects
------------------
- ``postgresql`` -- via ``psycopg2`` / ``asyncpg``
- ``snowflake``  -- via ``snowflake-sqlalchemy``
- ``duckdb``     -- via ``duckdb-engine``  (optional, backward compat / default fallback)
- ``sqlite``     -- in-memory only, for tests
"""

from __future__ import annotations

import os
import threading
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator, Optional, Tuple

from semabridge.utils.logger import get_logger

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

logger = get_logger(__name__)

# Dialects that use an in-process connection and should use StaticPool instead
# of the standard connection pool (pool_pre_ping has no effect on them either).
_IN_PROCESS_DIALECTS = frozenset({"sqlite", "duckdb"})


def _convert_jdbc_to_sqlalchemy_url(url: str) -> str:
    """Convert JDBC URL format to SQLAlchemy format.
    
    JDBC URLs (e.g., from environment config) need to be converted to SQLAlchemy format
    for SQLAlchemy to parse them correctly.
    
    Example:
        jdbc:snowflake://mzc31913.us-east-1.snowflakecomputing.com/?warehouse=WH&db=DB&schema=SCHEMA
        -> snowflake://user:password@mzc31913.us-east-1/DB/SCHEMA?warehouse=WH
    
    Args:
        url: Database connection URL (JDBC or SQLAlchemy format)
        
    Returns:
        SQLAlchemy-compatible URL string
    """
    if not url.startswith("jdbc:"):
        # Already in SQLAlchemy format
        return url
    
    # Remove 'jdbc:' prefix
    url = url[5:]
    
    # Handle Snowflake specific conversion
    if url.startswith("snowflake://"):
        import os
        from urllib.parse import urlparse, parse_qs, urlencode, quote
        
        try:
            # Parse the JDBC URL
            parsed = urlparse(url)
            
            # Extract query parameters
            query_params = parse_qs(parsed.query)
            
            # Get user and password from environment and URL-encode them
            # to handle special characters like @ in passwords
            user = quote(os.environ.get("SNOWFLAKE_USER", ""), safe="")
            password = quote(os.environ.get("SNOWFLAKE_PASSWORD", ""), safe="")
            
            # Extract database and schema from query params or path
            db = query_params.get("db", [parsed.path.lstrip("/").split("/")[0]])[0] if query_params.get("db") or parsed.path else ""
            schema = query_params.get("schema", ["PUBLIC"])[0] if query_params.get("schema") else "PUBLIC"
            
            # Extract account from netloc and remove .snowflakecomputing.com suffix if present
            account = parsed.netloc
            if account.endswith(".snowflakecomputing.com"):
                account = account.replace(".snowflakecomputing.com", "")
            
            # Reconstruct SQLAlchemy URL
            # Format: snowflake://user:password@account/database/schema?params
            if user and password:
                auth_part = f"{user}:{password}@"
            elif user:
                auth_part = f"{user}@"
            else:
                auth_part = ""
            
            # Build path (database/schema)
            path_part = f"/{db}/{schema}" if db else ""
            
            # Reconstruct query string (remove db and schema as they're in the path now)
            remaining_params = {k: v[0] for k, v in query_params.items() 
                              if k not in ("db", "schema")}
            query_string = f"?{urlencode(remaining_params)}" if remaining_params else ""
            
            new_url = f"snowflake://{auth_part}{account}{path_part}{query_string}"
            logger.info(f"Converted JDBC URL to SQLAlchemy format (redacted for security)")
            return new_url
        except Exception as e:
            logger.error(f"Failed to convert Snowflake JDBC URL: {e}. Falling back to original URL.")
            logger.debug(f"Original URL was: {url}")
            return url
    
    return url


class DatabaseManager:
    """Thread-safe, rotation-aware SQLAlchemy engine & session manager.

    The manager tracks the URL used to build the current engine.  On every
    call to :meth:`get_engine` it fetches the *latest* URL from the
    environment/configuration chain.  When the URL has changed since the
    engine was last created the manager transparently:

    1. Calls ``engine.dispose()`` to close all pooled connections.
    2. Builds a new engine pointing at the new database.
    3. Rebuilds the ``sessionmaker`` bound to the new engine.

    All state mutations are protected by ``threading.Lock`` so the class is
    safe to use from multiple request-handler threads simultaneously.

    Example -- database rotation scenario::

        import os
        from semabridge.repository.orm.session_factory import db_manager

        # Simulate a credential rotation agent updating the env var:
        os.environ["SEMABRIDGE_DATABASE_URL"] = "postgresql://new-host/db"

        # Next call automatically reconnects to the new database:
        engine = db_manager.get_engine()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine: Optional["Engine"] = None
        self._session_factory: Optional["sessionmaker[Session]"] = None
        self._current_url: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_latest_database_url(self) -> str:
        """Fetch the current database URL from configuration / env vars.

        Only invalidates the ``db_resolver`` cache when the active env-var
        value has changed since the last call.  Clearing the cache on every
        request caused the URL to always look "new" to ``get_engine()``,
        triggering a full ``_swap_engine()`` rebuild (200 ms) on every request
        and starving the asyncio thread pool under concurrent startup load.

        Resolution order:

        1. ``SEMABRIDGE_DATABASE_URL`` env var.
        2. ``DATABASE_URL`` env var.
        3. Centralized DB resolver → ``~/.semabridge/config.yaml`` database
           section, then DuckDB fallback at ``~/.semabridge/semabridge_state.db``.

        Returns:
            A valid SQLAlchemy connection URL string.

        Raises:
            RepositoryError: If no URL can be resolved.
        """
        import os as _os
        from semabridge.core.db_resolver import clear_db_config_cache, resolve_db_config

        # Detect runtime env-var changes so credential rotation still works,
        # but avoid clearing the cache on every call (which rebuilds the engine
        # on every request and blocks the asyncio thread pool).
        current_env_url = (
            _os.environ.get("SEMABRIDGE_DATABASE_URL")
            or _os.environ.get("DATABASE_URL")
            or ""
        )
        if current_env_url != self._current_url:
            # URL has changed (credential rotation) — force a re-resolve.
            clear_db_config_cache()

        try:
            cfg = resolve_db_config()
            return cfg.connection_url
        except Exception:  # noqa: BLE001
            pass

        # Absolute fallback — DuckDB at ~/.semabridge/
        from pathlib import Path as _Path
        env_url = _os.environ.get("SEMABRIDGE_DATABASE_URL") or _os.environ.get("DATABASE_URL")
        if env_url:
            return env_url
        sema_dir = _Path.home() / ".semabridge"
        sema_dir.mkdir(parents=True, exist_ok=True)
        fallback = f"duckdb:///{sema_dir / 'semabridge_state.db'}"
        logger.warning(
            "No database URL found in env or config; falling back to %s", fallback
        )
        return fallback

    def create_db_engine(self, url: str) -> "Engine":
        """Create a new SQLAlchemy ``Engine`` for the given URL.

        Configures pool settings from :class:`~semabridge.core.settings.DatabaseConfig`
        and enables ``pool_pre_ping`` so stale connections are detected and
        discarded before use.  In-process dialects (SQLite, DuckDB) use
        ``StaticPool`` instead of the standard connection pool.

        SSL for server databases is configured when ``DB_SSL_MODE`` is set.
        PostgreSQL uses psycopg2 ``connect_args``; MySQL uses PyMySQL
        ``ssl_ca`` / ``ssl_mode`` args.

        Args:
            url: A SQLAlchemy-compatible connection URL (or JDBC format which will be converted).

        Returns:
            A configured ``Engine`` instance.

        Raises:
            ImportError: If ``sqlalchemy`` or the dialect driver is missing.
            RepositoryError: If the engine cannot be created.
        """
        # Convert JDBC URLs to SQLAlchemy format if needed
        url = _convert_jdbc_to_sqlalchemy_url(url)
        
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.pool import StaticPool
        except ImportError as exc:
            raise ImportError(
                "SQLAlchemy >=2.0 is required. Install: pip install 'sqlalchemy>=2.0'"
            ) from exc

        dialect = url.split("://")[0].split("+")[0] if "://" in url else "unknown"
        logger.info("Creating SQLAlchemy engine: %s dialect", dialect)

        # --- Load pool / SSL settings from DatabaseConfig -----------------
        try:
            from semabridge.core.settings import DatabaseConfig
            db_cfg = DatabaseConfig()
        except Exception:  # noqa: BLE001
            db_cfg = None  # type: ignore[assignment]

        pool_size = getattr(db_cfg, "pool_size", 5)
        max_overflow = getattr(db_cfg, "max_overflow", 10)
        pool_recycle = getattr(db_cfg, "pool_recycle", 1800)
        pool_pre_ping = getattr(db_cfg, "pool_pre_ping", True)
        echo = getattr(db_cfg, "echo", False)
        ssl_mode = getattr(db_cfg, "ssl_mode", None)
        ssl_ca = getattr(db_cfg, "ssl_ca", None)

        # MySQL should recycle more aggressively (below default wait_timeout)
        if dialect == "mysql":
            pool_recycle = min(pool_recycle, 3600)

        try:
            if dialect in _IN_PROCESS_DIALECTS:
                # SQLite: must use StaticPool (single-thread file locking).
                # DuckDB: use NullPool — each session gets its own connection.
                #   DuckDB (>=0.8) supports concurrent in-process connections
                #   via its internal WAL; StaticPool forces all threads onto
                #   ONE connection, causing "cannot start a transaction within
                #   a transaction" under FastAPI concurrency.
                if dialect == "sqlite":
                    connect_args: dict = {"check_same_thread": False}
                    engine = create_engine(
                        url,
                        echo=echo,
                        future=True,
                        connect_args=connect_args,
                        poolclass=StaticPool,
                    )
                else:
                    # DuckDB on Windows: exclusive file locking prevents
                    # multiple simultaneous connections (NullPool fails).
                    # StaticPool shares ONE connection across all threads,
                    # causing "transaction within transaction" crashes.
                    #
                    # QueuePool(pool_size=1, max_overflow=0) keeps exactly
                    # one persistent connection and threads queue up to use
                    # it serially — no file-lock conflicts, no shared-state
                    # transaction collisions.
                    from sqlalchemy.pool import QueuePool
                    engine = create_engine(
                        url,
                        echo=echo,
                        future=True,
                        poolclass=QueuePool,
                        pool_size=1,
                        max_overflow=0,
                        pool_timeout=30,
                        pool_pre_ping=True,
                    )
            else:
                # Build SSL connect_args for server dialects
                connect_args = _build_ssl_connect_args(dialect, ssl_mode, ssl_ca)
                engine = create_engine(
                    url,
                    echo=echo,
                    future=True,
                    pool_pre_ping=pool_pre_ping,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_recycle=pool_recycle,
                    connect_args=connect_args,
                )
        except Exception as exc:
            from semabridge.core.exceptions import RepositoryError
            raise RepositoryError(
                "Failed to create database engine",
                details={"url_dialect": dialect, "error": str(exc)},
            ) from exc

        # Apply DuckDB SERIAL->SEQUENCE patch for backward compatibility
        if engine.dialect.name == "duckdb":
            _apply_duckdb_serial_patch(engine)

        return engine

    def get_engine(self, url_override: Optional[str] = None) -> "Engine":
        """Return a live, up-to-date SQLAlchemy ``Engine``.

        On each call the current database URL is fetched via
        :meth:`fetch_latest_database_url`.  If the URL has changed since the
        last engine was built, the old engine is disposed and a new one is
        created atomically under a ``threading.Lock``.

        Args:
            url_override: Force a specific URL (used by tests). When set
                the rotation-detection logic is skipped.

        Returns:
            The current ``Engine`` instance.
        """
        # Fast path for tests / explicit overrides -- bypass rotation checks.
        if url_override is not None:
            with self._lock:
                if self._current_url != url_override or self._engine is None:
                    self._swap_engine(url_override)
            return self._engine  # type: ignore[return-value]

        latest_url = self.fetch_latest_database_url()

        with self._lock:
            if self._engine is None or self._current_url != latest_url:
                self._swap_engine(latest_url)

        return self._engine  # type: ignore[return-value]

    def get_session_factory(self, url_override: Optional[str] = None) -> "sessionmaker[Session]":
        """Return the current ``sessionmaker`` bound to the active engine.

        Ensures the session factory always reflects the most recent engine
        after a rotation event.

        Args:
            url_override: Forwarded to :meth:`get_engine`.

        Returns:
            A ``sessionmaker[Session]`` instance.
        """
        self.get_engine(url_override)  # ensures _session_factory is up-to-date
        return self._session_factory  # type: ignore[return-value]

    def _session(self, url_override: Optional[str] = None) -> "Session":
        """Return a new ``Session`` for legacy callers.

        Some compatibility services still manage commit/rollback/close
        themselves and historically called ``_session()`` directly. Keep that
        contract while routing through the rotation-aware session factory.
        """
        return self.get_session_factory(url_override)()

    @contextmanager
    def get_session(self, url_override: Optional[str] = None) -> Generator["Session", None, None]:
        """Yield a scoped ``Session`` with automatic commit / rollback.

        The session is **committed** when the ``with`` block exits normally.
        If an exception is raised the session is **rolled back** before
        re-raising, and always **closed** in the ``finally`` block so the
        connection is returned to the pool.

        Intended for use as a FastAPI dependency::

            from semabridge.repository.orm.session_factory import db_manager

            def get_db() -> Generator[Session, None, None]:
                with db_manager.get_session() as session:
                    yield session

        Args:
            url_override: Forwarded to :meth:`get_session_factory`.

        Yields:
            An open ``Session`` object.
        """
        factory = self.get_session_factory(url_override)
        session: "Session" = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Dispose the current engine and release all pooled connections.

        Call this during application shutdown to cleanly close open
        database connections before exiting.
        """
        with self._lock:
            if self._engine is not None:
                logger.info(
                    "Disposing database engine (%s)", self._current_url or "unknown"
                )
                self._engine.dispose()
                self._engine = None
                self._session_factory = None
                self._current_url = None

    def reset(self) -> None:
        """Dispose the engine and clear all state.

        Equivalent to :meth:`dispose` but also clears the db_resolver
        cache so the next :meth:`get_engine` call performs a full
        re-resolution.  Primarily used in tests.
        """
        self.dispose()
        try:
            from semabridge.core.db_resolver import clear_db_config_cache
            clear_db_config_cache()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _swap_engine(self, url: str) -> None:
        """Replace the current engine with a new one for *url*.

        **Must** be called with ``self._lock`` held.
        """
        old_engine = self._engine
        old_url = self._current_url

        new_engine = self.create_db_engine(url)

        from sqlalchemy.orm import sessionmaker
        new_factory: "sessionmaker[Session]" = sessionmaker(
            bind=new_engine, expire_on_commit=False
        )

        self._engine = new_engine
        self._session_factory = new_factory
        self._current_url = url

        # Dispose old engine *after* the new one is live so there is no gap.
        if old_engine is not None and old_url != url:
            logger.info(
                "Database rotation detected -- disposing old engine (dialect: %s)",
                old_engine.dialect.name,
            )
            try:
                old_engine.dispose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error disposing old engine: %s", exc)


# ------------------------------------------------------------------
# Module-level singleton -- one per process
# ------------------------------------------------------------------

#: Process-wide ``DatabaseManager`` singleton.  Import and use this directly
#: for the most explicit API.
db_manager: DatabaseManager = DatabaseManager()


# ------------------------------------------------------------------
# Backward-compatible functional API (delegates to ``db_manager``)
# ------------------------------------------------------------------

def get_engine(url_override: Optional[str] = None) -> "Engine":
    """Return the current SQLAlchemy ``Engine`` (backward-compat wrapper).

    Delegates to :meth:`DatabaseManager.get_engine` on the module-level
    :data:`db_manager` singleton.  The rotation-detection logic runs on
    every call.

    Args:
        url_override: Force a specific URL (mainly for tests).

    Returns:
        A configured ``Engine`` instance.
    """
    return db_manager.get_engine(url_override)


def get_session_factory(url_override: Optional[str] = None) -> "sessionmaker[Session]":
    """Return the current ``sessionmaker`` (backward-compat wrapper).

    Delegates to :meth:`DatabaseManager.get_session_factory` on the
    module-level :data:`db_manager` singleton.

    Args:
        url_override: Forwarded to :func:`get_engine`.

    Returns:
        A ``sessionmaker`` instance.
    """
    return db_manager.get_session_factory(url_override)


def reset_engine() -> None:
    """Dispose engine & clear all caches (backward-compat wrapper).

    Delegates to :meth:`DatabaseManager.reset` on the module-level
    :data:`db_manager` singleton.  Primarily used in tests and after
    ``semabridge init``.
    """
    db_manager.reset()


# ------------------------------------------------------------------
# Private helpers (module-level, shared across engines of same dialect)
# ------------------------------------------------------------------

def _resolve_url() -> str:
    """Walk the resolution chain and return a connection URL string.

    .. deprecated::
        Use :meth:`DatabaseManager.fetch_latest_database_url` instead.
        This function is retained for any code that imported it directly.
    """
    warnings.warn(
        "_resolve_url() is deprecated. Use db_manager.fetch_latest_database_url() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return db_manager.fetch_latest_database_url()


def _build_ssl_connect_args(
    dialect: str,
    ssl_mode: Optional[str],
    ssl_ca: Optional[str],
) -> dict:
    """Build ``connect_args`` for SSL-enabled server dialects.

    Args:
        dialect:  Lowercase dialect name (``"postgresql"``, ``"mysql"``, …).
        ssl_mode: Dialect-specific SSL mode string, or ``None`` to skip SSL.
        ssl_ca:   Path to CA certificate file, or ``None``.

    Returns:
        A ``dict`` suitable for passing as ``connect_args`` to
        ``create_engine``.  Empty dict when SSL is not requested.
    """
    if not ssl_mode:
        return {}

    if dialect == "postgresql":
        args: dict = {"sslmode": ssl_mode}
        if ssl_ca:
            args["sslrootcert"] = ssl_ca
        return args

    if dialect == "mysql":
        args = {"ssl_mode": ssl_mode.upper()}
        if ssl_ca:
            args["ssl_ca"] = ssl_ca
        return args

    # Other dialects: pass through generic SSL options
    if ssl_ca:
        return {"ssl_ca": ssl_ca}
    return {}


def _apply_duckdb_serial_patch(engine: "Engine") -> None:
    """Rewrite ``SERIAL`` / ``BIGSERIAL`` in DDL to DuckDB sequences."""
    import re as _re

    from sqlalchemy import event as _event

    @_event.listens_for(engine, "before_cursor_execute", retval=True)
    def _patch_serial(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> Tuple[str, Any]:
        if "SERIAL" not in statement.upper():
            return statement, parameters

        tbl = _re.search(r"CREATE\s+TABLE\s+(\w+)", statement, _re.I)
        if tbl:
            table_name = tbl.group(1)

            def _repl(m: "_re.Match[str]") -> str:
                col = m.group(1)
                kw = m.group(2).upper()
                seq = f"{table_name}_{col}_seq"
                int_type = "BIGINT" if kw == "BIGSERIAL" else "INTEGER"
                try:
                    cursor.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq}")
                except Exception:  # noqa: BLE001
                    pass
                return f"{col} {int_type} DEFAULT nextval('{seq}')"

            statement = _re.sub(
                r"(\w+)\s+((?:BIG)?SERIAL)",
                _repl,
                statement,
                flags=_re.I,
            )
        else:
            statement = statement.replace("BIGSERIAL", "BIGINT")
            statement = statement.replace("SERIAL", "INTEGER")

        return statement, parameters
