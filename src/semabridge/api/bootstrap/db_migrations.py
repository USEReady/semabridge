"""Database bootstrap functions run at application startup."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger('semabridge.api')


def _get_column_names(engine, table_name: str) -> set[str]:
    """Return the column names for *table_name* using information_schema.

    Uses information_schema.columns instead of SQLAlchemy's inspector
    reflection, which fires pg_catalog.pg_collation queries that DuckDB
    does not support.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :tbl"
            ),
            {"tbl": table_name},
        ).fetchall()
    return {row[0] for row in rows}


def _table_exists(engine, table_name: str) -> bool:
    """Check whether *table_name* exists using information_schema."""
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :tbl LIMIT 1"
            ),
            {"tbl": table_name},
        ).fetchone()
    return row is not None


def _apply_schema_compatibility_fixes() -> None:
    """Apply lightweight column-level compatibility fixes for existing DBs.

    ``create_all()`` only creates missing tables; it does not add new columns to
    existing tables. Older repositories may therefore miss recently introduced
    columns and fail at runtime when ORM models select them.
    """
    from sqlalchemy import text

    from semabridge.repository.orm.session_factory import get_engine
    from semabridge.repository.schema_compat import widen_project_id_columns

    engine = get_engine()

    dialect = engine.dialect.name
    if dialect == "postgresql":
        expires_type = "TIMESTAMP WITH TIME ZONE"
    elif dialect == "duckdb":
        expires_type = "TIMESTAMPTZ"
    else:
        expires_type = "TIMESTAMP"

    pending_alters: list[str] = []

    if _table_exists(engine, "accounts"):
        existing_columns = _get_column_names(engine, "accounts")
        if "refresh_token" not in existing_columns:
            pending_alters.append("ALTER TABLE accounts ADD COLUMN refresh_token TEXT")
        if "token_expires_at" not in existing_columns:
            pending_alters.append(
                f"ALTER TABLE accounts ADD COLUMN token_expires_at {expires_type}"
            )
        if "auth_type" not in existing_columns:
            pending_alters.append("ALTER TABLE accounts ADD COLUMN auth_type VARCHAR(50)")
        if "owner_id" not in existing_columns:
            pending_alters.append("ALTER TABLE accounts ADD COLUMN owner_id INTEGER")

    if _table_exists(engine, "projects"):
        project_columns = _get_column_names(engine, "projects")
        if "account_id" not in project_columns:
            if dialect == "postgresql":
                pending_alters.append("ALTER TABLE projects ADD COLUMN account_id VARCHAR(36)")
            elif dialect == "duckdb":
                pending_alters.append("ALTER TABLE projects ADD COLUMN account_id VARCHAR")
            else:
                pending_alters.append("ALTER TABLE projects ADD COLUMN account_id TEXT")
        if "connection_tag" not in project_columns:
            pending_alters.append("ALTER TABLE projects ADD COLUMN connection_tag VARCHAR(255)")
        if "notification_email" not in project_columns:
            pending_alters.append("ALTER TABLE projects ADD COLUMN notification_email VARCHAR(255)")

    if _table_exists(engine, "snapshots"):
        snapshot_columns = _get_column_names(engine, "snapshots")
        if "deleted_at" not in snapshot_columns:
            pending_alters.append(f"ALTER TABLE snapshots ADD COLUMN deleted_at {expires_type}")
        if "sync_mode" not in snapshot_columns:
            pending_alters.append("ALTER TABLE snapshots ADD COLUMN sync_mode VARCHAR(20) NOT NULL DEFAULT 'copy'")

    if _table_exists(engine, "model_versions"):
        mv_columns = _get_column_names(engine, "model_versions")
        if "deleted_at" not in mv_columns:
            pending_alters.append(f"ALTER TABLE model_versions ADD COLUMN deleted_at {expires_type}")

    if _table_exists(engine, "runs"):
        run_columns = _get_column_names(engine, "runs")
        if "run_type" not in run_columns:
            pending_alters.append("ALTER TABLE runs ADD COLUMN run_type VARCHAR(50)")
        if "sync_mode" not in run_columns:
            pending_alters.append("ALTER TABLE runs ADD COLUMN sync_mode VARCHAR(20) NOT NULL DEFAULT 'copy'")
        if "before_src_snapshot_id" not in run_columns:
            pending_alters.append("ALTER TABLE runs ADD COLUMN before_src_snapshot_id VARCHAR(36)")
        if "restored_from_snapshot_id" not in run_columns:
            pending_alters.append("ALTER TABLE runs ADD COLUMN restored_from_snapshot_id VARCHAR(36)")
        if "restore_snapshot_id" not in run_columns:
            pending_alters.append("ALTER TABLE runs ADD COLUMN restore_snapshot_id VARCHAR(36)")
        if "before_target_snapshot_ids" not in run_columns:
            pending_alters.append("ALTER TABLE runs ADD COLUMN before_target_snapshot_ids TEXT")
        if "after_target_snapshot_ids" not in run_columns:
            pending_alters.append("ALTER TABLE runs ADD COLUMN after_target_snapshot_ids TEXT")

    # In PostgreSQL, migrate the unique constraint from `tag` to `(owner_id, tag)`
    if dialect == "postgresql":
        from sqlalchemy import inspect
        try:
            inspector = inspect(engine)
            constraints = inspector.get_unique_constraints("accounts")
            constraint_names = {c.get("name") for c in constraints if c.get("name")}
            if "accounts_tag_key" in constraint_names:
                pending_alters.append("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_tag_key CASCADE")
            if "uq_account_owner_tag" not in constraint_names:
                pending_alters.append("ALTER TABLE accounts ADD CONSTRAINT uq_account_owner_tag UNIQUE (owner_id, tag)")
        except Exception as e:
            logger.warning("Could not inspect constraints on accounts table: %s", e)

    with engine.begin() as conn:
        widened = widen_project_id_columns(conn)
        if not pending_alters and not widened:
            return
        for ddl in pending_alters:
            conn.execute(text(ddl))
        # Backfill ORM canonical column from legacy column when both exist.
        if _table_exists(engine, "runs"):
            run_columns = _get_column_names(engine, "runs")
            if "restored_from_snapshot_id" in run_columns and "restore_snapshot_id" in run_columns:
                conn.execute(
                    text(
                        "UPDATE runs "
                        "SET restored_from_snapshot_id = restore_snapshot_id "
                        "WHERE restored_from_snapshot_id IS NULL AND restore_snapshot_id IS NOT NULL"
                    )
                )

    logger.info("Applied accounts schema compatibility fixes: %s", ", ".join(pending_alters))


