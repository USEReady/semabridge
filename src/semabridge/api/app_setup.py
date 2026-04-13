from __future__ import annotations

import asyncio
import logging
import os
import time
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
    except Exception as exc:
        logger.error('ORM table setup failed: %s', exc)

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
    # Middleware stays centralized here so main.py only wires the app together.
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
    app.add_middleware(RequestResponseLoggingMiddleware)
    if AuthMiddleware is not None:
        app.add_middleware(AuthMiddleware)
    return app
