"""
Centralized Database URL Resolver.

Single source of truth for determining the active SQLAlchemy connection URL.
Every module that needs a database connection imports from here instead of
reading env-vars directly, so that runtime rotation (URL changes without
restart) is handled in one place.

Resolution Order
----------------
1. Explicit ``url_override`` argument (highest priority — tests only).
2. ``SEMABRIDGE_DATABASE_URL`` env-var.
3. ``DATABASE_URL`` env-var (legacy alias).
4. Global ``~/.semabridge/config.yaml`` → ``database.connection_url_env``
   pointing at a custom env-var name, then its value.
5. Sensible default: DuckDB at ``~/.semabridge/semabridge_state.db``.

The old ``SEMABRIDGE_DB_BACKEND`` env-var (``"duckdb"`` / ``"orm"``) is fully
deprecated.  The dialect is auto-derived from the URL scheme, so switching
backends is as simple as updating the connection URL.

Backward Compatibility
----------------------
``DBConfig`` retains its ``backend`` and ``db_path`` fields populated from the
URL so existing callers that read ``cfg.backend`` or ``cfg.db_path`` continue
to work without modification.  New code should use ``cfg.connection_url`` only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Load .env eagerly so SEMABRIDGE_DATABASE_URL is always available,
# regardless of whether main.py or alembic env.py has run first.
# override=False means already-set OS env vars are never overwritten.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).parents[3] / ".env", override=False)
except ImportError:
    pass  # python-dotenv is optional; rely on OS env vars when absent.

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

_GLOBAL_CONFIG_PATH = Path.home() / ".semabridge" / "config.yaml"

# In-process dialects use StaticPool — no external server, no SSL
_IN_PROCESS_DIALECTS: frozenset[str] = frozenset({"sqlite", "duckdb"})


@dataclass(frozen=True)
class DBConfig:
    """Resolved database configuration.

    Attributes:
        connection_url:     SQLAlchemy connection URL (always set).
        dialect:            Lowercase dialect name (e.g. ``"sqlite"``, ``"postgresql"``).
        backend:            ``"duckdb"`` for in-process dialects, ``"orm"`` for
                            server dialects.  Kept for backward compatibility —
                            prefer ``dialect`` for new code.
        db_path:            Filesystem path when dialect is ``"duckdb"``.
                            ``None`` for server dialects.
        connection_url_env: Name of the env-var that supplied the URL (informational).
    """

    connection_url: str
    dialect: str = "duckdb"
    backend: str = "orm"
    db_path: Optional[str] = None
    connection_url_env: Optional[str] = None


# Internal cache so we don't re-parse YAML on every call
_cached_config: Optional[DBConfig] = None


def resolve_db_config(
    *,
    backend_override: Optional[str] = None,
    db_path_override: Optional[str] = None,
    url_override: Optional[str] = None,
) -> DBConfig:
    """Resolve the effective database configuration.

    Args:
        backend_override:   Legacy parameter — kept for backward compat.
                            Ignored unless ``db_path_override`` is also set
                            with value ``"duckdb"``, in which case a
                            ``duckdb:///`` URL is constructed.
        db_path_override:   Force a specific DuckDB file path (legacy).
        url_override:       Force a specific SQLAlchemy URL (highest priority).

    Returns:
        A frozen ``DBConfig`` dataclass.

    Raises:
        ValueError: If the resolved URL is empty or unresolvable.
    """
    global _cached_config

    # Fast-path: return cache when no overrides are given
    if (
        _cached_config is not None
        and backend_override is None
        and db_path_override is None
        and url_override is None
    ):
        return _cached_config

    # --- Step 1: explicit url_override (test injection) ------------------
    if url_override:
        return _build_config(url_override, env_name=None, cache=False)

    # --- Step 2: legacy db_path_override → duckdb:/// URL ----------------
    if db_path_override:
        resolved_path = str(Path(db_path_override).expanduser().resolve())
        return _build_config(
            f"duckdb:///{resolved_path}",
            env_name="(db_path_override)",
            cache=False,
        )

    # --- Step 3: environment variables -----------------------------------
    env_url = (
        os.environ.get("SEMABRIDGE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    env_name: Optional[str] = None
    if "SEMABRIDGE_DATABASE_URL" in os.environ:
        env_name = "SEMABRIDGE_DATABASE_URL"
    elif "DATABASE_URL" in os.environ:
        env_name = "DATABASE_URL"

    if env_url:
        cfg = _build_config(env_url, env_name=env_name, cache=True)
        return cfg

    # --- Step 4: config.yaml → connection_url_env ------------------------
    yaml_cfg = _load_database_section()
    custom_env = yaml_cfg.get("connection_url_env")
    if custom_env:
        custom_url = os.environ.get(custom_env)
        if custom_url:
            cfg = _build_config(custom_url, env_name=custom_env, cache=True)
            return cfg

    # Legacy config.yaml backend/db_path entries
    yaml_db_path = yaml_cfg.get("db_path")
    yaml_backend = yaml_cfg.get("backend", "")
    if yaml_db_path and yaml_backend in ("duckdb", ""):
        resolved_path = str(Path(yaml_db_path).expanduser().resolve())
        cfg = _build_config(
            f"duckdb:///{resolved_path}",
            env_name="config.yaml",
            cache=True,
        )
        return cfg

    # --- Step 5: ultimate fallback → DuckDB at ~/.semabridge/ ------------
    sema_dir = Path.home() / ".semabridge"
    sema_dir.mkdir(parents=True, exist_ok=True)
    fallback_path = sema_dir / "semabridge_state.db"
    fallback_url = f"duckdb:///{fallback_path}"
    logger.warning(
        "No SEMABRIDGE_DATABASE_URL set; falling back to %s", fallback_url
    )
    cfg = _build_config(fallback_url, env_name=None, cache=True)
    return cfg


def _build_config(
    url: str,
    *,
    env_name: Optional[str],
    cache: bool,
) -> DBConfig:
    """Build a ``DBConfig`` from a URL string and optionally cache it."""
    global _cached_config

    dialect = _dialect_of(url)
    db_path: Optional[str] = None

    # Extract and normalise filesystem path for DuckDB URLs
    if dialect == "duckdb" and url.startswith("duckdb:///"):
        extracted = url[len("duckdb:///"):]
        if extracted:
            db_path = str(Path(extracted).expanduser().resolve())
            url = f"duckdb:///{db_path}"

    # Legacy backend field (in-process dbs → "duckdb", server dbs → "orm")
    backend = "duckdb" if dialect in _IN_PROCESS_DIALECTS else "orm"

    logger.debug(
        "db_resolver: dialect=%s backend=%s env=%s", dialect, backend, env_name
    )

    cfg = DBConfig(
        connection_url=url,
        dialect=dialect,
        backend=backend,
        db_path=db_path,
        connection_url_env=env_name,
    )
    if cache:
        _cached_config = cfg
    return cfg


def _dialect_of(url: str) -> str:
    """Extract the lowercase dialect name from a SQLAlchemy URL."""
    if "://" not in url:
        return "unknown"
    return url.split("://")[0].split("+")[0].lower()


def clear_db_config_cache() -> None:
    """Invalidate the cached config.

    Call after changing ``SEMABRIDGE_DATABASE_URL`` at runtime (e.g. after
    ``semabridge init`` or a credentials rotation).
    """
    global _cached_config
    _cached_config = None


def get_default_db_path() -> str:
    """Return the resolved DuckDB filesystem path (convenience wrapper).

    If the active backend is a server dialect (PostgreSQL, MySQL, …) this
    still returns the default DuckDB path so legacy subsystems that have not
    been migrated from raw ``duckdb.connect()`` continue to operate.  New
    code should call :func:`resolve_db_config` and use
    ``cfg.connection_url`` directly.
    """
    try:
        cfg = resolve_db_config()
        if cfg.db_path:
            return cfg.db_path
    except ValueError:
        pass

    sema_dir = Path.home() / ".semabridge"
    sema_dir.mkdir(parents=True, exist_ok=True)
    return str(sema_dir / "semabridge_state.db")


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _load_database_section() -> dict:
    """Parse only the ``database`` section from global config.yaml."""
    if not _GLOBAL_CONFIG_PATH.exists():
        return {}
    try:
        import yaml

        text = _GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data.get("database", {})
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not read database section from global config", exc_info=True
        )
    return {}