def _migrate_credentials_table() -> None:
    """Migrate semabridge_credentials to user-scoped composite PK (idempotent).

    Old schema: PRIMARY KEY (service, key)  — global, shared by all users.
    New schema: PRIMARY KEY (owner_id, service, key)  — sentinel 0 = global.

    Safe to run multiple times. Checks for the ``owner_id`` column before
    executing any DDL so that subsequent restarts are a pure no-op.
    """
    from sqlalchemy import text
    from semabridge.repository.orm.session_factory import get_engine

    engine = get_engine()
    if engine.dialect.name not in ("postgresql", "duckdb", "sqlite"):
        logger.debug("Credentials migration skipped for dialect: %s", engine.dialect.name)
        return

    if not _table_exists(engine, "semabridge_credentials"):
        # Table doesn't exist yet — create_all() will create it with the new schema.
        return

    existing_cols = _get_column_names(engine, "semabridge_credentials")
    if "owner_id" in existing_cols:
        return  # Already migrated — nothing to do.

    dialect = engine.dialect.name
    logger.info("Migrating semabridge_credentials: adding owner_id to composite PK...")

    ddl_steps: list[str] = []

    if dialect == "postgresql":
        ddl_steps = [
            "ALTER TABLE semabridge_credentials ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE semabridge_credentials DROP CONSTRAINT IF EXISTS semabridge_credentials_pkey",
            "ALTER TABLE semabridge_credentials ADD PRIMARY KEY (owner_id, service, key)",
            "CREATE INDEX IF NOT EXISTS ix_credential_owner_service "
            "ON semabridge_credentials (owner_id, service)",
        ]
    elif dialect == "sqlite":
        ddl_steps = [
            "ALTER TABLE semabridge_credentials RENAME TO _semabridge_credentials_old",
            """
            CREATE TABLE semabridge_credentials (
                owner_id INTEGER NOT NULL DEFAULT 0,
                service  VARCHAR(50)  NOT NULL,
                key      VARCHAR(100) NOT NULL,
                value    TEXT NOT NULL,
                is_secret BOOLEAN NOT NULL DEFAULT 0,
                updated_at TIMESTAMP,
                PRIMARY KEY (owner_id, service, key)
            )
            """,
            """
            INSERT INTO semabridge_credentials (owner_id, service, key, value, is_secret, updated_at)
            SELECT 0, service, key, value, is_secret, updated_at
            FROM _semabridge_credentials_old
            """,
            "CREATE INDEX IF NOT EXISTS ix_credential_owner_service "
            "ON semabridge_credentials (owner_id, service)",
            "DROP TABLE _semabridge_credentials_old",
        ]
    else:
        # DuckDB — same approach as PostgreSQL.
        ddl_steps = [
            "ALTER TABLE semabridge_credentials ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE semabridge_credentials DROP PRIMARY KEY",
            "ALTER TABLE semabridge_credentials ADD PRIMARY KEY (owner_id, service, key)",
        ]

    try:
        with engine.begin() as conn:
            for ddl in ddl_steps:
                conn.execute(text(ddl.strip()))
        logger.info(
            "semabridge_credentials migration complete: PK is now (owner_id, service, key). "
            "Existing rows assigned owner_id=0 (global/system)."
        )
    except Exception as exc:
        logger.warning("semabridge_credentials migration failed (may already be migrated): %s", exc)


