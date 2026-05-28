from __future__ import annotations

import asyncio
import logging
import os
import time
import contextlib
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from semabridge.api.services.connection_api_service import _get_msal_app, _last_poll_time, _poll_sessions, _poll_sessions_lock
from semabridge.api.services.project_api_service import (
    _compat_clear_project_schedule,
    _compat_ensure_loaded,
    _compat_project_schedules,
    _execute_project_run,
    scheduler_service,
)
from semabridge.api.websocket_alerts import install_websocket_alert_handler
from semabridge.core.env import load_repo_dotenv
from semabridge.utils.logger import setup_logging

try:
    from semabridge.auth.middleware import AuthMiddleware
except ImportError:
    AuthMiddleware = None  # type: ignore[assignment]

load_repo_dotenv()

if os.name == 'nt':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

setup_logging(level='INFO')
install_websocket_alert_handler()
logger = logging.getLogger('semabridge.api')


def _apply_schema_compatibility_fixes() -> None:
    """Apply lightweight column-level compatibility fixes for existing DBs.

    ``create_all()`` only creates missing tables; it does not add new columns to
    existing tables. Older repositories may therefore miss recently introduced
    columns and fail at runtime when ORM models select them.
    """
    from sqlalchemy import inspect, text

    from semabridge.repository.orm.session_factory import get_engine
    from semabridge.repository.schema_compat import widen_project_id_columns

    engine = get_engine()
    inspector = inspect(engine)

    dialect = engine.dialect.name
    if dialect == "postgresql":
        expires_type = "TIMESTAMP WITH TIME ZONE"
    elif dialect == "duckdb":
        expires_type = "TIMESTAMPTZ"
    else:
        # SQLite and generic fallback
        expires_type = "TIMESTAMP"

    pending_alters: list[str] = []

    if "accounts" in set(inspector.get_table_names()):
        existing_columns = {col["name"] for col in inspector.get_columns("accounts")}
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

    if "projects" in set(inspector.get_table_names()):
        project_columns = {col["name"] for col in inspector.get_columns("projects")}
        if "account_id" not in project_columns:
            if dialect == "postgresql":
                pending_alters.append("ALTER TABLE projects ADD COLUMN account_id VARCHAR(36)")
            elif dialect == "duckdb":
                pending_alters.append("ALTER TABLE projects ADD COLUMN account_id VARCHAR")
            else:
                pending_alters.append("ALTER TABLE projects ADD COLUMN account_id TEXT")
        if "connection_tag" not in project_columns:
            pending_alters.append("ALTER TABLE projects ADD COLUMN connection_tag VARCHAR(255)")

    if "snapshots" in set(inspector.get_table_names()):
        snapshot_columns = {col["name"] for col in inspector.get_columns("snapshots")}
        if "deleted_at" not in snapshot_columns:
            pending_alters.append(f"ALTER TABLE snapshots ADD COLUMN deleted_at {expires_type}")
        if "sync_mode" not in snapshot_columns:
            pending_alters.append("ALTER TABLE snapshots ADD COLUMN sync_mode VARCHAR(20) NOT NULL DEFAULT 'copy'")

    if "model_versions" in set(inspector.get_table_names()):
        mv_columns = {col["name"] for col in inspector.get_columns("model_versions")}
        if "deleted_at" not in mv_columns:
            pending_alters.append(f"ALTER TABLE model_versions ADD COLUMN deleted_at {expires_type}")

    if "runs" in set(inspector.get_table_names()):
        run_columns = {col["name"] for col in inspector.get_columns("runs")}
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

    # In PostgreSQL, we must gracefully migrate the unique constraint from `tag` to `(owner_id, tag)`
    # This prevents the "Account with tag 'su' already exists" bug for multi-tenant accounts
    if dialect == "postgresql":
        try:
            constraints = inspector.get_unique_constraints("accounts")
            constraint_names = {c.get("name") for c in constraints if c.get("name")}
            
            # Drop the old global constraint if it exists
            if "accounts_tag_key" in constraint_names:
                pending_alters.append("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_tag_key CASCADE")
            
            # Add the new composite constraint if it doesn't exist
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
        if "runs" in set(inspector.get_table_names()):
            run_columns = {col["name"] for col in inspector.get_columns("runs")}
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
    from sqlalchemy import inspect, text
    from semabridge.repository.orm.session_factory import get_engine

    engine = get_engine()
    if engine.dialect.name not in ("postgresql", "duckdb", "sqlite"):
        logger.debug("Credentials migration skipped for dialect: %s", engine.dialect.name)
        return

    inspector = inspect(engine)
    if "semabridge_credentials" not in set(inspector.get_table_names()):
        # Table doesn't exist yet — create_all() will create it with the new schema.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("semabridge_credentials")}
    if "owner_id" in existing_cols:
        return  # Already migrated — nothing to do.

    dialect = engine.dialect.name
    logger.info("Migrating semabridge_credentials: adding owner_id to composite PK...")

    ddl_steps: list[str] = []

    if dialect == "postgresql":
        ddl_steps = [
            # 1. Add column with default 0 — existing rows become system/global automatically.
            "ALTER TABLE semabridge_credentials ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 0",
            # 2. Drop the old (service, key) primary key.
            "ALTER TABLE semabridge_credentials DROP CONSTRAINT IF EXISTS semabridge_credentials_pkey",
            # 3. Create new (owner_id, service, key) primary key.
            "ALTER TABLE semabridge_credentials ADD PRIMARY KEY (owner_id, service, key)",
            # 4. Supporting index for per-user, per-service range scans.
            "CREATE INDEX IF NOT EXISTS ix_credential_owner_service "
            "ON semabridge_credentials (owner_id, service)",
        ]
    elif dialect == "sqlite":
        # SQLite can't ALTER PRIMARY KEY — recreate the table.
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
    """Apply PostgreSQL Row Level Security policies on tenant-scoped tables.

    RLS is Layer 2 defense-in-depth: even if application code forgets to
    filter by ``owner_id``, the database refuses to return other users' rows.

    The policy uses the session variable ``app.current_user_id`` which is
    set by :func:`semabridge.api.deps.get_scoped_db` at the start of each
    request via ``SET LOCAL``.

    Policy logic:
        - When ``app.current_user_id`` is set → only rows where
          ``owner_id = current_user_id`` OR ``owner_id IS NULL`` are visible.
        - When ``app.current_user_id`` is not set (CLI/internal) → all rows
          are visible (policy evaluates to ``true``).

    This is idempotent — ``CREATE POLICY ... IF NOT EXISTS`` is not supported
    by all PG versions, so we use ``DROP POLICY IF EXISTS`` + ``CREATE POLICY``.
    """
    from sqlalchemy import text
    from semabridge.repository.orm.session_factory import get_engine

    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return  # RLS is PostgreSQL-specific.

    rls_statements = [
        # Enable RLS on accounts table (idempotent)
        "ALTER TABLE accounts ENABLE ROW LEVEL SECURITY",
        # Drop existing policy if present (idempotent re-creation)
        "DROP POLICY IF EXISTS account_owner_isolation ON accounts",
        # Create the policy:
        #   - When app.current_user_id is '' (not set) → allow all (CLI/internal)
        #   - When set → only owner's rows + orphaned rows (owner_id IS NULL)
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
        # Ensure the application role can still see rows through RLS
        # (superusers bypass RLS by default, but the application role does not)
        "ALTER TABLE accounts FORCE ROW LEVEL SECURITY",
    ]

    with engine.begin() as conn:
        for stmt in rls_statements:
            conn.execute(text(stmt.strip()))

    logger.info("PostgreSQL RLS policies applied on 'accounts' table.")

