"""Run the Alembic chain (``migrations/schema_versions``) at application startup.

* PostgreSQL only -- SQLite (tests) and DuckDB keep ``Base.metadata.create_all``.
* A database without ``alembic_version`` that already has the app's tables
  (built before Alembic was adopted) is stamped at ``sb0001_baseline`` first;
  an empty database runs the baseline itself.
* Then ``upgrade head``. A PostgreSQL advisory lock serializes concurrent
  starters (e.g. uvicorn reload + a CLI).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("semabridge.api.migrations")

BASELINE_REVISION = "sb0001_baseline"
_ADVISORY_LOCK_KEY = 72_910_331  # arbitrary, app-wide constant


def _alembic_config(connection: Any):
    from alembic.config import Config

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("version_locations", str(migrations_dir / "schema_versions"))
    cfg.set_main_option("path_separator", "os")
    cfg.attributes["connection"] = connection
    cfg.attributes["skip_logging_config"] = True
    return cfg


def current_revision(connection: Any) -> str | None:
    from alembic.runtime.migration import MigrationContext

    return MigrationContext.configure(connection).get_current_revision()


def upgrade_database(engine: Any) -> Dict[str, Any]:
    """Stamp (if needed) and upgrade the database to head. Returns a report."""
    from alembic import command
    from sqlalchemy import inspect, text

    report: Dict[str, Any] = {"dialect": engine.dialect.name, "stamped": None, "before": None, "after": None}
    if engine.dialect.name != "postgresql":
        report["skipped"] = "alembic runs on PostgreSQL only"
        return report

    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
        cfg = _alembic_config(conn)
        tables = set(inspect(conn).get_table_names())
        before = current_revision(conn) if "alembic_version" in tables else None
        report["before"] = before
        if before is None and "projects" in tables:
            command.stamp(cfg, BASELINE_REVISION)
            report["stamped"] = BASELINE_REVISION
            logger.info("Existing database stamped at Alembic baseline %s", BASELINE_REVISION)
        command.upgrade(cfg, "head")
        report["after"] = current_revision(conn)
    if report["after"] != report["before"]:
        logger.info("Database migrated: %s -> %s", report["before"], report["after"])
    return report