def _apply_rls_policies() -> None:
    """Apply PostgreSQL Row Level Security policies on tenant-scoped tables."""
    from sqlalchemy import text
    from semabridge.repository.orm.session_factory import get_engine

    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return  # RLS is PostgreSQL-specific.

    rls_statements = [
        "ALTER TABLE accounts ENABLE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS account_owner_isolation ON accounts",
        """
        CREATE POLICY account_owner_isolation ON accounts
        FOR ALL
        USING (
            current_setting('app.current_user_id', true) = ''
            OR current_setting('app.current_user_id', true) IS NULL
            OR owner_id IS NULL
            OR owner_id = current_setting('app.current_user_id', true)::integer
        )
        """,
        "ALTER TABLE accounts FORCE ROW LEVEL SECURITY",
    ]

    with engine.begin() as conn:
        for stmt in rls_statements:
            conn.execute(text(stmt.strip()))

    logger.info("PostgreSQL RLS policies applied on 'accounts' table.")


def _assign_orphaned_accounts_to_dev_user() -> None:
    """Assign accounts with NULL owner_id to the first active user."""
    if os.environ.get("AUTH_ENABLED", "").lower() == "true":
        return  # Skip in production — admin must assign explicitly.

    from sqlalchemy import select, update
    from semabridge.repository.orm.models import Account, User
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        orphan_count = session.execute(
            select(Account).where(Account.owner_id.is_(None))
        ).scalars().all()

        if not orphan_count:
            return

        dev_user = session.execute(
            select(User).where(User.is_active.is_(True)).order_by(User.id)
        ).scalars().first()

        if not dev_user:
            logger.debug(
                "No active dev user found — %d orphaned accounts remain unassigned.",
                len(orphan_count),
            )
            return

        session.execute(
            update(Account)
            .where(Account.owner_id.is_(None))
            .values(owner_id=dev_user.id)
        )
        session.commit()
        logger.info(
            "Assigned %d orphaned accounts to dev user '%s' (id=%d).",
            len(orphan_count), dev_user.username, dev_user.id,
        )