def _assign_orphaned_accounts_to_dev_user() -> None:
    """Assign accounts with NULL owner_id to the first active user.

    This handles the dev-to-multi-user transition. Accounts created before
    the ``owner_id`` column was added (or before ``AUTH_ENABLED=true``) have
    ``owner_id IS NULL``.  In development mode, this assigns them to the
    first active user so they appear correctly in the UI.

    In production (``AUTH_ENABLED=true``), orphaned accounts remain unowned
    and are inaccessible until an admin assigns them.
    """
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    async def _create_orm_tables_with_retry() -> None:
        max_retries = 3
        retry_delays = [2, 5, 10]
        for attempt in range(max_retries):
            try:
                import semabridge.repository.orm.cache_models  # noqa: F401
                import semabridge.repository.orm.models  # noqa: F401
                from semabridge.repository.orm.session_factory import get_engine

                orm_engine = get_engine()
                dialect = orm_engine.dialect.name
                if dialect == 'duckdb':
                    from semabridge.repository.orm.base import Base
                    Base.metadata.create_all(bind=orm_engine)
                    logger.debug('DuckDB: ORM tables verified / created via create_all()')
                elif dialect == 'snowflake':
                    logger.debug('Snowflake: Skipping ORM table creation (tables should exist in production)')
                else:
                    # PostgreSQL: use create_all with checkfirst=True.
                    # Alembic migrations are available for CI/CD but are skipped
                    # at dev startup to avoid connection-pool deadlocks on Windows.
                    from semabridge.repository.orm.base import Base
                    Base.metadata.create_all(bind=orm_engine, checkfirst=True)
                    logger.debug('PostgreSQL: ORM tables verified / created via create_all(checkfirst=True)')
                return
            except Exception as exc:
                error_str = str(exc).lower()
                is_ssl_error = 'wantreaderror' in error_str or 'ssl' in error_str or 'openssl' in error_str
                is_network_error = 'connection' in error_str or 'timeout' in error_str or 'refused' in error_str
                if (is_ssl_error or is_network_error) and attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    logger.warning('ORM table setup failed (attempt %d/%d) - retrying in %d seconds: %s', attempt + 1, max_retries, delay, exc)
                    await asyncio.sleep(delay)
                else:
                    logger.warning('ORM table setup failed - attempting create_all() fallback: %s', exc)
                    try:
                        from semabridge.repository.orm.base import Base
                        from semabridge.repository.orm.session_factory import get_engine

                        engine = get_engine()
                        if engine.dialect.name == 'snowflake':
                            try:
                                Base.metadata.create_all(bind=engine)
                            except NotImplementedError as idx_exc:
                                if 'index' in str(idx_exc).lower():
                                    logger.warning('Snowflake index creation not supported in fallback - continuing: %s', idx_exc)
                                else:
                                    raise
                        else:
                            Base.metadata.create_all(bind=engine)
                    except Exception as fallback_exc:
                        logger.error('ORM table creation failed entirely: %s', fallback_exc)
                    return

    try:
        await _create_orm_tables_with_retry()
        try:
            _apply_schema_compatibility_fixes()
        except Exception as fix_exc:
            logger.warning('Schema compatibility fix skipped: %s', fix_exc)
        try:
            _migrate_credentials_table()
        except Exception as cred_exc:
            logger.warning('Credentials table migration skipped: %s', cred_exc)
        try:
            _apply_rls_policies()
        except Exception as rls_exc:
            logger.warning('RLS policy setup skipped: %s', rls_exc)
        try:
            _assign_orphaned_accounts_to_dev_user()
        except Exception as orphan_exc:
            logger.debug('Orphaned account assignment skipped: %s', orphan_exc)
    except Exception as exc:
        logger.error('ORM table setup failed: %s', exc)

    # Phase 3: Log multi-tenant enforcement status at startup.
    auth_enabled = os.environ.get("AUTH_ENABLED", "").lower() == "true"
    if auth_enabled:
        logger.info(
            "AUTH_ENABLED=true — multi-tenant credential isolation ENFORCED. "
            "Direct project ownership checks active on all project-scoped sync and CRUD endpoints."
        )
    else:
        logger.info(
            "AUTH_ENABLED is not set — running in single-user development mode. "
            "Account ownership checks are DISABLED."
        )

    def _prime_msal() -> None:
        try:
            _get_msal_app('https://login.microsoftonline.com/organizations')
            logger.debug('MSAL client warmed up')
        except Exception as exc:
            logger.debug('MSAL warm-up skipped (no network?): %s', exc)

    asyncio.get_event_loop().run_in_executor(None, _prime_msal)

    try:
        from semabridge.core.settings import reload_settings
        from semabridge.repository.credential_manager import CredentialManager

        cm = CredentialManager()
        injected = cm.inject_all()
        reload_settings()
        logger.debug(
            'Startup credential injection complete: fabric=%s, snowflake=%s',
            injected.get('fabric', 0),
            injected.get('snowflake', 0),
        )
    except Exception as exc:
        logger.warning('Startup credential injection skipped: %s', exc)

    try:
        # Restore scheduler state after settings/env/bootstrap work is done so
        # scheduled project runs reuse the same execution path as manual runs.
        scheduler_service.configure(_execute_project_run, _compat_clear_project_schedule)
        scheduler_service.start()
        _compat_ensure_loaded()
        for project_id, schedule_payload in list(_compat_project_schedules.items()):
            try:
                scheduler_service.save_project_schedule(project_id, schedule_payload)
            except Exception as schedule_exc:
                logger.warning('Failed to restore project schedule %s: %s', project_id, schedule_exc)
    except Exception as exc:
        logger.error('Scheduler startup failed: %s', exc)

    async def _cleanup_poll_sessions() -> None:
        while True:
            await asyncio.sleep(300)
            try:
                now = time.time()
                expired = []
                with _poll_sessions_lock:
                    for fid, state in _poll_sessions.items():
                        if now > state.get('expires_at', 0):
                            expired.append(fid)
                    for fid in expired:
                        _poll_sessions.pop(fid, None)
                        _last_poll_time.pop(fid, None)
                if expired:
                    logger.debug('Cleaned up %d expired OAuth poll sessions', len(expired))
            except Exception as exc:
                logger.error('Poll session cleanup error: %s', exc)

    cleanup_task = asyncio.create_task(_cleanup_poll_sessions())
    logger.info('SemaBridge API startup complete; application is ready')
    yield
    cleanup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cleanup_task

    from semabridge.repository.orm.session_factory import db_manager as orm_db_manager

    scheduler_service.shutdown()
    orm_db_manager.dispose()
    logger.debug('Database engine disposed on shutdown')


class RequestResponseLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        skip_paths = ['/api/health', '/docs', '/redoc', '/openapi.json']
        if request.url.path in skip_paths:
            return await call_next(request)

        method = request.method
        path = request.url.path
        query_string = request.url.query
        log_msg = f'[REQUEST] {method} {path}'
        if query_string:
            log_msg += f'?{query_string}'

        logger.info(log_msg)
        start_time = time.time()
        try:
            response = await call_next(request)
        except asyncio.CancelledError:
            elapsed = time.time() - start_time
            logger.info(f'[CANCELLED] {method} {path} ({elapsed:.2f}s) during shutdown/request cancellation')
            raise
        except Exception:
            elapsed = time.time() - start_time
            logger.exception(f'[EXCEPTION] {method} {path} ({elapsed:.2f}s)')
            raise
        elapsed = time.time() - start_time

        if response.status_code >= 400:
            logger.warning(f'[RESPONSE] {method} {path} -> {response.status_code} ({elapsed:.2f}s)')
        else:
            logger.info(f'[RESPONSE] {method} {path} -> {response.status_code} ({elapsed:.2f}s)')

        return response


def configure_app(app: FastAPI) -> FastAPI:
    # Starlette processes add_middleware() in REVERSE order: last-added runs first.
    # We want the execution order: CORSMiddleware → Logging → AuthMiddleware
    # So we add them in reverse: Auth first, Logging second, CORS last.
    if AuthMiddleware is not None:
        app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestResponseLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            'http://localhost:5173',   # Vite dev server
            'http://localhost:3000',   # Alternative dev port
            'http://127.0.0.1:5173',
            'http://127.0.0.1:3000',
        ],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    return app
