"""
Alembic environment configuration for SemaBridge.

Resolves the database URL from ``SEMABRIDGE_DATABASE_URL`` (via
:class:`~semabridge.core.db_resolver`) and exposes the full
``Base.metadata`` for autogenerate support, covering all ORM models
including the cache tables.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Load .env so SEMABRIDGE_DATABASE_URL is available when running
# `alembic upgrade head` directly from the terminal.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[3] / ".env", override=False)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Alembic config object
# ---------------------------------------------------------------------------
config = context.config

# Logging setup via alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import all ORM models so that Base.metadata is fully populated.
# Alembic uses this for autogenerate (alembic revision --autogenerate).
# ---------------------------------------------------------------------------
from semabridge.repository.orm.base import Base  # noqa: E402

# Explicitly import every model module so their tables are registered in Base.
import semabridge.repository.orm.models  # noqa: E402, F401
import semabridge.repository.orm.cache_models  # noqa: E402, F401

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------

def _get_url() -> str:
    """Return the database URL at migration time.

    Priority:
    1. ``SEMABRIDGE_DATABASE_URL`` env var (set by caller / CI)
    2. ``DATABASE_URL`` env var (legacy)
    3. Fallback: resolve via :func:`~semabridge.core.db_resolver.resolve_db_config`
    """
    url = (
        os.environ.get("SEMABRIDGE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if url:
        return url

    try:
        from semabridge.core.db_resolver import resolve_db_config
        return resolve_db_config().connection_url
    except Exception:
        raise RuntimeError(
            "Cannot determine database URL for Alembic migration. "
            "Set SEMABRIDGE_DATABASE_URL environment variable."
        )


# ---------------------------------------------------------------------------
# Offline migrations (emit SQL to stdout without a live connection)
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations in offline mode (SQL output, no live connection)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE support
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations (run against a live database connection)
# ---------------------------------------------------------------------------

def run_migrations_online() -> None:
    """Run migrations in online mode (live database connection).

    DuckDB is not supported by Alembic's DDL layer.  When DuckDB is
    configured we fall back to ``Base.metadata.create_all()`` which
    issues ``CREATE TABLE IF NOT EXISTS`` for every mapped model.
    """
    url = _get_url()

    # DuckDB: Alembic has no DDL implementation for it (KeyError: 'duckdb').
    # Use SQLAlchemy's create_all() directly instead.
    if url.startswith("duckdb"):
        from sqlalchemy import create_engine
        engine = create_engine(url)
        target_metadata.create_all(engine)
        print("DuckDB: schema created via Base.metadata.create_all() (Alembic not supported for DuckDB)")
        return

    # Override the ini-file URL with the runtime-resolved one.
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER TABLE support
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

