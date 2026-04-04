"""
SemaBridge FastAPI Service
Production-ready backend for React + PyQt parity

- Real Fabric discovery
- Real YAML generation
- Real validation
- No mocked data
"""
# Load .env FIRST so SEMABRIDGE_DATABASE_URL, FABRIC_* and all other
# env-vars are resolved before any module-level code reads os.environ.
import os as _os
from pathlib import Path as _Path

from semabridge.core.env import get_fabric_access_token_from_env, load_repo_dotenv

load_repo_dotenv()

from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Any, List, Optional

import asyncio
from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Query, File, UploadFile

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import hashlib
import json
import os
import re
import yaml
import logging
import time
import tempfile
import uuid

# Windows-specific asyncio stability:
# use the selector loop instead of Proactor to avoid intermittent
# `_ProactorBaseWritePipeTransport._loop_writing` assertion failures
# during heavy logging / websocket / pipe writes.
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# Core imports
from semabridge.core.settings import get_settings, reload_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.utils.logger import setup_logging
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.auth.fabric_validator import fabric_validator
from semabridge.api.repo_router import router as repo_router
from semabridge.api.sync_router import router as sync_router
from semabridge.api.account_router import router as account_router
from semabridge.api.settings_api import router as settings_router
from semabridge.api.discovery_api import router as discovery_router
from semabridge.api.browse import router as browse_router
from semabridge.api.websocket_alerts import alert_router, install_websocket_alert_handler
from semabridge.api.semantic_models import SemanticSyncRequest, SemanticRefreshRequest
from semabridge.api.services.scheduler_service import SchedulerService
from semabridge.api.services.sync_execution_service import execute_sync_request
from semabridge.api.services.version_control_service import VersionControlService
from sqlalchemy.orm import Session
from semabridge.api.deps import get_db
from semabridge.api.ui import router as ui_router

try:
    from semabridge.api.auth_router import router as auth_router
    from semabridge.auth.middleware import AuthMiddleware
    _AUTH_AVAILABLE = True
except ImportError:
    auth_router = None  # type: ignore[assignment]
    AuthMiddleware = None  # type: ignore[assignment,misc]
    _AUTH_AVAILABLE = False

# -------------------------------------------------------
# Initialize Core Services (module-level, before app creation)
# -------------------------------------------------------

setup_logging(level="INFO")

# Install WebSocket alert handler so warnings/errors auto-dispatch to UI
install_websocket_alert_handler()

logger = logging.getLogger("semabridge.api")

db_manager = ModelRepository()
engine = ExecutionEngine(db_manager=db_manager)

settings = get_settings()
scheduler_service = SchedulerService()
version_control_service: Optional[VersionControlService] = None

# Content hash tracker -- avoids duplicate versions for unchanged files
_last_snapshot_hash: dict[str, str] = {}


# -------------------------------------------------------
# Bearer Token Extraction (needed by early Fabric routes)
# -------------------------------------------------------
def _extract_bearer_token(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> Optional[str]:
    """Extract a bearer token from Authorization header.

    Accepts header format: ``Authorization: Bearer <token>``.
    Returns ``None`` when no header is provided so existing auth flows can
    continue to use stored credentials or env-token fallback.
    """
    if not authorization:
        logger.info("Fabric request received without Authorization header")
        return None

    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        logger.warning("Invalid Authorization header format for Fabric request")
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Expected: Bearer <token>",
        )
    logger.info("Fabric bearer token received in Authorization header")
    return token


# -------------------------------------------------------
# Application Lifespan (startup + graceful shutdown)
# -------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # type: ignore[type-arg]
    """Manage application startup and shutdown lifecycle.

    Replaces the deprecated ``@app.on_event("startup")`` / ``"shutdown"``
    pattern with a single async context manager.

    Startup tasks:
    1. Ensure ORM tables exist.
    2. Pre-fetch OIDC discovery document for MSAL.
    3. Inject stored UI credentials into environment.

    Shutdown tasks:
    - Dispose the SQLAlchemy connection pool so all DB connections are
      cleanly closed before the process exits.
    """
    import asyncio

    # ------ STARTUP ----------------------------------------------------------

    # 1. ORM tables — run Alembic migrations automatically so the user
    #    never has to run `alembic upgrade head` manually.
    #    Use retry logic for Snowflake transient SSL/network errors.
    async def _create_orm_tables_with_retry() -> None:
        """Create ORM tables with exponential backoff retry for Snowflake SSL errors."""
        import time
        max_retries = 3
        retry_delays = [2, 5, 10]
        
        for attempt in range(max_retries):
            try:
                import semabridge.repository.orm.models  # noqa: F401  -- registers models
                import semabridge.repository.orm.cache_models  # noqa: F401
                from semabridge.repository.orm.session_factory import get_engine
                from semabridge.core.db_resolver import resolve_db_config

                _orm_engine = get_engine()
                _dialect = _orm_engine.dialect.name

                if _dialect == "duckdb":
                    # Alembic has no DDL backend for DuckDB — use create_all directly.
                    from semabridge.repository.orm.base import Base
                    Base.metadata.create_all(bind=_orm_engine)
                    logger.debug("DuckDB: ORM tables verified / created via create_all()")
                elif _dialect == "snowflake":
                    # Snowflake doesn't support indexes on regular tables
                    # Skip automatic table creation for Snowflake — tables should already exist
                    logger.debug("Snowflake: Skipping ORM table creation (tables should exist in production)")
                    # Note: If tables don't exist, the first query will provide a clear error message
                else:
                    # For PostgreSQL / SQLite / MySQL — run Alembic migrations.
                    from pathlib import Path as _Path
                    from alembic.config import Config as _AlembicConfig
                    from alembic import command as _alembic_cmd

                    # main.py lives at src/semabridge/api/main.py, so parents[3]
                    # resolves to the repository root.
                    _root = _Path(__file__).resolve().parents[3]
                    _ini = _root / "config" / "alembic.ini"
                    if not _ini.exists():
                        _ini = _root / "alembic.ini"
                    _alembic_cfg = _AlembicConfig(str(_ini))
                    _alembic_cfg.set_main_option(
                        "script_location",
                        str(_root / "src" / "semabridge" / "migrations"),
                    )
                    # Inject the live URL so Alembic uses the same database as the app.
                    _db_url = str(_orm_engine.url)
                    _alembic_cfg.set_main_option("sqlalchemy.url", _db_url)
                    _alembic_cmd.upgrade(_alembic_cfg, "head")
                    logger.debug("Alembic migrations applied (dialect: %s)", _dialect)
                
                # Success — exit retry loop
                return
                
            except Exception as _e:
                # Check for Snowflake SSL/network errors worth retrying
                error_str = str(_e).lower()
                is_ssl_error = "wantreaderror" in error_str or "ssl" in error_str or "openssl" in error_str
                is_network_error = "connection" in error_str or "timeout" in error_str or "refused" in error_str
                
                if (is_ssl_error or is_network_error) and attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    logger.warning(
                        "ORM table setup failed (attempt %d/%d) — retrying in %d seconds: %s",
                        attempt + 1, max_retries, delay, _e
                    )
                    await asyncio.sleep(delay)
                else:
                    # Not a retryable error or last retry — fall back to create_all
                    logger.warning("ORM table setup failed — attempting create_all() fallback: %s", _e)
                    try:
                        from semabridge.repository.orm.base import Base
                        from semabridge.repository.orm.session_factory import get_engine
                        _engine = get_engine()
                        _dialect = _engine.dialect.name
                        
                        # For Snowflake, skip indexes as they're not supported on regular tables
                        if _dialect == "snowflake":
                            try:
                                Base.metadata.create_all(bind=_engine)
                            except NotImplementedError as _idx_e:
                                if "index" in str(_idx_e).lower():
                                    logger.warning("Snowflake index creation not supported in fallback - continuing: %s", _idx_e)
                                else:
                                    raise
                            logger.debug("Fallback create_all() succeeded (Snowflake indexes skipped)")
                        else:
                            Base.metadata.create_all(bind=_engine)
                            logger.debug("Fallback create_all() succeeded")
                    except Exception as _e2:
                        logger.error("ORM table creation failed entirely: %s", _e2)
                    return
    
    try:
        await _create_orm_tables_with_retry()
    except Exception as _e:
        logger.error("ORM table setup failed: %s", _e)

    # Auto-provisioning of default backend accounts has been disabled to support
    # true multi-tenant UI tag management.

    # 2. MSAL warm-up (background thread -- never blocks startup)
    def _prime_msal() -> None:
        try:
            _get_msal_app("https://login.microsoftonline.com/organizations")
            logger.debug("MSAL client warmed up")
        except Exception as exc:
            logger.debug("MSAL warm-up skipped (no network?): %s", exc)

    asyncio.get_event_loop().run_in_executor(None, _prime_msal)

    # 3. Stored credentials injection
    try:
        from semabridge.repository.credential_manager import CredentialManager

        cm = CredentialManager()
        injected = cm.inject_all()
        reload_settings()
        logger.debug(
            "Startup credential injection complete: fabric=%s, snowflake=%s",
            injected.get("fabric", 0),
            injected.get("snowflake", 0),
        )
    except Exception as _e:
        logger.warning("Startup credential injection skipped: %s", _e)

    try:
        scheduler_service.configure(_execute_project_run, _compat_clear_project_schedule)
        scheduler_service.start()
        _compat_ensure_loaded()
        for project_id, schedule_payload in list(_compat_project_schedules.items()):
            try:
                scheduler_service.save_project_schedule(project_id, schedule_payload)
            except Exception as schedule_exc:
                logger.warning("Failed to restore project schedule %s: %s", project_id, schedule_exc)
    except Exception as _e:
        logger.error("Scheduler startup failed: %s", _e)

    # 4. Background Poll Session Cleanup (Every 5 mins)
    async def _cleanup_poll_sessions() -> None:
        while True:
            await asyncio.sleep(300)  # 5 minutes
            try:
                now = _time.time()
                expired = []
                with _poll_sessions_lock:
                    for fid, state in _poll_sessions.items():
                        if now > state.get("expires_at", 0):
                            expired.append(fid)
                    for fid in expired:
                        _poll_sessions.pop(fid, None)
                        _last_poll_time.pop(fid, None)
                if expired:
                    logger.debug("Cleaned up %d expired OAuth poll sessions", len(expired))
            except Exception as e:
                logger.error("Poll session cleanup error: %s", e)

    cleanup_task = asyncio.create_task(_cleanup_poll_sessions())

    yield  # ------- APPLICATION IS RUNNING -----------------------------------

    # ------ SHUTDOWN ---------------------------------------------------------
    cleanup_task.cancel()

    from semabridge.repository.orm.session_factory import db_manager as _orm_db_manager
    scheduler_service.shutdown()
    _orm_db_manager.dispose()
    logger.debug("Database engine disposed on shutdown")


# -------------------------------------------------------
# App Setup
# -------------------------------------------------------

app = FastAPI(
    title="SemaBridge API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS (configure in prod if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request/Response Logging Middleware
class RequestResponseLoggingMiddleware(BaseHTTPMiddleware):
    """Log HTTP requests and responses - shows only essential info to users."""
    
    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip logging for health checks and static files to reduce noise
        skip_paths = ["/api/health", "/docs", "/redoc", "/openapi.json"]
        if request.url.path in skip_paths:
            return await call_next(request)
        
        method = request.method
        path = request.url.path
        query_string = request.url.query
        
        # Build request log message
        log_msg = f"[REQUEST] {method} {path}"
        if query_string:
            log_msg += f"?{query_string}"
        
        logger.info(log_msg)
        
        # Time the request
        start_time = time.time()
        response = await call_next(request)
        elapsed = time.time() - start_time
        
        # Log response with status and duration
        # Use different formatting for errors vs success
        if response.status_code >= 400:
            logger.warning(f"[RESPONSE] {method} {path} -> {response.status_code} ({elapsed:.2f}s)")
        else:
            logger.info(f"[RESPONSE] {method} {path} -> {response.status_code} ({elapsed:.2f}s)")
        
        return response

app.add_middleware(RequestResponseLoggingMiddleware)

# JWT auth middleware -- enforced only when AUTH_ENABLED=true
app.add_middleware(AuthMiddleware)

# Repository-Map endpoints
app.include_router(repo_router)

# Sync engine endpoints (bidirectional PBIX <-> Snowflake <-> Power BI)
app.include_router(sync_router)

# Authentication endpoints (register, login, credentials)
if _AUTH_AVAILABLE and auth_router:
    app.include_router(auth_router)

# Account & Credentials Vault endpoints
app.include_router(account_router)

# Local folder registry + discovery endpoints
app.include_router(settings_router)
app.include_router(discovery_router)
app.include_router(browse_router)

# WebSocket alert endpoints (real-time UI notifications)
app.include_router(alert_router)

# React project-creation sync bridge endpoints.
# Mount under /api so it stays aligned with the frontend's API_BASE_URL.
app.include_router(ui_router, prefix="/api")


def _normalize_yaml_windows_path_fields(yaml_text: str) -> str:
    """Normalize backslashes in known YAML path fields to forward slashes.

    Prevents invalid escape sequence errors like "\\U" inside double-quoted
    Windows paths for fields such as pbix_folder.
    """
    pattern = re.compile(
        r'^(\s*(?:pbix_folder|pbix_path|repository_path)\s*:\s*")([^"]*)("\s*)$',
        flags=re.MULTILINE,
    )

    def _replace(match: re.Match[str]) -> str:
        prefix, value, suffix = match.groups()
        safe_value = value.replace("\\", "/")
        return f"{prefix}{safe_value}{suffix}"

    return pattern.sub(_replace, yaml_text)


def _resolve_models_path() -> Path:
    """
    Resolve the local models directory using a strict precedence chain.

        Resolution order:
            1. SEMABRIDGE_LOCAL_MODELS_PATH env var
            2. core.local_models_path in config.yaml (from semabridge init)
            3. semabridge.yaml source.pbix_folder
            4. semabridge.yaml source.repository_path
            5. Default: project root (current working directory)
    """
    # 1. Environment variable
    env_val = os.environ.get("SEMABRIDGE_LOCAL_MODELS_PATH")
    if env_val:
        return Path(env_val).expanduser().resolve()

    # 2. Global config.yaml
    try:
        from semabridge.core.initializer import SemabridgeInitializer
        global_cfg_path = SemabridgeInitializer.get_default_config_path()
        if global_cfg_path.exists():
            global_cfg = yaml.safe_load(global_cfg_path.read_text(encoding="utf-8"))
            raw = (global_cfg or {}).get("core", {}).get("local_models_path")
            if raw:
                return Path(raw).expanduser().resolve()
    except Exception:
        pass

    # 3/4. Project-level semabridge.yaml
    try:
        from semabridge.core.config_loader import get_default_config_path
        sema_yaml = get_default_config_path() or Path("config/semabridge.yaml")
        if sema_yaml.exists():
            cfg = yaml.safe_load(sema_yaml.read_text(encoding="utf-8"))
            source_cfg = (cfg or {}).get("source", {})
            raw = source_cfg.get("pbix_folder") or source_cfg.get("repository_path")
            if raw:
                return Path(raw).expanduser().resolve()
    except Exception:
        pass

    # 5. Default â€” project root (no more ./models subdirectory)
    return Path.cwd()


def _snapshot_model_if_changed(
    model_id: str, content: str, author: str = "discovery",
) -> str | None:
    """
    Create a DuckDB version row for a model ONLY if its content has changed
    since the last snapshot.  Returns the new version_id or None if skipped.

    Extracts `version_tag` from the model YAML itself so each version
    is tagged with the model's own semantic version identifier.
    """
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if _last_snapshot_hash.get(model_id) == content_hash:
        return None

    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        parsed = {"raw": content}

    # Auto-extract version_tag from the model YAML
    version_tag = parsed.get("version_tag") or parsed.get("version") or None

    ws_id = ""
    try:
        ws_id = settings.fabric.workspace_id
    except Exception:
        ws_id = "local"

    version_id = db_manager.insert_model_version(
        model_id=model_id,
        workspace_id=ws_id or "local",
        snapshot=parsed,
        author=author,
        change_summary=f"Snapshot by {author}",
        version_tag=version_tag,
    )
    _last_snapshot_hash[model_id] = content_hash
    return version_id


version_control_service = VersionControlService(
    db_manager=db_manager,
    models_path_resolver=_resolve_models_path,
    hash_tracker=_last_snapshot_hash,
)


# -------------------------------------------------------
# Health
# -------------------------------------------------------

@app.get("/api/health")
async def health_check():
    """Return service status including database connectivity.

    Executes a lightweight ``SELECT 1`` ping using the current engine.
    If the ping fails the response indicates a degraded state so
    load-balancers and monitoring tools can react accordingly.
    """
    from sqlalchemy import text
    from semabridge.repository.orm.session_factory import db_manager as _orm_db_manager

    db_status = "connected"
    db_dialect = "unknown"
    try:
        _engine = _orm_db_manager.get_engine()
        db_dialect = _engine.dialect.name
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Health check DB ping failed: %s", exc)
        db_status = "unreachable"

    overall = "ok" if db_status == "connected" else "degraded"
    return {
        "status": overall,
        "service": "semabridge-api",
        "database": db_status,
        "db_dialect": db_dialect,
    }


# -------------------------------------------------------
# Fabric Discovery (REAL)
# -------------------------------------------------------

@app.get("/api/discovery/fabric")
async def discover_fabric_models(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
):
    import asyncio
    from pydantic import ValidationError
    import anyio
    import httpx

    try:
        settings = get_settings()
        
        # Validate Fabric configuration exists and is properly set up
        try:
            fabric_config = settings.fabric
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Fabric is not configured. "
                    "Set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, and FABRIC_WORKSPACE_ID in .env or environment variables."
                )
            )
        
        resolved_workspace_id = (workspace_id or "").strip()
        if not resolved_workspace_id:
            resolved_workspace_id = os.environ.get("FABRIC_WORKSPACE_ID", "").strip()
        if not resolved_workspace_id:
            try:
                resolved_workspace_id = settings.fabric.workspace_id
            except Exception:
                pass

        if not resolved_workspace_id:
            raise HTTPException(
                status_code=400,
                detail="No Fabric workspace configured. Select a workspace in Settings -> Connections.",
            )

        cache_key = f"fabric:{resolved_workspace_id}:{identity_id}"
        cached = _discovery_cache.get(cache_key)
        if cached and _time.monotonic() < cached["expires_at"]:
            return cached["data"]

        access_token = await anyio.to_thread.run_sync(
            _resolve_fabric_access_token,
            bearer_token,
            identity_id,
        )

        logger.info(
            "Attempting to discover Fabric models in workspace: %s with Identity: %s",
            resolved_workspace_id,
            identity_id,
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://api.fabric.microsoft.com/v1/workspaces/{resolved_workspace_id}/semanticModels",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 401:
            logger.error("Fabric API returned 401 Unauthorized. Token may be invalid or expired.")
            logger.error("Response: %s", resp.text[:500])
            if "invalid_token" in resp.text.lower() or "expired" in resp.text.lower():
                raise HTTPException(
                    status_code=401,
                    detail="Fabric token expired or invalid. Please sign in again via Connections.",
                )
            raise HTTPException(
                status_code=401,
                detail="Fabric API returned 401. Possibly invalid workspace ID or insufficient permissions. Please sign in again.",
            )
        if resp.status_code != 200:
            logger.error("Fabric API error %s: %s", resp.status_code, resp.text[:500])
            raise HTTPException(status_code=resp.status_code, detail=f"Fabric API error: {resp.text}")

        models = resp.json().get("value", [])
        logger.info("Successfully discovered %s Fabric semantic models", len(models))
        result = [
            {
                "id": m.get("id", ""),
                "name": m.get("displayName", "Unnamed"),
                "type": "semantic_model",
                "status": "Available",
            }
            for m in models
        ]
        _discovery_cache[cache_key] = {"data": result, "expires_at": _time.monotonic() + _DISCOVERY_CACHE_TTL}
        return result

    except HTTPException:
        raise
    except Exception as e:
        err_str = str(e) or f"<no message from {type(e).__name__}>"
        logger.exception(f"Unexpected error in Fabric discovery: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Fabric discovery failed ({type(e).__name__}): {err_str}",
        )


@app.get("/api/discovery/fabric/workspaces/{workspace_id}/models")
async def discover_fabric_models_by_workspace(
    workspace_id: str,
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
    connection_id: Optional[str] = Query(None, alias="connectionId"),
):
    """Compatibility route for workspace-scoped Fabric discovery."""
    return await discover_fabric_models(
        bearer_token=bearer_token,
        identity_id=(identity_id or connection_id),
        workspace_id=(workspace_id or "").strip() or None,
    )


@app.get("/api/discovery/snowflake")
async def discover_snowflake():
    """
    Discover **semantic views** in the configured Snowflake schema.

    Physical tables are no longer returned by this endpoint; only Snowflake
    semantic views (``CREATE OR REPLACE SEMANTIC VIEW``) are exposed as the
    primary data objects in the discovery process.

    Returns a list of objects with keys: id, name, type, status, description.
    Falls back to a DDL-scan approach when ``SHOW SEMANTIC VIEWS`` is
    unavailable in the connected environment.
    """
    import time
    global _snowflake_discovery_cache
    if '_snowflake_discovery_cache' not in globals():
        _snowflake_discovery_cache = {}

    cache_key = "views"
    if cache_key in _snowflake_discovery_cache:
        cached_data, cached_time = _snowflake_discovery_cache[cache_key]
        if time.monotonic() - cached_time < 300: # 5 minutes TTL
            return cached_data

    try:
        from pydantic import ValidationError
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor

        # Re-inject stored credentials and bust the settings cache so that
        # credentials saved via the UI (Settings → Connections or SSO) are
        # always visible to SnowflakeConfig, even if they were stored after
        # the last server restart.
        try:
            from semabridge.repository.credential_manager import CredentialManager
            from semabridge.core.settings import reload_settings
            _cm = CredentialManager()
            _cm.inject_credentials_to_env("snowflake")
            settings = reload_settings()
        except Exception:
            settings = get_settings()

        # Validate Snowflake configuration exists
        try:
            snowflake_config = settings.snowflake
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Snowflake is not configured. "
                    "Set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, and SNOWFLAKE_PASSWORD in .env or environment variables."
                )
            )
        
        extractor = SnowflakeExtractor(snowflake_config)

        logger.info(
            f"Discovering Snowflake semantic views in "
            f"{snowflake_config.database}.{snowflake_config.schema_name}"
        )

        views = extractor.discover_semantic_views()

        results = [
            {
                "id": v["name"],
                "name": v["name"],
                "type": "semantic_view",
                "status": "Available",
                "description": v.get("comment") or "",
                "created_on": v.get("created_on"),
                "schema": v.get("schema", snowflake_config.schema_name),
                "database": v.get("database", snowflake_config.database),
            }
            for v in views
        ]

        logger.info(f"Discovered {len(results)} Snowflake semantic view(s)")
        
        final_results = sorted(results, key=lambda x: x["name"])
        import time
        _snowflake_discovery_cache[cache_key] = (final_results, time.monotonic())
        return final_results

    except HTTPException:
        raise
    except AttributeError as ae:
        logger.error(f"Snowflake semantic view discovery failed (AttributeError): {ae}")
        # Catch the specific SnowflakeConnection.execute() error
        if "execute" in str(ae).lower() or "SnowflakeConnection" in str(ae):
            raise HTTPException(
                status_code=500,
                detail=f"Snowflake connection error - this is likely a driver issue. Please check your Snowflake connection. Error: {ae}",
            )
        raise HTTPException(
            status_code=500,
            detail=f"Snowflake semantic view discovery failed: {ae}",
        )
    except Exception as e:
        logger.error(f"Snowflake semantic view discovery failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Snowflake semantic view discovery failed: {e}",
        )


@app.get("/api/discovery/repository")
async def discover_repository():
    """
    Discover local models from the repository.
    Uses SQLAlchemy ORM so it works with any configured backend
    (DuckDB, PostgreSQL, SQLite, etc.).
    """
    from semabridge.repository.orm.models import ModelVersion
    from sqlalchemy import select, func

    try:
        session = db_manager._session()
        try:
            # Subquery: latest created_at timestamp per model_id
            subq = (
                select(
                    ModelVersion.model_id,
                    func.max(ModelVersion.created_at).label("max_ts"),
                )
                .group_by(ModelVersion.model_id)
                .subquery()
            )
            latest_rows = session.execute(
                select(ModelVersion).join(
                    subq,
                    (ModelVersion.model_id == subq.c.model_id)
                    & (ModelVersion.created_at == subq.c.max_ts),
                )
            ).scalars().all()

            # Version counts per model_id
            count_rows = session.execute(
                select(ModelVersion.model_id, func.count().label("cnt"))
                .group_by(ModelVersion.model_id)
            ).all()
            count_map = {r.model_id: r.cnt for r in count_rows}

            results = []
            for row in latest_rows:
                model_id = row.model_id
                snapshot_data = row.snapshot
                if isinstance(snapshot_data, str):
                    try:
                        snapshot = json.loads(snapshot_data)
                    except Exception:
                        snapshot = {}
                else:
                    snapshot = snapshot_data or {}

                results.append({
                    "id": model_id,
                    "name": f"{model_id}.yaml",
                    "type": "yaml",
                    "status": "Available",
                    "path": f"duckdb://{model_id}",
                    "versioned": count_map.get(model_id, 0) > 0,
                })

            return sorted(results, key=lambda x: x["name"])
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Repository discovery failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to scan repository: {e}")


# -------------------------------------------------------
# Semantic Model Sync Endpoints (Fabric ↔ Snowflake)
# -------------------------------------------------------

@app.get("/api/discovery/semantic", response_model=None)
async def discover_semantic():
    """
    Unified semantic discovery across **both** Fabric and Snowflake.

    Returns semantic models from Fabric and semantic views from Snowflake
    together with cross-platform mapping / sync-status information.
    Platform errors are surfaced as non-fatal fields so partial results
    are always returned.
    """
    from pydantic import ValidationError
    from semabridge.api.semantic_models import (
        SemanticDiscoveryResponse,
        SemanticMappingItem,
        SemanticModelItem,
    )

    fabric_items: list = []
    snowflake_items: list = []
    fabric_error: Optional[str] = None
    snowflake_error: Optional[str] = None

    # --- Fabric ---
    try:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        settings = get_settings()
        
        # Check if Fabric is properly configured before trying to initialize
        try:
            fabric_config = settings.fabric
        except ValidationError as ve:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Fabric is not properly configured. "
                    "Please set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, and FABRIC_WORKSPACE_ID in settings."
                )
            )
        
        extractor = FabricExtractor(fabric_config)
        models = extractor.list_semantic_models()
        fabric_items = [
            SemanticModelItem(
                id=m.get("id", m.get("displayName", "")),
                name=m.get("displayName", ""),
                type="semantic_model",
                platform="fabric",
                description=m.get("description", ""),
                extra={k: v for k, v in m.items() if k not in ("id", "displayName", "description")},
            )
            for m in models
        ]
    except HTTPException:
        raise
    except Exception as exc:
        fabric_error = str(exc)
        logger.warning(f"Fabric semantic discovery failed (non-fatal): {exc}")

    # --- Snowflake ---
    try:
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        settings = get_settings()
        
        # Check if Snowflake is properly configured before trying to initialize
        try:
            snowflake_config = settings.snowflake
        except ValidationError as ve:
            snowflake_error = (
                "Snowflake is not properly configured. "
                "Please set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD in settings."
            )
            logger.warning(f"Snowflake semantic view discovery failed (non-fatal): {snowflake_error}")
            snowflake_config = None
        
        if snowflake_config is not None:
            extractor = SnowflakeExtractor(snowflake_config)
            views = extractor.discover_semantic_views()
            snowflake_items = [
                SemanticModelItem(
                    id=v["name"],
                    name=v["name"],
                    type="semantic_view",
                    platform="snowflake",
                    description=v.get("comment", ""),
                    extra={"schema": v.get("schema"), "database": v.get("database")},
                )
                for v in views
            ]
    except HTTPException:
        raise
    except Exception as exc:
        snowflake_error = str(exc)
        logger.warning(f"Snowflake semantic view discovery failed (non-fatal): {exc}")

    # --- Cross-platform mappings ---
    mappings: list = []
    try:
        from semabridge.sync.repository import SyncRepository
        repo = SyncRepository()
        all_mappings = repo.list_mappings()
        sf_view_names = {i.name for i in snowflake_items}
        fabric_name_map = {i.name: i for i in fabric_items}

        for mapping in all_mappings:
            if mapping.source_type not in ("fabric",) and mapping.target_type not in (
                "snowflake_semantic_view",
                "fabric",
            ):
                continue
            sf_view = mapping.snowflake_semantic_view or mapping.target_identifier
            fabric_name = (
                mapping.model_name
                if mapping.source_type == "fabric"
                else mapping.model_name
            )
            mappings.append(
                SemanticMappingItem(
                    fabric_name=fabric_name,
                    fabric_id=mapping.fabric_model_id or mapping.source_identifier,
                    snowflake_view=sf_view if sf_view in sf_view_names else sf_view,
                    last_synced=mapping.last_synced_at,
                    in_sync=bool(mapping.last_osi_hash),
                    osi_hash=mapping.last_osi_hash,
                )
            )
    except Exception as exc:
        logger.warning(f"Mapping enrichment failed (non-fatal): {exc}")

    return SemanticDiscoveryResponse(
        fabric=fabric_items,
        snowflake=snowflake_items,
        mappings=mappings,
        fabric_error=fabric_error,
        snowflake_error=snowflake_error,
    )


@app.post("/api/semantic/sync", response_model=None)
async def semantic_sync(request_body: SemanticSyncRequest):
    """
    Trigger a semantic model synchronization between Fabric and Snowflake.

    Accepted ``direction`` values:
    - ``fabric_to_snowflake`` — push Fabric semantic models as Snowflake semantic views
    - ``snowflake_to_fabric`` — push Snowflake semantic views to Fabric as semantic models
    - ``fabric_snowflake_bidirectional`` — sync in both directions

    The sync runs in **background** via the existing ``SyncOrchestrator``
    pipeline (Extract → OSI → Convert → Deploy).
    """
    from semabridge.api.semantic_models import SemanticSyncRequest, SemanticSyncResponse
    from semabridge.sync.models import SyncConfig, SyncDirection
    from semabridge.sync.orchestrator import SyncOrchestrator
    from semabridge.sync.repository import SyncRepository

    # Validate direction is a Fabric↔Snowflake one
    allowed = {
        SyncDirection.FABRIC_TO_SNOWFLAKE,
        SyncDirection.SNOWFLAKE_TO_FABRIC,
        SyncDirection.FABRIC_SNOWFLAKE_BIDIRECTIONAL,
    }
    if request_body.direction not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"direction '{request_body.direction.value}' is not a semantic sync direction. "
                f"Use one of: {[d.value for d in allowed]}"
            ),
        )

    config = SyncConfig(
        direction=request_body.direction,
        conflict_resolution=request_body.conflict_resolution,
        fabric_workspace_id=request_body.fabric_workspace_id,
        snowflake_schema=request_body.snowflake_schema,
        fabric_model_names=request_body.fabric_model_names,
        snowflake_semantic_views=request_body.snowflake_semantic_views,
        max_workers=request_body.max_workers,
        incremental=request_body.incremental,
    )

    repo = SyncRepository()
    orchestrator = SyncOrchestrator(repo)
    job = orchestrator.run(config, initiated_by="api")

    return SemanticSyncResponse(
        job_id=job.job_id,
        direction=job.direction.value,
        status=job.status.value,
        total_items=job.total_items,
        message=(
            f"Semantic sync job started: {job.total_items} item(s) "
            f"queued for direction '{job.direction.value}'"
        ),
    )


@app.post("/api/semantic/refresh", response_model=None)
async def semantic_refresh(request_body: SemanticRefreshRequest):
    """
    Refresh semantic metadata from both Fabric and Snowflake **without deploying**.

    Fetches the latest OSI representation from each platform, stores a new
    snapshot in the repository, and returns a diff summary showing what has
    changed since the last refresh.

    This is a read-only operation — nothing is written to Fabric or Snowflake.
    """
    from semabridge.api.semantic_models import (
        SemanticRefreshRequest,
        SemanticRefreshResponse,
        SemanticRefreshSummary,
    )
    from semabridge.repository.semantic_diff_engine import SemanticDiffEngine

    response = SemanticRefreshResponse()
    diff_engine = SemanticDiffEngine()

    # --- Fabric ---
    try:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager

        settings = get_settings()
        extractor = FabricExtractor(settings.fabric)
        converter = TMSLToOSIConverter()
        snapshot_mgr = SemanticSnapshotManager()

        models = extractor.list_semantic_models()
        if request_body.fabric_model_names:
            lower_filter = {n.lower() for n in request_body.fabric_model_names}
            models = [m for m in models if m.get("displayName", "").lower() in lower_filter]

        for model_meta in models:
            try:
                tmsl = extractor.get_model_definition(model_meta["id"])
                osi = converter.to_osi(
                    {
                        "tmsl": tmsl,
                        "workspace_id": settings.fabric.workspace_id,
                        "dataset_id": model_meta.get("displayName", model_meta["id"]),
                    }
                )
                prev_snapshot = snapshot_mgr.get_latest_snapshot(osi.unique_name)
                if prev_snapshot:
                    diff = diff_engine.compare_states(
                        prev_snapshot.semantic_entities or {},
                        osi.model_dump(),
                    )
                    summary = SemanticRefreshSummary(
                        model_name=osi.unique_name,
                        platform="fabric",
                        added_datasets=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "dataset" and c.change_type.value == "added"
                        ],
                        removed_datasets=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "dataset" and c.change_type.value == "removed"
                        ],
                        added_metrics=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "measure" and c.change_type.value == "added"
                        ],
                        removed_metrics=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "measure" and c.change_type.value == "removed"
                        ],
                        added_relationships=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "relationship" and c.change_type.value == "added"
                        ],
                        removed_relationships=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "relationship" and c.change_type.value == "removed"
                        ],
                        osi_hash=prev_snapshot.schema_hash,
                    )
                else:
                    summary = SemanticRefreshSummary(
                        model_name=osi.unique_name, platform="fabric"
                    )
                snapshot_mgr.create_snapshot(osi.unique_name, osi.model_dump())
                response.summaries.append(summary)
                response.refreshed_fabric += 1
            except Exception as exc:
                response.errors.append(
                    f"Fabric '{model_meta.get('displayName', '')}': {exc}"
                )
    except Exception as exc:
        response.errors.append(f"Fabric refresh setup failed: {exc}")

    # --- Snowflake ---
    try:
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
        from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager

        settings = get_settings()
        extractor = SnowflakeExtractor(settings.snowflake)
        converter = SemanticViewToOSIConverter()
        snapshot_mgr = SemanticSnapshotManager()

        views = extractor.discover_semantic_views()
        if request_body.snowflake_semantic_views:
            lower_filter = {v.lower() for v in request_body.snowflake_semantic_views}
            views = [v for v in views if v["name"].lower() in lower_filter]

        for view_meta in views:
            try:
                ddl = extractor.extract_semantic_view_ddl(view_meta["name"])
                osi = converter.to_osi(
                    {"ddl": ddl, "view_name": view_meta["name"]}
                )
                prev_snapshot = snapshot_mgr.get_latest_snapshot(osi.unique_name)
                if prev_snapshot:
                    diff = diff_engine.compare_states(
                        prev_snapshot.semantic_entities or {},
                        osi.model_dump(),
                    )
                    summary = SemanticRefreshSummary(
                        model_name=osi.unique_name,
                        platform="snowflake",
                        added_datasets=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "dataset" and c.change_type.value == "added"
                        ],
                        removed_datasets=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "dataset" and c.change_type.value == "removed"
                        ],
                        added_metrics=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "measure" and c.change_type.value == "added"
                        ],
                        removed_metrics=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "measure" and c.change_type.value == "removed"
                        ],
                        added_relationships=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "relationship" and c.change_type.value == "added"
                        ],
                        removed_relationships=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "relationship" and c.change_type.value == "removed"
                        ],
                        osi_hash=prev_snapshot.schema_hash,
                    )
                else:
                    summary = SemanticRefreshSummary(
                        model_name=osi.unique_name, platform="snowflake"
                    )
                snapshot_mgr.create_snapshot(osi.unique_name, osi.model_dump())
                response.summaries.append(summary)
                response.refreshed_snowflake += 1
            except Exception as exc:
                response.errors.append(f"Snowflake view '{view_meta['name']}': {exc}")
    except Exception as exc:
        response.errors.append(f"Snowflake refresh setup failed: {exc}")

    return response


# -------------------------------------------------------
# Individual Model CRUD + Auto-Versioning
# -------------------------------------------------------

@app.get("/api/models/{model_id}")
async def get_model(model_id: str):
    """
    Read a single model from the repository (latest version).
    Uses SQLAlchemy ORM so it works with any configured backend
    (DuckDB, PostgreSQL, SQLite, etc.).
    """
    from semabridge.repository.orm.models import ModelVersion
    from sqlalchemy import select

    session = db_manager._session()
    try:
        row = session.execute(
            select(ModelVersion)
            .where(ModelVersion.model_id == model_id)
            .order_by(ModelVersion.created_at.desc())
            .limit(1)
        ).scalars().first()

        if not row:
            raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found in repository")

        snapshot_data = row.snapshot
        if isinstance(snapshot_data, str):
            try:
                parsed = json.loads(snapshot_data)
            except Exception:
                parsed = {}
        else:
            parsed = snapshot_data or {}

        content = yaml.dump(parsed, sort_keys=False) if parsed else ""

        return {
            "model_id": model_id,
            "filename": f"{model_id}.yaml",
            "content": content,
            "path": f"duckdb://{model_id}",
        }
    finally:
        session.close()


@app.put("/api/models/{model_id}")
async def save_model(model_id: str, payload: Dict[str, Any]):
    """
    Save a new immutable version of the model directly to DuckDB.
    """
    content = payload.get("content", "")
    author = payload.get("author", "ui")
    message = payload.get("message", "Saved from UI")
    version_tag = payload.get("version_tag", None)

    if not content.strip():
        raise HTTPException(status_code=400, detail="content is required")

    # Parse content for DuckDB snapshot
    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        parsed = {"raw": content}

    # Auto-extract version_tag from model YAML if not provided explicitly
    if not version_tag and isinstance(parsed, dict):
        version_tag = parsed.get("version_tag") or parsed.get("version") or None

    # Resolve workspace
    ws_id = "local"
    try:
        ws_id = settings.fabric.workspace_id or "local"
    except Exception:
        pass

    # Create version in DuckDB
    version_id = db_manager.insert_model_version(
        model_id=model_id,
        workspace_id=ws_id,
        snapshot=parsed,
        author=author,
        change_summary=message,
        version_tag=version_tag,
    )

    # Update hash tracker so next discovery won't double-snapshot
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _last_snapshot_hash[model_id] = content_hash

    tag_str = f" tag={version_tag}" if version_tag else ""
    logger.info(f"Versioned {model_id} â†’ {version_id[:8]}â€¦{tag_str} by {author}")

    return {
        "status": "saved",
        "model_id": model_id,
        "version_id": version_id,
        "version_tag": version_tag,
        "filename": f"{model_id}.yaml",
        "path": f"duckdb://{model_id}",
    }

# -------------------------------------------------------
# Get Default Config (REAL SETTINGS)
# -------------------------------------------------------
@app.get("/api/config")
async def get_config():
    try:
        from semabridge.core.config_loader import get_default_config_path
        config_path = get_default_config_path() or Path("config/semabridge.yaml")

        if not config_path.exists():
            raise HTTPException(
                status_code=404,
                detail="semabridge.yaml not found in project"
            )

        content = config_path.read_text(encoding="utf-8")

        return {"content": content}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
         

# -------------------------------------------------------
# Global Config (~/.semabridge/config.yaml)
# -------------------------------------------------------

def _global_config_path() -> Path:
    """Return the path to the global config file."""
    return Path.home() / ".semabridge" / "config.yaml"


@app.get("/api/global-config")
async def get_global_config():
    """Read the global config file as raw YAML text."""
    config_path = _global_config_path()
    if not config_path.exists():
        # Return a sensible default template
        default_content = """\
# SemaBridge Global Configuration
# Located at: ~/.semabridge/config.yaml

fabric:
  tenant_id: ""
  client_id: ""
  # client_secret_env: FABRIC_CLIENT_SECRET

snowflake:
  account: ""
  database: ""
  schema: "PUBLIC"
  warehouse: ""
  role: ""
  # password_env: SNOWFLAKE_PASSWORD

logging:
  level: INFO
  file: ~/.semabridge/semabridge.log

defaults:
  source_type: fabric
  target_type: snowflake
"""
        return {"content": default_content, "path": str(config_path), "exists": False}

    try:
        content = config_path.read_text(encoding="utf-8")
        return {"content": content, "path": str(config_path), "exists": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read global config: {e}")


@app.put("/api/global-config")
async def save_global_config(payload: Dict[str, Any]):
    """Validate and save the global config YAML."""
    content = payload.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Config content cannot be empty")

    # Validate YAML syntax
    try:
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Config must be a YAML mapping (dict)")
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML syntax: {e}")

    # Ensure no inline secrets
    errors = []
    _check_inline_secrets(parsed, "", errors)
    if errors:
        raise HTTPException(status_code=400, detail=f"Security violation: {'; '.join(errors)}")

    config_path = _global_config_path()
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(content, encoding="utf-8")
        return {"status": "saved", "path": str(config_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save global config: {e}")


def _check_inline_secrets(data: Any, path: str, errors: list) -> None:
    """Recursively check that no inline secrets appear in config values."""
    secret_keys = {"password", "secret", "token", "api_key", "private_key"}
    if isinstance(data, dict):
        for key, value in data.items():
            full_path = f"{path}.{key}" if path else key
            key_lower = key.lower()
            # Allow keys ending in _env (they reference env vars, not inline secrets)
            if any(s in key_lower for s in secret_keys) and not key_lower.endswith("_env"):
                if isinstance(value, str) and value.strip():
                    errors.append(f"Inline secret at '{full_path}' â€” use '{key}_env' suffix to reference an environment variable instead")
            _check_inline_secrets(value, full_path, errors)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _check_inline_secrets(item, f"{path}[{i}]", errors)


# -------------------------------------------------------
# Generate YAML From Selected Models
# -------------------------------------------------------

@app.post("/api/config/generate")
async def generate_config(payload: Dict[str, Any]):
    """
    Generate semabridge.yaml configuration based on selected models.
    Output intentionally follows the same schema used by the project config UI.
    """
    try:
        settings = get_settings()
        selected_models: List[str] = payload.get("models", [])
        model_ids: List[str] = payload.get("modelIds", [])
        source_type: str = payload.get("sourceType", "fabric")
        target_type: str = payload.get("targetType", "snowflake")
        pbix_folder: str = str(payload.get("pbixFolder", "") or "").strip()

        # If modelIds were not provided explicitly, infer UUID-like values from model list.
        if not model_ids and selected_models:
            uuid_like = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
            model_ids = [m for m in selected_models if isinstance(m, str) and uuid_like.match(m.strip())]

        project_name = str(payload.get("projectName") or settings.model.name or "semabridge-project").strip()
        workspace_name = str(payload.get("workspaceName") or "SemaBridge Workspace").strip()

        source_cfg: Dict[str, Any] = {
            "type": source_type,
        }

        if source_type == "fabric":
            source_cfg["workspace_id"] = settings.fabric.workspace_id or ""
            source_cfg["workspace"] = workspace_name
        elif source_type == "snowflake":
            source_cfg["database"] = settings.snowflake.database or ""
            source_cfg["schema"] = settings.snowflake.schema_name or "PUBLIC"
        elif source_type == "pbix":
            local_models_path = pbix_folder or str(_resolve_models_path())
            source_cfg["pbix_folder"] = local_models_path.replace("\\", "/")

        if selected_models:
            source_cfg["models"] = selected_models
        else:
            source_cfg["model"] = "*"

        config_doc: Dict[str, Any] = {
            "project_name": project_name,
            "source": source_cfg,
            "target": {
                "type": target_type,
            },
            "ui": {
                "output_format": "osi",
                "editor_mode": "yaml",
            },
            "options": {
                "auto_relationships": True,
                "include_hidden_fields": True,
                "generate_descriptions": True,
            },
        }

        if model_ids:
            config_doc["selection"] = {"model_ids": model_ids}

        yaml_content = yaml.safe_dump(
            config_doc,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )

        return {"content": yaml_content}

    except Exception as e:
        logger.exception("YAML generation failed")
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------------------------------------
# YAML Validation
# -------------------------------------------------------

@app.post("/api/config/validate")
async def validate_config(payload: Dict[str, str]):

    content = payload.get("content", "")
    errors = []

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return {
            "valid": False,
            "errors": [{
                "line": getattr(e, "problem_mark", None).line + 1
                if hasattr(e, "problem_mark") else 0,
                "message": str(e)
            }]
        }

    # Required sections
    required_sections = ["source", "target"]

    for section in required_sections:
        if section not in parsed:
            errors.append({
                "line": 0,
                "message": f"Missing required section: '{section}'"
            })

    # â”€â”€ Warnings for missing tables â”€â”€
    warnings = []
    source = parsed.get("source", {}) if parsed else {}
    if source.get("type") == "snowflake" or source.get("repository_path"):
        model_names = source.get("models", [])
        try:
            for mname in (model_names or []):
                models_path = _resolve_models_path()
                candidate = models_path / f"{mname}.yaml"
                if candidate.exists():
                    mcfg = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                    for ds in mcfg.get("datasets", []):
                        tbl = ds.get("source_table") or ds.get("table", "")
                        if tbl:
                            # Quick check â€” cannot verify Snowflake tables without connection,
                            # but we can flag if the YAML references tables not defined locally
                            pass
        except Exception:
            pass

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


# -------------------------------------------------------
# History (DuckDB Snapshots)
# -------------------------------------------------------

@app.get("/api/history")
async def get_history():

    try:
        project_id = settings.model.name
        snapshots = db_manager.list_snapshots(project_id, limit=20)

        return [
            {
                "version_id": s.snapshot_id,
                "timestamp": s.timestamp,
                "description": s.version_tag or "Automated Sync",
                "status": s.status
            }
            for s in snapshots
        ]

    except Exception as e:
        logger.exception("History fetch failed")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# Sync/Deploy Models (REAL)
# -------------------------------------------------------

@app.post("/api/sync")
async def sync_models(payload: Dict[str, Any]):
    """
    Trigger a full synchronization based on the current configuration.
    """
    try:
        return execute_sync_request(payload, _normalize_yaml_windows_path_fields)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Sync execution failed")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# Live Validation â€” check models for missing tables + warnings
# -------------------------------------------------------

@app.post("/api/config/validate-live")
async def validate_live(
    payload: Dict[str, Any] = None,
    db: "Session" = Depends(get_db),
):
    """
    Run live-validation on the current model state.

    Returns both errors and warnings (e.g. missing source tables).
    This endpoint is called periodically by the frontend.
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    try:
        from sqlalchemy import text

        rows = db.execute(text("""
                WITH RankedVersions AS (
                    SELECT model_id, snapshot, created_at,
                           ROW_NUMBER() OVER(PARTITION BY model_id ORDER BY created_at DESC) as rn
                    FROM model_versions
                )
                SELECT model_id, snapshot
                FROM RankedVersions
                WHERE rn = 1
            """)).fetchall()

        for row in rows:
            model_id = row[0]
            snapshot_data = row[1]
            
            if isinstance(snapshot_data, str):
                try:
                    data = json.loads(snapshot_data)
                except Exception as e:
                    errors.append({
                        "model": model_id,
                        "severity": "error",
                        "message": f"Failed to parse JSON: {str(e)}",
                    })
                    continue
            else:
                data = snapshot_data or {}

            if not isinstance(data, dict):
                continue

            if not any(
                k in data
                for k in (
                    "datasets",
                    "metrics",
                    "measures",
                    "model_name",
                    "name",
                    "unique_name",
                    "label",
                )
            ):
                continue

            display_model_name = (
                str(data.get("model_name") or "").strip()
                or str(data.get("name") or "").strip()
                or str(data.get("label") or "").strip()
                or str(data.get("unique_name") or "").strip()
                or model_id
            )

            # Check datasets for missing source references
            for ds in data.get("datasets", []):
                tbl = ds.get("source_table") or ds.get("table", "")
                cols = ds.get("columns", [])
                ds_name = ds.get("name") or ds.get("unique_name") or "?"
                if not tbl:
                    warnings.append({
                        "model": display_model_name,
                        "severity": "warning",
                        "message": f"Dataset '{ds_name}' has no source_table defined",
                    })
                if not cols:
                    warnings.append({
                        "model": display_model_name,
                        "severity": "warning",
                        "message": f"Dataset '{ds_name}' has no columns defined",
                    })

            # Check relationships for dangling references
            datasets_names = {
                (ds.get("name") or ds.get("unique_name") or "").upper()
                for ds in data.get("datasets", [])
                if (ds.get("name") or ds.get("unique_name"))
            }
            for rel in data.get("relationships", []):
                from_m = (rel.get("from_model") or rel.get("from_table") or "").upper()
                to_m = (rel.get("to_model") or rel.get("to_table") or "").upper()
                if from_m and from_m not in datasets_names:
                    warnings.append({
                        "model": display_model_name,
                        "severity": "error",
                        "message": f"Relationship references unknown dataset '{from_m}'",
                    })
                if to_m and to_m not in datasets_names:
                    warnings.append({
                        "model": display_model_name,
                        "severity": "error",
                        "message": f"Relationship references unknown dataset '{to_m}'",
                    })

            # Schema checks
            if not any(
                str(data.get(k) or "").strip()
                for k in ("model_name", "name", "unique_name", "label")
            ):
                errors.append({
                    "model": display_model_name,
                    "severity": "error",
                    "message": "Model missing identity field (expected one of: model_name, name, unique_name, label)",
                })
    except Exception as e:
        errors.append({"model": "system", "severity": "error", "message": str(e)})

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "total_issues": len(errors) + len(warnings),
    }



# -------------------------------------------------------
# Workspaces (Fabric) — DB-driven, no startup globals
# -------------------------------------------------------


@app.get("/api/workspaces")
async def list_workspaces(
    db: "Session" = Depends(get_db),
    identity_id: Optional[str] = Query(None),
):
    """List available Fabric workspaces for the selected Fabric account."""
    import httpx

    access_token: str | None = None
    if not identity_id:
        raise HTTPException(status_code=400, detail="account_id is required")

    try:
        import anyio
        access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, None, identity_id)
        logger.info("list_workspaces: using account '%s'", identity_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("list_workspaces: account-scoped lookup failed for %s: %s", identity_id, exc)
        raise HTTPException(status_code=503, detail="Workspace discovery temporarily unavailable")

    if not access_token:
        raise HTTPException(status_code=401, detail="token_missing")

    # --- Call Fabric API with the live token ---
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                "https://api.fabric.microsoft.com/v1/workspaces",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            return [
                {"id": ws.get("id", ""), "name": ws.get("displayName", "")}
                for ws in resp.json().get("value", [])
                if ws.get("id")
            ]
        logger.warning(
            "list_workspaces: Fabric API returned %s: %s",
            resp.status_code, resp.text[:200],
        )
    except Exception as exc:
        logger.warning("list_workspaces: Fabric API call failed: %s", exc)

    return []



# -------------------------------------------------------
# Compatibility endpoints (newfrontend)
# -------------------------------------------------------

_compat_projects: Dict[str, Dict[str, Any]] = {}
_compat_project_configs: Dict[str, str] = {}
_compat_project_runs: Dict[str, List[Dict[str, Any]]] = {}
_compat_folders: Dict[str, Dict[str, Any]] = {}
_compat_mappings: Dict[str, Dict[str, Any]] = {}
_compat_project_schedules: Dict[str, Dict[str, Any]] = {}
_compat_job_config: Dict[str, Any] = {
    "schedule_type": "Manual Trigger Only",
    "cron": "0 0 * * *",
    "timezone": "UTC",
    "enabled": False,
    "mode": "local",
}
_compat_store_loaded: bool = False


def _compat_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _compat_store_path() -> Path:
    p = Path("config/.semabridge_compat_store.json")
    if p.exists():
        return p
    legacy = Path(".semabridge_compat_store.json")
    if legacy.exists():
        return legacy
    return p


def _compat_save_store() -> None:
    payload = {
        "projects": _compat_projects,
        "project_configs": _compat_project_configs,
        "project_runs": _compat_project_runs,
        "folders": _compat_folders,
        "mappings": _compat_mappings,
        "project_schedules": _compat_project_schedules,
        "job_config": _compat_job_config,
    }
    try:
        _compat_store_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist compat store: %s", exc)


def _compat_clear_project_schedule(project_id: str) -> None:
    _compat_project_schedules.pop(str(project_id), None)
    _compat_save_store()


def _compat_load_store() -> None:
    global _compat_store_loaded
    if _compat_store_loaded:
        return

    p = _compat_store_path()
    if not p.exists():
        _compat_store_loaded = True
        return

    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        if isinstance(data.get("projects"), dict):
            _compat_projects.update(data.get("projects") or {})
        if isinstance(data.get("project_configs"), dict):
            _compat_project_configs.update(data.get("project_configs") or {})
        if isinstance(data.get("project_runs"), dict):
            _compat_project_runs.update(data.get("project_runs") or {})
        if isinstance(data.get("folders"), dict):
            _compat_folders.update(data.get("folders") or {})
        if isinstance(data.get("mappings"), dict):
            _compat_mappings.update(data.get("mappings") or {})
        if isinstance(data.get("project_schedules"), dict):
            _compat_project_schedules.update(data.get("project_schedules") or {})
        if isinstance(data.get("job_config"), dict):
            _compat_job_config.update(data.get("job_config") or {})
    except Exception as exc:
        logger.warning("Failed to load compat store: %s", exc)
    finally:
        _compat_store_loaded = True


def _compat_bootstrap_projects_from_orm() -> None:
    """Seed compatibility project cache from persisted ORM projects when memory is empty."""
    if _compat_projects:
        return

    try:
        from semabridge.repository.orm.models import Project
        from sqlalchemy import select

        session = db_manager._session()
        try:
            rows = session.execute(
                select(Project).order_by(Project.last_updated.desc().nullslast()).limit(500)
            ).scalars().all()
        finally:
            session.close()

        for row in rows:
            pid = str(row.project_id or "").strip()
            if not pid:
                continue
            project = {
                "id": pid,
                "project_id": pid,
                "name": _compat_clean_project_name(row.name, f"Project {pid[-6:]}"),
                "description": "",
                "source": row.adapter or "fabric",
                "adapter": row.adapter or "fabric",
                "workspace_id": row.workspace_id or "",
                "target_type": "snowflake",
                "folder_id": None,
                "status": "draft",
                "created_at": _compat_now_iso(),
                "updated_at": _compat_now_iso(),
            }
            _compat_projects[pid] = project
            _compat_project_configs.setdefault(pid, _compat_load_repo_yaml_text() or _compat_default_project_yaml(project))
            _compat_project_runs.setdefault(pid, [])
    except Exception as exc:
        logger.debug("ORM project bootstrap skipped: %s", exc)


def _compat_bootstrap_project_from_repo_yaml() -> None:
    """Ensure at least one visible project from root semabridge.yaml if present."""
    if _compat_projects:
        return

    yaml_text = _compat_load_repo_yaml_text()
    if not yaml_text:
        return

    try:
        parsed = yaml.safe_load(yaml_text) or {}
    except Exception:
        parsed = {}

    pname = _compat_clean_project_name(parsed.get("project_name"), "SemaBridge Project")
    pid_hash = hashlib.sha1(pname.encode("utf-8")).hexdigest()[:12]
    project_id = f"proj-{pid_hash}"

    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    target_cfg = parsed.get("target") if isinstance(parsed.get("target"), dict) else {}

    project = {
        "id": project_id,
        "project_id": project_id,
        "name": pname,
        "description": str(parsed.get("description") or ""),
        "source": source_cfg.get("type") or "fabric",
        "adapter": source_cfg.get("type") or "fabric",
        "workspace_id": str(source_cfg.get("workspace_id") or ""),
        "target_type": target_cfg.get("type") or "snowflake",
        "folder_id": None,
        "status": "draft",
        "created_at": _compat_now_iso(),
        "updated_at": _compat_now_iso(),
    }
    _compat_projects[project_id] = project
    _compat_project_configs[project_id] = yaml_text
    _compat_project_runs.setdefault(project_id, [])


def _compat_ensure_loaded() -> None:
    _compat_load_store()
    _compat_bootstrap_projects_from_orm()
    _compat_bootstrap_project_from_repo_yaml()


def _compat_repo_yaml_path() -> Path:
    from semabridge.core.config_loader import get_project_file_path
    return get_project_file_path("semabridge.yaml")


def _compat_load_repo_yaml_text() -> str:
    try:
        p = _compat_repo_yaml_path()
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if text.strip():
                return text
    except Exception:
        pass
    return ""


def _compat_save_repo_yaml_text(yaml_text: str) -> None:
    p = _compat_repo_yaml_path()
    p.write_text(yaml_text, encoding="utf-8")


def _compat_clean_project_name(raw: Any, fallback: str = "Untitled Project") -> str:
    if isinstance(raw, str):
        text = raw.strip()
        if text and text.lower() != "[object object]":
            return text
    return fallback


def _compat_project_payload(project_id: str, payload: dict) -> Dict[str, Any]:
    source_obj = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    target_obj = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    targets_list = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    first_target_obj = targets_list[0] if targets_list and isinstance(targets_list[0], dict) else {}
    project_name = _compat_clean_project_name(payload.get("name"), f"Project {project_id[-6:]}")
    account_id = (
        payload.get("account_id")
        or payload.get("selectedAccountId")
        or source_obj.get("identity_id")
        or payload.get("identity_id")
        or ""
    )
    return {
        "id": project_id,
        "project_id": project_id,
        "name": project_name,
        "description": payload.get("description") or "",
        "source": source_obj.get("type") or payload.get("source_type") or "fabric",
        "adapter": source_obj.get("type") or payload.get("source_type") or "fabric",
        "account_id": str(account_id) if account_id else None,
        "workspace_id": source_obj.get("workspace_id") or "",
        "target_type": target_obj.get("type") or first_target_obj.get("type") or payload.get("target_type") or "snowflake",
        "folder_id": payload.get("folder_id"),
        "status": "draft",
        "created_at": _compat_now_iso(),
        "updated_at": _compat_now_iso(),
    }


def _compat_default_project_yaml(project: Dict[str, Any]) -> str:
    name = _compat_clean_project_name(project.get("name"), "Untitled Project").replace('"', '\\"')
    src = project.get("source") or "fabric"
    target = project.get("target_type") or "snowflake"
    account_id = str(project.get("account_id") or "").strip()
    lines = [
        f'project_name: "{name}"',
        "source:",
        f"  type: {src}",
    ]
    if account_id and src == "fabric":
        account_id_escaped = account_id.replace('"', '\\"')
        lines.append(f'  identity_id: "{account_id_escaped}"')
    workspace_id = project.get("workspace_id")
    if workspace_id:
        workspace_id_escaped = str(workspace_id).replace('"', '\\"')
        lines.append(f'  workspace_id: "{workspace_id_escaped}"')
    lines.extend([
        '  model: "*"',
        "target:",
        f"  type: {target}",
        "ui:",
        '  output_format: "osi"',
    ])
    return "\n".join(lines)

@app.get("/api/projects")
async def list_projects_compat():
    """Compatibility: newfrontend expects a projects collection."""
    _compat_ensure_loaded()
    deduped: Dict[str, Dict[str, Any]] = {}
    for p in _compat_projects.values():
        pid = str(p.get("id") or p.get("project_id") or "").strip()
        if not pid:
            continue
        
        # Filter out auto-generated test projects
        project_name = str(p.get("name") or "").strip().lower()
        is_test = (
            project_name in ("test", "teste", "test project") or
            project_name == "fabricmodel"  # Auto-generated default
        )
        if is_test:
            # Skip test/auto-generated projects
            continue
        
        current = deduped.get(pid)
        if not current:
            deduped[pid] = p
            continue
        cur_ts = str(current.get("updated_at") or current.get("created_at") or "")
        new_ts = str(p.get("updated_at") or p.get("created_at") or "")
        if new_ts >= cur_ts:
            deduped[pid] = p

    return list(deduped.values())


@app.post("/api/projects")
async def create_project_compat(request: dict):
    """Compatibility: create in-memory project for UI continuity."""
    _compat_ensure_loaded()
    payload = request or {}
    payload_name = _compat_clean_project_name(payload.get("name"), "")
    src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    src_type = (src.get("type") or payload.get("source_type") or "fabric").strip().lower()
    ws_id = str(src.get("workspace_id") or payload.get("workspace_id") or "").strip()

    # Idempotency guard: if a project with same name/source/workspace already exists,
    # return it instead of creating a duplicate entry.
    if payload_name:
        for existing in _compat_projects.values():
            ex_name = _compat_clean_project_name(existing.get("name"), "")
            ex_src = str(existing.get("source") or existing.get("adapter") or "").strip().lower()
            ex_ws = str(existing.get("workspace_id") or "").strip()
            if ex_name == payload_name and ex_src == src_type and ex_ws == ws_id:
                existing["updated_at"] = _compat_now_iso()
                _compat_projects[str(existing.get("id") or existing.get("project_id"))] = existing
                return existing

    project_id = str(payload.get("id") or payload.get("project_id") or f"proj-{int(_time.time() * 1000)}")
    project = _compat_project_payload(project_id, payload)
    _compat_projects[project_id] = project

    config_yaml = payload.get("config_yaml")
    if isinstance(config_yaml, str) and config_yaml.strip():
        _compat_project_configs[project_id] = config_yaml
        try:
            _compat_save_repo_yaml_text(config_yaml)
        except Exception as exc:
            logger.warning("Failed to persist project config to semabridge.yaml: %s", exc)
    else:
        repo_yaml = _compat_load_repo_yaml_text()
        _compat_project_configs.setdefault(project_id, repo_yaml or _compat_default_project_yaml(project))

    _compat_project_runs.setdefault(project_id, [])
    _compat_save_store()
    return project


@app.get("/api/projects/{project_id}")
async def get_project_compat(project_id: str):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert for stale in-memory cache after reload.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
        _compat_save_store()
    return project


@app.patch("/api/projects/{project_id}")
async def patch_project_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for key in ("name", "description", "folder_id", "status"):
        if key in (payload or {}):
            project[key] = payload.get(key)

    if isinstance(payload.get("source"), dict):
        project["source"] = payload["source"].get("type") or project.get("source")
        project["adapter"] = project["source"]
        if "workspace_id" in payload["source"]:
            project["workspace_id"] = payload["source"].get("workspace_id")

    if isinstance(payload.get("target"), dict):
        project["target_type"] = payload["target"].get("type") or project.get("target_type")

    if isinstance(payload.get("targets"), list) and payload.get("targets"):
        first_target = payload["targets"][0] if isinstance(payload["targets"][0], dict) else {}
        if first_target.get("type"):
            project["target_type"] = first_target.get("type")

    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    _compat_save_store()
    return project


@app.delete("/api/projects/{project_id}")
async def delete_project_compat(project_id: str):
    _compat_ensure_loaded()
    _compat_projects.pop(project_id, None)
    _compat_project_configs.pop(project_id, None)
    _compat_project_runs.pop(project_id, None)
    _compat_save_store()
    return Response(status_code=204)


@app.get("/api/projects/{project_id}/config")
async def get_project_config_compat(project_id: str):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert: keep UI editable even if project cache was reset.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
    # IMPORTANT: prefer per-project config first so "Copy Presets" can load
    # different YAMLs for different projects. Fall back to repository file only
    # when the project has no stored config.
    yaml_text = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(project)
    _compat_project_configs[project_id] = yaml_text
    return {"project_id": project_id, "config_yaml": yaml_text, "yaml_path": str(_compat_repo_yaml_path().resolve()).replace('\\\\', '/')}


@app.put("/api/projects/{project_id}/config")
async def save_project_config_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert: allow saving config even when only project_id is known.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
    yaml_text = str((payload or {}).get("config_yaml") or "").strip()
    if not yaml_text:
        raise HTTPException(status_code=400, detail="config_yaml is required")
    _compat_project_configs[project_id] = yaml_text
    try:
        parsed = yaml.safe_load(yaml_text) or {}
    except Exception:
        parsed = {}
    if isinstance(parsed, dict):
        source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
        account_id = str(source_cfg.get("identity_id") or "").strip()
        if account_id and project_id in _compat_projects:
            _compat_projects[project_id]["account_id"] = account_id
    try:
        _compat_save_repo_yaml_text(yaml_text)
    except Exception as exc:
        logger.warning("Failed to persist project config to semabridge.yaml: %s", exc)
    project["updated_at"] = _compat_now_iso()
    _compat_save_store()
    return {
        "status": "saved",
        "project_id": project_id,
        "yaml_path": str(_compat_repo_yaml_path().resolve()).replace('\\\\', '/'),
        "warnings": [],
    }


def _compat_set_project_pbix_path(project_id: str, pbix_path: str) -> None:
    """Persist PBIX path in project metadata + project semabridge.yaml blob."""
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    normalized_pbix_path = str(Path(pbix_path).resolve()).replace('\\\\', '/')
    project["pbix_file_path"] = normalized_pbix_path
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project

    yaml_text = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(project)
    parsed = yaml.safe_load(yaml_text) if yaml_text else {}
    if not isinstance(parsed, dict):
        parsed = {}
    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    source_cfg["type"] = "pbix"
    source_cfg["pbix_path"] = normalized_pbix_path
    source_cfg["pbix_file_path"] = normalized_pbix_path
    parsed["source"] = source_cfg

    rebuilt = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)
    _compat_project_configs[project_id] = rebuilt
    _compat_save_store()


def _save_uploaded_pbix_file(upload: UploadFile, target_dir: Path) -> Path:
    """Store an uploaded PBIX file in the target directory and return absolute path."""
    filename = str(upload.filename or "").strip()
    if not filename.lower().endswith(".pbix"):
        raise HTTPException(status_code=400, detail="Only .pbix files are supported")

    safe_name = Path(filename).name
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"{uuid.uuid4().hex}_{safe_name}"

    content = upload.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    destination.write_bytes(content)
    return destination.resolve()


def _extract_snapshot_connectors(snapshot_obj: Any) -> List[str]:
    """Extract source/target connector names from snapshot SML payload."""
    connectors: List[str] = []
    sml = getattr(snapshot_obj, "sml_blob", {}) or {}
    if not isinstance(sml, dict):
        return connectors

    for key in ("source_type", "source", "adapter", "connector"):
        val = sml.get(key)
        if isinstance(val, str) and val.strip():
            connectors.append(val.strip())
            break

    target_val = sml.get("target_type") or sml.get("target")
    if isinstance(target_val, str) and target_val.strip():
        connectors.append(target_val.strip())
    elif isinstance(sml.get("targets"), list):
        for tgt in sml.get("targets"):
            if isinstance(tgt, dict):
                t = str(tgt.get("type") or "").strip()
                if t:
                    connectors.append(t)

    deduped: List[str] = []
    seen = set()
    for c in connectors:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _snapshot_graph_payload(snapshot_obj: Any, model_name: str, include_system_tables: bool = False) -> Dict[str, Any]:
    """Build React-Flow compatible graph payload from a snapshot object."""
    snapshot_id = getattr(snapshot_obj, "snapshot_id", "")
    sml = getattr(snapshot_obj, "sml_blob", {}) or {}
    if not isinstance(sml, dict):
        sml = {}

    def _safe_id(raw: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_\-:.]", "_", str(raw or "unknown"))

    system_prefixes = (
        "information_schema.",
        "pg_",
        "sqlite_",
        "duckdb_",
        "sys.",
        "__",
    )

    def _is_system_table(table_name: str) -> bool:
        val = str(table_name or "").strip().lower()
        if not val:
            return False
        return val.startswith(system_prefixes)

    def _qualify(schema_name: str, table_name: str) -> str:
        schema_name = str(schema_name or "").strip()
        table_name = str(table_name or "").strip()
        if not table_name:
            return ""
        if "." in table_name:
            return table_name
        if schema_name:
            return f"{schema_name}.{table_name}"
        return table_name

    def _card_symbol(card: str) -> str:
        c = str(card or "").strip().lower().replace("_", "-")
        mapping = {
            "one-to-one": "1:1",
            "one-to-many": "1:*",
            "many-to-one": "*:1",
            "many-to-many": "*:*",
        }
        return mapping.get(c, c or "?:?")

    detected_model = str(sml.get("model_name") or model_name or getattr(snapshot_obj, "project_id", "") or "model").strip()
    model_node_id = f"model-{_safe_id(detected_model)}"

    nodes: List[Dict[str, Any]] = [{
        "id": model_node_id,
        "type": "modelNode",
        "position": {"x": 400, "y": 150},
        "data": {
            "label": detected_model,
            "model_id": detected_model,
            "workspace_id": str(sml.get("workspace_id") or ""),
            "description": str(sml.get("description") or ""),
            "status": "valid",
            "nodeType": "model",
        },
    }]
    edges: List[Dict[str, Any]] = []

    table_nodes: Dict[str, str] = {}
    excluded_system_tables = 0

    datasets = sml.get("datasets", [])
    if not isinstance(datasets, list):
        datasets = []

    entities = sml.get("entities", {})
    if isinstance(entities, dict):
        for ent_name, ent_def in entities.items():
            if not isinstance(ent_def, dict):
                continue
            datasets.append({
                "name": ent_name,
                "table": ent_def.get("table") or ent_name,
                "schema": ent_def.get("schema") or ent_def.get("database_schema") or "PUBLIC",
                "source_type": ent_def.get("source_type") or "snapshot",
                "columns": ent_def.get("columns") or [],
            })

    for idx, ds in enumerate(datasets):
        if not isinstance(ds, dict):
            continue
        schema = str(ds.get("source_schema") or ds.get("schema") or ds.get("database_schema") or "PUBLIC")
        table = str(
            ds.get("source_table")
            or ds.get("table")
            or ds.get("name")
            or ds.get("unique_name")
            or ds.get("label")
            or f"table_{idx}"
        )
        qualified = _qualify(schema, table)

        if qualified in table_nodes and table.startswith("unknown"):
            qualified = _qualify(schema, f"{table}_{idx + 1}")

        if not include_system_tables and _is_system_table(qualified):
            excluded_system_tables += 1
            continue

        if qualified in table_nodes:
            continue

        table_node_id = f"table-{_safe_id(qualified)}"
        table_nodes[qualified] = table_node_id
        columns = ds.get("columns") if isinstance(ds.get("columns"), list) else []

        nodes.append({
            "id": table_node_id,
            "type": "tableNode",
            "position": {"x": 120 + (len(table_nodes) - 1) * 260, "y": 0},
            "data": {
                "label": qualified,
                "table_name": table,
                "schema": schema,
                "source_type": str(ds.get("source_type") or "snapshot"),
                "columns": columns,
                "nodeType": "table",
                "status": "valid",
            },
        })

        edges.append({
            "id": f"e-{table_node_id}-{model_node_id}",
            "source": table_node_id,
            "target": model_node_id,
            "animated": True,
            "style": {"stroke": "#22C55E"},
        })

    measures = sml.get("metrics", sml.get("measures", []))
    if not isinstance(measures, list):
        measures = []

    for midx, measure in enumerate(measures):
        if not isinstance(measure, dict):
            continue
        measure_name = str(measure.get("name") or measure.get("unique_name") or measure.get("label") or f"measure_{midx}")
        measure_node_id = f"measure-{_safe_id(detected_model)}-{midx}"
        nodes.append({
            "id": measure_node_id,
            "type": "measureNode",
            "position": {"x": 120 + midx * 260, "y": 320},
            "data": {
                "label": measure_name,
                "expression": str(measure.get("expression") or ""),
                "data_type": str(measure.get("data_type") or measure.get("format_string") or ""),
                "parent_model": detected_model,
                "nodeType": "measure",
            },
        })
        edges.append({
            "id": f"e-{model_node_id}-{measure_node_id}",
            "source": model_node_id,
            "target": measure_node_id,
            "animated": False,
            "style": {"stroke": "#EAB308"},
        })

    relationships = sml.get("relationships", [])
    if not isinstance(relationships, list):
        relationships = []

    for ridx, rel in enumerate(relationships):
        if not isinstance(rel, dict):
            continue

        from_schema = rel.get("from_schema") or rel.get("source_schema") or ""
        to_schema = rel.get("to_schema") or rel.get("target_schema") or ""
        from_table = rel.get("from_table") or rel.get("from_model") or rel.get("from") or rel.get("source")
        to_table = rel.get("to_table") or rel.get("to_model") or rel.get("to") or rel.get("target")

        from_key = _qualify(from_schema, str(from_table or "")).strip()
        to_key = _qualify(to_schema, str(to_table or "")).strip()

        if not from_key or not to_key:
            continue
        if (not include_system_tables) and (_is_system_table(from_key) or _is_system_table(to_key)):
            continue

        if from_key not in table_nodes:
            nid = f"table-{_safe_id(from_key)}"
            table_nodes[from_key] = nid
            nodes.append({
                "id": nid,
                "type": "tableNode",
                "position": {"x": 120 + (len(table_nodes) - 1) * 260, "y": 0},
                "data": {
                    "label": from_key,
                    "table_name": from_key,
                    "schema": from_schema or "PUBLIC",
                    "source_type": "snapshot",
                    "columns": [],
                    "nodeType": "table",
                    "status": "broken",
                },
            })

        if to_key not in table_nodes:
            nid = f"table-{_safe_id(to_key)}"
            table_nodes[to_key] = nid
            nodes.append({
                "id": nid,
                "type": "tableNode",
                "position": {"x": 120 + (len(table_nodes) - 1) * 260, "y": 0},
                "data": {
                    "label": to_key,
                    "table_name": to_key,
                    "schema": to_schema or "PUBLIC",
                    "source_type": "snapshot",
                    "columns": [],
                    "nodeType": "table",
                    "status": "broken",
                },
            })

        cardinality = str(rel.get("cardinality") or rel.get("relationship_type") or rel.get("type") or "many-to-one")
        from_col = str(rel.get("from_column") or (rel.get("from_columns") or [""])[0] or rel.get("join_key") or "")
        to_col = str(rel.get("to_column") or (rel.get("to_columns") or [""])[0] or "")
        join_label = f"{from_col} → {to_col}" if from_col and to_col else from_col

        edges.append({
            "id": f"rel-{ridx}-{table_nodes[from_key]}-{table_nodes[to_key]}",
            "source": table_nodes[from_key],
            "target": table_nodes[to_key],
            "label": f"{_card_symbol(cardinality)}{f' • {join_label}' if join_label else ''}",
            "type": "smoothstep",
            "style": {"stroke": "#818CF8", "strokeDasharray": "6 3"},
            "data": {
                "cardinality": cardinality,
                "from_column": from_col,
                "to_column": to_col,
            },
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "snapshot_id": snapshot_id,
        "model": model_name,
        "timestamp": getattr(snapshot_obj, "timestamp", None),
        "version_tag": getattr(snapshot_obj, "version_tag", None),
        "run_id": getattr(snapshot_obj, "run_id", None),
        "status": getattr(snapshot_obj, "status", "success"),
        "connectors": _extract_snapshot_connectors(snapshot_obj),
        "meta": {
            "tables_included": len(table_nodes),
            "system_tables_excluded": excluded_system_tables,
            "include_system_tables": include_system_tables,
        },
    }


@app.get("/api/graph/{model_name}/snapshots")
async def graph_snapshots_compat(model_name: str):
    """Snapshot history for Explore time-machine (newest first)."""
    try:
        logger.info("[Explore] Snapshot list requested model=%s", model_name)
        snapshots = db_manager.list_snapshots(model_name, limit=200)
        logger.info("[Explore] Snapshot list resolved model=%s count=%s", model_name, len(snapshots or []))
        return [
            {
                "snapshot_id": s.snapshot_id,
                "timestamp": s.timestamp,
                "version_tag": s.version_tag or f"v{s.snapshot_id[:8]}",
                "status": s.status or "success",
                "duration_ms": s.duration_ms or 0,
                "model_name": model_name,
                "run_id": s.run_id,
                "initiated_by": s.initiated_by,
                "connectors": _extract_snapshot_connectors(s),
            }
            for s in snapshots
        ]
    except Exception as exc:
        logger.debug("Failed to list snapshots for %s: %s", model_name, exc)
        return []


@app.get("/api/graph/{model_name}/snapshot/{snapshot_id}")
async def graph_snapshot_compat(model_name: str, snapshot_id: str, include_system_tables: bool = False):
    """Load graph for a single snapshot."""
    try:
        logger.info(
            "[Explore] Snapshot graph requested model=%s snapshot_id=%s include_system_tables=%s",
            model_name,
            snapshot_id,
            include_system_tables,
        )
        snapshot = db_manager.get_snapshot(snapshot_id)
        if not snapshot:
            logger.warning("Snapshot %s not found", snapshot_id)
            return {"nodes": [], "edges": [], "snapshot_id": snapshot_id, "model": model_name}
        payload = _snapshot_graph_payload(snapshot, model_name, include_system_tables)
        logger.info(
            "[Explore] Snapshot graph ready model=%s snapshot_id=%s nodes=%s edges=%s",
            model_name,
            snapshot_id,
            len(payload.get("nodes") or []),
            len(payload.get("edges") or []),
        )
        return payload
    except Exception as exc:
        logger.debug("Failed to load snapshot graph %s: %s", snapshot_id, exc)
        return {"nodes": [], "edges": [], "snapshot_id": snapshot_id, "model": model_name}


@app.get("/api/graph/{model_name}/compare")
async def compare_graph_snapshots_compat(
    model_name: str,
    from_snapshot_id: str,
    to_snapshot_id: str,
    include_system_tables: bool = False,
):
    """Compare two snapshots and return graph diff + tabular change details."""
    try:
        snap_from = db_manager.get_snapshot(from_snapshot_id)
        snap_to = db_manager.get_snapshot(to_snapshot_id)
        if not snap_from or not snap_to:
            return {
                "summary": {"added": 0, "removed": 0, "modified": 0},
                "changes": [],
                "relationships": [],
                "styled_graph": {"nodes": [], "edges": []},
            }

        from_graph = _snapshot_graph_payload(snap_from, model_name, include_system_tables)
        to_graph = _snapshot_graph_payload(snap_to, model_name, include_system_tables)

        from_nodes = from_graph.get("nodes", [])
        to_nodes = to_graph.get("nodes", [])
        from_edges = from_graph.get("edges", [])
        to_edges = to_graph.get("edges", [])

        def _node_key(n: Dict[str, Any]) -> str:
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            return f"{d.get('nodeType', '')}:{str(d.get('label') or n.get('id') or '')}"

        def _edge_key(e: Dict[str, Any], node_map: Dict[str, Dict[str, Any]]) -> str:
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            d_src = (node_map.get(src, {}).get("data") or {}) if isinstance(node_map.get(src, {}), dict) else {}
            d_tgt = (node_map.get(tgt, {}).get("data") or {}) if isinstance(node_map.get(tgt, {}), dict) else {}
            src_lbl = str(d_src.get("label") or src)
            tgt_lbl = str(d_tgt.get("label") or tgt)
            rel_lbl = str(e.get("label") or "")
            return f"{src_lbl}->{tgt_lbl}:{rel_lbl}"

        from_node_map = {_node_key(n): n for n in from_nodes}
        to_node_map = {_node_key(n): n for n in to_nodes}
        from_id_map = {str(n.get("id")): n for n in from_nodes}
        to_id_map = {str(n.get("id")): n for n in to_nodes}

        changes: List[Dict[str, Any]] = []

        added_keys = sorted(set(to_node_map.keys()) - set(from_node_map.keys()))
        removed_keys = sorted(set(from_node_map.keys()) - set(to_node_map.keys()))
        common_keys = sorted(set(from_node_map.keys()) & set(to_node_map.keys()))

        for k in added_keys:
            n = to_node_map[k]
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            changes.append({
                "change_type": "added",
                "entity_type": d.get("nodeType") or "node",
                "entity_name": d.get("label") or n.get("id"),
                "details": "Entity added",
                "from_value": None,
                "to_value": d,
            })

        for k in removed_keys:
            n = from_node_map[k]
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            changes.append({
                "change_type": "removed",
                "entity_type": d.get("nodeType") or "node",
                "entity_name": d.get("label") or n.get("id"),
                "details": "Entity removed",
                "from_value": d,
                "to_value": None,
            })

        for k in common_keys:
            n1 = from_node_map[k]
            n2 = to_node_map[k]
            d1 = n1.get("data", {}) if isinstance(n1.get("data"), dict) else {}
            d2 = n2.get("data", {}) if isinstance(n2.get("data"), dict) else {}
            left = {
                "columns": d1.get("columns") or [],
                "expression": d1.get("expression") or "",
                "data_type": d1.get("data_type") or "",
                "status": d1.get("status") or "",
            }
            right = {
                "columns": d2.get("columns") or [],
                "expression": d2.get("expression") or "",
                "data_type": d2.get("data_type") or "",
                "status": d2.get("status") or "",
            }
            if json.dumps(left, sort_keys=True, default=str) != json.dumps(right, sort_keys=True, default=str):
                changes.append({
                    "change_type": "modified",
                    "entity_type": d2.get("nodeType") or d1.get("nodeType") or "node",
                    "entity_name": d2.get("label") or d1.get("label") or n2.get("id"),
                    "details": "Schema/metric definition changed",
                    "from_value": left,
                    "to_value": right,
                })

        from_edge_map = {_edge_key(e, from_id_map): e for e in from_edges}
        to_edge_map = {_edge_key(e, to_id_map): e for e in to_edges}
        rel_changes: List[Dict[str, Any]] = []

        for k in sorted(set(to_edge_map.keys()) - set(from_edge_map.keys())):
            rel_changes.append({"change_type": "added", "relationship": k})
        for k in sorted(set(from_edge_map.keys()) - set(to_edge_map.keys())):
            rel_changes.append({"change_type": "removed", "relationship": k})

        # Build styled graph (base = to_graph), append removed entities as ghost nodes/edges
        styled_nodes = []
        for n in to_nodes:
            key = _node_key(n)
            status = "unchanged"
            if key in added_keys:
                status = "added"
            elif any(c.get("change_type") == "modified" and c.get("entity_name") == (n.get("data", {}) or {}).get("label") for c in changes):
                status = "modified"
            nn = dict(n)
            nn["data"] = {**(n.get("data") if isinstance(n.get("data"), dict) else {}), "diffStatus": status}
            styled_nodes.append(nn)

        for k in removed_keys:
            n = from_node_map[k]
            ghost = dict(n)
            ghost["id"] = f"removed-{n.get('id')}"
            ghost["data"] = {**(n.get("data") if isinstance(n.get("data"), dict) else {}), "diffStatus": "removed"}
            styled_nodes.append(ghost)

        styled_edges = []
        for e in to_edges:
            key = _edge_key(e, to_id_map)
            status = "added" if key not in from_edge_map else "unchanged"
            ee = dict(e)
            ee["data"] = {**(e.get("data") if isinstance(e.get("data"), dict) else {}), "diffStatus": status}
            styled_edges.append(ee)

        for k in sorted(set(from_edge_map.keys()) - set(to_edge_map.keys())):
            e = dict(from_edge_map[k])
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            if src in from_id_map and _node_key(from_id_map[src]) in removed_keys:
                e["source"] = f"removed-{src}"
            if tgt in from_id_map and _node_key(from_id_map[tgt]) in removed_keys:
                e["target"] = f"removed-{tgt}"
            e["id"] = f"removed-{e.get('id') or k}"
            e["data"] = {**(e.get("data") if isinstance(e.get("data"), dict) else {}), "diffStatus": "removed"}
            styled_edges.append(e)

        summary = {
            "added": len([c for c in changes if c.get("change_type") == "added"]),
            "removed": len([c for c in changes if c.get("change_type") == "removed"]),
            "modified": len([c for c in changes if c.get("change_type") == "modified"]),
            "relationships_added": len([r for r in rel_changes if r.get("change_type") == "added"]),
            "relationships_removed": len([r for r in rel_changes if r.get("change_type") == "removed"]),
        }

        return {
            "summary": summary,
            "changes": changes,
            "relationships": rel_changes,
            "styled_graph": {
                "nodes": styled_nodes,
                "edges": styled_edges,
                "meta": {
                    "from_snapshot_id": from_snapshot_id,
                    "to_snapshot_id": to_snapshot_id,
                    "include_system_tables": include_system_tables,
                },
            },
        }
    except Exception as exc:
        logger.debug("Failed to compare snapshots %s -> %s: %s", from_snapshot_id, to_snapshot_id, exc)
        return {
            "summary": {"added": 0, "removed": 0, "modified": 0},
            "changes": [],
            "relationships": [],
            "styled_graph": {"nodes": [], "edges": []},
        }


@app.get("/api/projects/{project_id}/runs")
async def get_project_runs_compat(project_id: str):
    _compat_ensure_loaded()
    return _compat_project_runs.get(project_id, [])


def _extract_source_type_from_project_cfg(project_cfg: str, fallback: str = "fabric") -> str:
    try:
        parsed = yaml.safe_load(project_cfg) or {}
        source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
        source_type = str(source_cfg.get("type") or fallback).strip().lower()
        return source_type or fallback
    except Exception:
        return fallback


def _collect_step_logs(summary: Dict[str, Any], prefix: str = "") -> List[str]:
    lines: List[str] = []
    steps = summary.get("steps_completed") if isinstance(summary, dict) else []
    errors = summary.get("errors") if isinstance(summary, dict) else []

    for step in steps or []:
        step_no = step.get("step_number", "?")
        step_name = step.get("step_name") or "Unknown"
        step_status = str(step.get("status") or "info").upper()
        detail = f" - {step.get('message')}" if step.get("message") else ""
        prefix_text = f"{prefix} " if prefix else ""
        lines.append(f"{step_status} {prefix_text}Stage {step_no}: {step_name}{detail}")

    for err in errors or []:
        step_no = err.get("step_number", "?")
        step_name = err.get("step_name") or "Execution"
        msg = err.get("message") or "Unknown error"
        prefix_text = f"{prefix} " if prefix else ""
        lines.append(f"ERROR {prefix_text}Stage {step_no} ({step_name}) - {msg}")

    return lines


def _build_run_logs(sync_result: Dict[str, Any]) -> List[str]:
    logs: List[str] = []
    summary = sync_result.get("summary") if isinstance(sync_result, dict) else {}
    logs.extend(_collect_step_logs(summary if isinstance(summary, dict) else {}))

    for result in (sync_result.get("results") or []):
        if not isinstance(result, dict):
            continue
        model_name = str(result.get("model") or "Model")
        model_summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        logs.extend(_collect_step_logs(model_summary, f"[{model_name}]"))

    return logs


def _build_stage_states(sync_result: Dict[str, Any]) -> List[Dict[str, str]]:
    stage_defaults: List[Dict[str, str]] = [
        {"id": "extraction", "label": "Extraction", "status": "pending"},
        {"id": "osi_conversion", "label": "OSI Conversion", "status": "pending"},
        {"id": "sml_generation", "label": "SML Generation", "status": "pending"},
        {"id": "snowflake_deployment", "label": "Snowflake Deployment", "status": "pending"},
    ]

    summary = sync_result.get("summary") if isinstance(sync_result, dict) else {}
    steps = summary.get("steps_completed") if isinstance(summary, dict) else []
    if not isinstance(steps, list):
        return stage_defaults

    status_by_stage = {item["id"]: item["status"] for item in stage_defaults}
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_number = int(step.get("step_number") or 0)
        normalized = str(step.get("status") or "pending").lower()
        if step_number == 4:
            status_by_stage["extraction"] = normalized
        elif step_number == 6:
            status_by_stage["osi_conversion"] = normalized
            status_by_stage["sml_generation"] = normalized
        elif step_number in (8, 9):
            status_by_stage["snowflake_deployment"] = normalized

    return [
        {"id": item["id"], "label": item["label"], "status": status_by_stage.get(item["id"], item["status"])}
        for item in stage_defaults
    ]


def _create_project_run(project_id: str, schedule_label: str = "Manual") -> tuple[dict, str, float]:
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    started = _time.time()
    run_id = f"run-{int(_time.time() * 1000)}"
    project_cfg = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(_compat_projects[project_id])
    account_id = str(_compat_projects[project_id].get("account_id") or "").strip()
    if account_id:
        try:
            parsed_cfg = yaml.safe_load(project_cfg) or {}
        except Exception:
            parsed_cfg = {}
        if isinstance(parsed_cfg, dict):
            source_cfg = parsed_cfg.get("source") if isinstance(parsed_cfg.get("source"), dict) else {}
            if source_cfg.get("type", "fabric").lower() == "fabric" and not source_cfg.get("identity_id"):
                source_cfg["identity_id"] = account_id
                parsed_cfg["source"] = source_cfg
                project_cfg = yaml.safe_dump(parsed_cfg, sort_keys=False, allow_unicode=False)
    source_type = _extract_source_type_from_project_cfg(
        project_cfg,
        fallback=str(_compat_projects[project_id].get("source") or "fabric").lower(),
    )

    run = {
        "run_id": run_id,
        "id": run_id,
        "project_id": project_id,
        "project_name": _compat_projects[project_id].get("name", project_id),
        "schedule": schedule_label,
        "status": "running",
        "source_type": source_type,
        "message": "Execution started.",
        "logs": ["LIVE Run queued. Waiting for execution engine..."],
        "stage_states": [
            {"id": "extraction", "label": "Extraction", "status": "running"},
            {"id": "osi_conversion", "label": "OSI Conversion", "status": "pending"},
            {"id": "sml_generation", "label": "SML Generation", "status": "pending"},
            {"id": "snowflake_deployment", "label": "Snowflake Deployment", "status": "pending"},
        ],
        "duration_ms": 0,
        "started_at": _compat_now_iso(),
    }
    _compat_project_runs.setdefault(project_id, []).insert(0, run)
    _compat_save_store()
    return run, project_cfg, started


async def _perform_project_run(run: dict, project_cfg: str, started: float) -> dict:
    try:
        sync_result = await sync_models({"content": project_cfg})
        elapsed_ms = int((_time.time() - started) * 1000)
        run["duration_ms"] = elapsed_ms
        overall = str((sync_result or {}).get("status") or "").lower()
        if overall == "success":
            run["status"] = "success"
        elif overall == "partial":
            run["status"] = "warning"
        else:
            run["status"] = "failed"
        run["summary"] = (sync_result or {}).get("summary") or {}
        run["results"] = (sync_result or {}).get("results") or []
        run["models_synced"] = int((sync_result or {}).get("models_synced") or 0)
        run["total_models"] = int((sync_result or {}).get("total_models") or 0)
        run["logs"] = _build_run_logs(sync_result or {})
        run["stage_states"] = _build_stage_states(sync_result or {})
        if run["status"] == "success":
            run["message"] = "Execution completed successfully."
        elif run["status"] == "warning":
            run["message"] = "Execution completed with warnings."
        else:
            first_error = ""
            if run["summary"].get("errors"):
                first_error = str((run["summary"].get("errors") or [{}])[0].get("message") or "")
            run["message"] = first_error or "Execution failed."
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
        run["message"] = str(exc)
        run["logs"] = [f"ERROR Execution failed: {exc}"]
        run["stage_states"] = [
            {"id": "extraction", "label": "Extraction", "status": "failed"},
            {"id": "osi_conversion", "label": "OSI Conversion", "status": "pending"},
            {"id": "sml_generation", "label": "SML Generation", "status": "pending"},
            {"id": "snowflake_deployment", "label": "Snowflake Deployment", "status": "pending"},
        ]
    _compat_save_store()
    return run


async def _execute_project_run(project_id: str, schedule_label: str = "Manual") -> dict:
    run, project_cfg, started = _create_project_run(project_id, schedule_label)
    return await _perform_project_run(run, project_cfg, started)


def _run_project_background(run: dict, project_cfg: str, started: float) -> None:
    import asyncio

    asyncio.run(_perform_project_run(run, project_cfg, started))



@app.post("/api/projects/{project_id}/run")
async def run_project_now_compat(project_id: str, background_tasks: BackgroundTasks):
    run, project_cfg, started = _create_project_run(project_id, "Manual")
    background_tasks.add_task(_run_project_background, run, project_cfg, started)
    return {"run_id": run["id"], "status": "running", "message": "Sync started in background"}


@app.get("/api/folders")
async def list_folders_compat():
    """Compatibility: newfrontend expects a folders collection."""
    return list(_compat_folders.values())


@app.post("/api/folders")
async def create_folder_compat(payload: dict):
    folder_id = str((payload or {}).get("id") or (payload or {}).get("folder_id") or f"folder-{int(_time.time() * 1000)}")
    folder = {
        "id": folder_id,
        "folder_id": folder_id,
        "name": (payload or {}).get("name") or f"Folder {folder_id[-4:]}",
        "color": (payload or {}).get("color") or "#6366f1",
    }
    _compat_folders[folder_id] = folder
    return folder


@app.patch("/api/folders/{folder_id}")
async def rename_folder_compat(folder_id: str, payload: dict):
    folder = _compat_folders.get(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if "name" in (payload or {}):
        folder["name"] = (payload or {}).get("name")
    if "color" in (payload or {}):
        folder["color"] = (payload or {}).get("color")
    _compat_folders[folder_id] = folder
    return folder


@app.delete("/api/folders/{folder_id}")
async def delete_folder_compat(folder_id: str):
    _compat_folders.pop(folder_id, None)
    for project in _compat_projects.values():
        if project.get("folder_id") == folder_id:
            project["folder_id"] = None
            project["updated_at"] = _compat_now_iso()
    return Response(status_code=204)


@app.patch("/api/projects/{project_id}/folder")
async def move_project_to_folder_compat(project_id: str, payload: dict):
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    folder_id = (payload or {}).get("folder_id")
    if folder_id is not None and folder_id not in _compat_folders:
        raise HTTPException(status_code=404, detail="Folder not found")
    project["folder_id"] = folder_id
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    return project


@app.get("/api/jobs/runs")
async def list_job_runs_compat():
    """Compatibility: return empty runs when scheduler APIs are absent."""
    all_runs: List[Dict[str, Any]] = []
    for runs in _compat_project_runs.values():
        all_runs.extend(runs)
    all_runs.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return all_runs


@app.get("/api/jobs/config")
async def get_jobs_config_compat():
    """Compatibility: return non-failing default scheduler config."""
    return _compat_job_config


@app.get("/api/jobs/schedules")
async def list_job_schedules_compat():
    _compat_ensure_loaded()
    items: List[Dict[str, Any]] = []
    for project_id, schedule in _compat_project_schedules.items():
        project = _compat_projects.get(project_id) or {}
        items.append({
            **schedule,
            "project_id": project_id,
            "project_name": project.get("name") or project_id,
        })
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items


@app.get("/api/projects/{project_id}/schedule")
async def get_project_schedule_compat(project_id: str):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    schedule = scheduler_service.get_project_schedule(project_id) or _compat_project_schedules.get(project_id)
    if schedule:
        return schedule
    return {
        "project_id": project_id,
        "schedule_type": "manual",
        "enabled": False,
    }


@app.post("/api/projects/{project_id}/schedule")
async def save_project_schedule_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        schedule = scheduler_service.save_project_schedule(project_id, payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if str(schedule.get("schedule_type") or "manual") == "manual":
        _compat_project_schedules.pop(project_id, None)
    else:
        _compat_project_schedules[project_id] = {
            "project_id": project_id,
            "schedule_type": schedule.get("schedule_type"),
            "cron": schedule.get("cron") or "",
            "date": schedule.get("date") or "",
            "time": schedule.get("time") or "",
            "timezone": schedule.get("timezone") or "UTC",
            "enabled": bool(schedule.get("enabled")),
            "scheduled_time": schedule.get("scheduled_time") or "",
            "next_run_at": schedule.get("next_run_at"),
            "created_at": schedule.get("created_at") or _compat_now_iso(),
        }

    _compat_job_config.update({
        "project_id": project_id,
        "schedule_type": str(schedule.get("schedule_type") or "manual"),
        "cron": schedule.get("cron") or "",
        "timezone": schedule.get("timezone") or "UTC",
        "enabled": bool(schedule.get("enabled")),
        "date": schedule.get("date") or "",
        "time": schedule.get("time") or "",
        "scheduled_time": schedule.get("scheduled_time") or "",
    })
    _compat_save_store()
    return schedule


@app.delete("/api/projects/{project_id}/schedule")
async def delete_project_schedule_compat(project_id: str):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    removed = scheduler_service.delete_project_schedule(project_id)
    _compat_project_schedules.pop(project_id, None)
    if not removed:
        _compat_save_store()
        return {"status": "deleted", "project_id": project_id, "schedule_type": "manual", "enabled": False}
    _compat_save_store()
    return {"status": "deleted", **removed}


@app.put("/api/jobs/config")
async def update_jobs_config_compat(payload: dict):
    """Compatibility: accept schedule config updates without failing."""
    merged = {**_compat_job_config, **(payload or {})}
    _compat_job_config.update(merged)
    project_id = str((payload or {}).get("project_id") or "").strip()
    schedule_type = str((payload or {}).get("schedule_type") or "").strip()
    if project_id and schedule_type and project_id in _compat_projects:
        try:
            scheduler_service.save_project_schedule(project_id, payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    _compat_save_store()
    return {"status": "saved", **merged}


@app.post("/api/jobs/trigger")
async def trigger_job_compat(payload: dict, background_tasks: BackgroundTasks):
    """Compatibility: trigger a project job using the shared execution flow."""
    _compat_ensure_loaded()
    project_id = str((payload or {}).get("project_id") or "").strip()
    if not project_id and _compat_projects:
        project_id = next(iter(_compat_projects.keys()))
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    run, project_cfg, started = _create_project_run(project_id, "Manual")
    run["message"] = "Job trigger accepted."
    background_tasks.add_task(_run_project_background, run, project_cfg, started)
    return run


@app.get("/api/mappings")
async def list_mappings_compat(project_id: Optional[str] = None):
    pid = str(project_id or "").strip()

    def _seed_for_project(seed_project_id: str) -> Dict[str, Any]:
        source_fields = [
            {"name": "transaction_id", "type": "uuid"},
            {"name": "amount", "type": "decimal"},
            {"name": "customer_ref", "type": "string"},
            {"name": "created_at", "type": "timestamp"},
            {"name": "status_code", "type": "integer"},
            {"name": "contact_email", "type": "string"},
            {"name": "region_id", "type": "integer"},
        ]
        target_fields = [
            {"name": "txn_id", "type": "varchar"},
            {"name": "sale_amount", "type": "float"},
            {"name": "customer_key", "type": "varchar"},
            {"name": "sale_date", "type": "date"},
            {"name": "order_status", "type": "integer"},
            {"name": "email_address", "type": "varchar"},
            {"name": "region_key", "type": "integer"},
        ]

        seeded = []
        for idx, src in enumerate(source_fields):
            tgt = target_fields[idx] if idx < len(target_fields) else None
            mapping_id = f"{seed_project_id}-map-{idx + 1}"
            existing = _compat_mappings.get(mapping_id, {})
            mapping = {
                "id": mapping_id,
                "project_id": seed_project_id,
                "source_field": src["name"],
                "source_type": src["type"],
                "target_field": tgt["name"] if tgt else None,
                "target_type": tgt["type"] if tgt else None,
                "status": existing.get("status") or "auto",
                "transform": existing.get("transform") or "",
                "validation": existing.get("validation") or "None",
            }
            _compat_mappings[mapping_id] = mapping
            seeded.append(mapping)

        return {
            "source_fields": source_fields,
            "target_fields": target_fields,
            "mappings": seeded,
        }

    if pid:
        mappings = [
            m for m in _compat_mappings.values()
            if str(m.get("project_id") or "") == pid
        ]
        if not mappings:
            return _seed_for_project(pid)
        return {
            "source_fields": [],
            "target_fields": [],
            "mappings": mappings,
        }

    # No project filter: ensure at least one dataset exists for UI usability.
    if not _compat_mappings:
        return _seed_for_project("default")

    return {
        "source_fields": [],
        "target_fields": [],
        "mappings": list(_compat_mappings.values()),
    }


@app.post("/api/mappings/auto")
async def auto_map_compat(payload: dict):
    project_id = str((payload or {}).get("project_id") or "default")
    data = await list_mappings_compat(project_id=project_id)
    mappings = data.get("mappings", []) if isinstance(data, dict) else []

    # Auto-map marks all available mappings as auto when triggered.
    for mapping in mappings:
        mapping_id = str(mapping.get("id") or "")
        if not mapping_id:
            continue
        mapping["status"] = "auto"
        _compat_mappings[mapping_id] = mapping

    return {
        "source_fields": data.get("source_fields", []) if isinstance(data, dict) else [],
        "target_fields": data.get("target_fields", []) if isinstance(data, dict) else [],
        "mappings": mappings,
        "status": "ok",
    }


@app.put("/api/mappings/{mapping_id}")
async def update_mapping_compat(mapping_id: str, payload: dict):
    existing = _compat_mappings.get(mapping_id, {"id": mapping_id, "project_id": (payload or {}).get("project_id")})
    existing.update(payload or {})
    existing["id"] = mapping_id
    _compat_mappings[mapping_id] = existing
    return existing


@app.delete("/api/mappings")
async def delete_mappings_compat(project_id: Optional[str] = None):
    if project_id:
        for mapping_id in [k for k, v in _compat_mappings.items() if str(v.get("project_id") or "") == str(project_id)]:
            _compat_mappings.pop(mapping_id, None)
    else:
        _compat_mappings.clear()
    return Response(status_code=204)


# -------------------------------------------------------
# Version Compare & Rollback
# -------------------------------------------------------

# -------------------------------------------------------
# Model Version Control API  (REQ-VC)
# -------------------------------------------------------

@app.get("/api/model-versions")
async def list_model_versions(
    model_id: str = "",
    workspace_id: str = "",
    limit: int = 50,
):
    """
    List version history.  If model_id is blank, return versions
    for ALL models found in DuckDB.
    """
    try:
        all_versions = version_control_service.list_versions(
            model_id=model_id,
            workspace_id=workspace_id,
            limit=limit,
        )

        # Mark capability flags for DuckDB-backed entries
        for item in all_versions:
            item.setdefault("source", "model_versions")
            item.setdefault("can_compare", True)
            item.setdefault("can_rollback", True)

        # Add ORM-backed config version history (ModelVersionHistory)
        try:
            from semabridge.repository.orm.models import ModelVersionHistory
            from semabridge.repository.orm.session_factory import get_session_factory

            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                query = session.query(ModelVersionHistory)
                if model_id:
                    query = query.filter(ModelVersionHistory.model_name == model_id)
                rows = query.order_by(ModelVersionHistory.applied_at.desc()).limit(limit).all()

            for row in rows:
                all_versions.append(
                    {
                        "version_id": f"cfgdb-{row.id}",
                        "model_id": row.model_name,
                        "workspace_id": workspace_id or "",
                        "author": "ui",
                        "timestamp": row.applied_at.isoformat() if row.applied_at else "",
                        "description": f"Config version {row.version_tag}",
                        "version_tag": row.version_tag,
                        "is_rollback": False,
                        "rollback_from_version": None,
                        "source": "config_db",
                        "can_compare": False,
                        "can_rollback": False,
                    }
                )
        except Exception as orm_exc:
            logger.debug(f"ORM model version history unavailable: {orm_exc}")

        # Sort by timestamp descending, cap at limit
        all_versions.sort(key=lambda v: v.get("timestamp", ""), reverse=True)
        return all_versions[:limit]
    except Exception as e:
        logger.error(f"list_model_versions failed: {e}", exc_info=True)
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=500, detail=f"Failed to load version history: {e}")


@app.get("/api/model-versions/compare")
async def compare_model_versions(v1: str = "", v2: str = ""):
    """Compare two model versions and return tabular diff."""
    try:
        diffs = version_control_service.compare_versions(v1, v2)
        return {"changes": diffs}
    except Exception as e:
        logger.warning(f"compare_model_versions failed: {e}")
        return {"changes": []}


@app.delete("/api/model-versions")
async def delete_model_versions(
    model_id: str,
    workspace_id: str = "",
):
    """Delete all version history rows for a model."""
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")
    try:
        deleted = version_control_service.delete_versions(
            model_id=model_id,
            workspace_id=workspace_id,
        )
        # Invalidate discovery cache entries for this model so stale data isn't served
        keys_to_clear = [k for k in _discovery_cache if model_id in k]
        for k in keys_to_clear:
            _discovery_cache.pop(k, None)
        logger.info(f"Deleted {deleted} version(s) for model={model_id} via API")
        return {"deleted": deleted, "model_id": model_id}
    except Exception as e:
        logger.warning(f"delete_model_versions failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/model-versions/snapshot")
async def get_version_snapshot(version_id: str = ""):
    """Get the full snapshot JSON for a specific version."""
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id is required")
    try:
        snapshot = version_control_service.get_snapshot(version_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found")
        return {"version_id": version_id, "snapshot": snapshot}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"get_version_snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/model-versions/rollback")
async def rollback_model_version(payload: Dict[str, Any]):
    """
    Rollback a model to a previous version (non-destructive).

    Creates a new DuckDB version (flagged as rollback) AND writes the
    snapshot back to the local repository YAML file so the file system
    stays in sync.
    """
    version_id = payload.get("version_id")
    model_id = payload.get("model_id", "default")
    workspace_id = payload.get("workspace_id", "default")
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id is required")
    try:
        return version_control_service.rollback_version(
            version_id=version_id,
            model_id=model_id,
            workspace_id=workspace_id,
            author="ui",
        )
    except Exception as e:
        logger.exception(f"rollback_model_version failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# Legacy Compare & Rollback (kept for backward compat)
# -------------------------------------------------------

@app.get("/api/history/compare")
async def compare_versions(version_from: str = "", version_to: str = ""):
    """Compare two version snapshots and return a list of changes."""
    try:
        changes = db_manager.compare_versions(version_from, version_to)
        return {"changes": changes}
    except Exception as e:
        logger.warning(f"Compare failed: {e}")
        return {"changes": []}


@app.post("/api/history/rollback")
async def rollback_version(payload: Dict[str, Any]):
    """Rollback to a specific version."""
    version_id = payload.get("version_id")
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id is required")
    try:
        result = db_manager.rollback_to_version(version_id)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.exception(f"Rollback failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# PBIX Import (Air-Gapped Extraction)
# -------------------------------------------------------

@app.post("/api/upload")
async def upload_pbix_temp(file: UploadFile = File(...)):
    """Upload a PBIX file to a temporary local folder and return absolute path."""
    temp_root = Path(tempfile.gettempdir()) / "semabridge" / "uploads"
    saved = _save_uploaded_pbix_file(file, temp_root)
    return {"path": str(saved).replace('\\\\', '/')}


@app.post("/api/projects/{project_id}/upload")
async def upload_project_pbix(project_id: str, file: UploadFile = File(...)):
    """Upload PBIX for a specific project and persist path in project metadata/config."""
    project_root = Path(tempfile.gettempdir()) / "semabridge" / "projects" / project_id
    saved = _save_uploaded_pbix_file(file, project_root)
    _compat_set_project_pbix_path(project_id, str(saved))
    return {
        "project_id": project_id,
        "path": str(saved).replace('\\\\', '/'),
    }

@app.post("/api/pbix/import")
async def import_pbix(payload: Dict[str, Any]):
    """Import a local .pbix file and extract its semantic model.

    Request body:
        {"pbix_path": "C:/path/to/model.pbix"}

    Returns:
        Extracted metadata: tables, measures, relationships, connections.
    """
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.core.exceptions import PBIXParsingError

    pbix_path = payload.get("pbix_path", "")
    if not pbix_path:
        raise HTTPException(status_code=400, detail="pbix_path is required")

    try:
        connector = LocalPBIXConnector({"pbix_path": pbix_path})
        connector.authenticate()
        result = connector.discover()

        logger.info(f"PBIX import: {len(result.get('tables', []))} tables extracted from {pbix_path}")
        return {
            "status": "success",
            "source": pbix_path,
            "tables": result.get("tables", []),
            "measures": result.get("measures", []),
            "relationships": result.get("relationships", []),
            "connections": result.get("connections", []),
            "m_code": result.get("m_code", []),
            "models": result.get("models", []),
            "metadata": result.get("metadata", {}),
        }
    except PBIXParsingError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception(f"PBIX import failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pbix/browse")
async def browse_pbix_files(directory: str = ""):
    """List .pbix files in a directory for the file picker.

    Args:
        directory: Directory to scan. Defaults to configured local_models_path.

    Returns:
        List of .pbix file paths found.
    """
    scan_dir = Path(directory) if directory else _resolve_models_path()
    if not scan_dir.exists():
        return {"files": [], "directory": str(scan_dir)}

    pbix_files = [
        {
            "name": f.name,
            "path": str(f.resolve()),
            "size_bytes": f.stat().st_size,
            "modified": f.stat().st_mtime,
        }
        for f in scan_dir.rglob("*.pbix")
    ]

    return {"files": pbix_files, "directory": str(scan_dir)}


# -------------------------------------------------------
# Composite Model Analysis
# -------------------------------------------------------

@app.post("/api/composite/register")
async def register_composite_report(payload: Dict[str, Any]):
    """Register a report and its composite model dependencies.

    Request body:
        {
            "report_name": "Sales Dashboard",
            "source_path": "C:/path/to/report.pbix",
            "workspace_id": "local",
            "connections": [...from PBIX extraction...]
        }
    """
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        report_id = resolver.register_report(
            report_name=payload.get("report_name", ""),
            source_path=payload.get("source_path", ""),
            workspace_id=payload.get("workspace_id", "local"),
            connections=payload.get("connections", []),
        )
        return {"status": "registered", "report_id": report_id}
    except Exception as e:
        logger.exception(f"Composite registration failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/composite/impact/{model_guid}")
async def get_impact_analysis(model_guid: str):
    """Impact analysis: which reports depend on this semantic model?

    Args:
        model_guid: The upstream semantic model GUID to check.

    Returns:
        List of reports that reference this model.
    """
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        impacted = resolver.get_impacted_reports(model_guid)
        return {"model_guid": model_guid, "impacted_reports": impacted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/composite/links")
async def get_all_composite_links():
    """List all report â†’ model dependency links."""
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        links = resolver.list_all_links()
        return {"links": links, "total": len(links)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# Multi-Workspace Discovery
# -------------------------------------------------------

@app.post("/api/multi-workspace/discover")
async def discover_multi_workspace(payload: Dict[str, Any]):
    """Discover semantic models across multiple Fabric workspaces concurrently.

    Request body:
        {"workspace_ids": ["ws-id-1", "ws-id-2", ...]}

    Returns:
        Dictionary mapping workspace_id â†’ list of discovered items.
    """
    from semabridge.connectors.multi_workspace_orchestrator import MultiWorkspaceOrchestrator

    workspace_ids = payload.get("workspace_ids", [])
    if not workspace_ids:
        raise HTTPException(status_code=400, detail="workspace_ids array is required")

    try:
        orchestrator = MultiWorkspaceOrchestrator(settings.fabric)
        results = orchestrator.discover_all_workspaces(workspace_ids=workspace_ids)

        summary = {
            ws_id: {"count": len(items), "items": items}
            for ws_id, items in results.items()
        }
        total = sum(len(items) for items in results.values())

        return {
            "status": "success",
            "workspaces_scanned": len(workspace_ids),
            "total_items_discovered": total,
            "results": summary,
        }
    except Exception as e:
        logger.exception(f"Multi-workspace discovery failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# Connections Management (UI-Driven Auth)
# -------------------------------------------------------

@app.get("/api/connections/status")
def get_connections_status():
    """Get configuration status for all supported services.

    Returns which services are configured, which fields are missing,
    and the stored values (secrets masked).
    """
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        return cm.get_connection_status()
    except Exception as e:
        logger.exception(f"Failed to fetch connection status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/connections/{service}")
def save_connection(service: str, payload: Dict[str, Any]):
    """Save credentials for a service (fabric or snowflake).

    Request body: key-value pairs of configuration fields.
    Example for fabric:
        {
            "tenant_id": "...",
            "client_id": "...",
            "client_secret": "...",
            "workspace_id": "..."
        }
    """
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        saved = cm.save_credentials(service, payload)

        # Immediately inject into env so the app can use them
        cm.inject_credentials_to_env(service)

        # Clear settings cache so new values are picked up
        from semabridge.core.settings import reload_settings
        reload_settings()

        return {
            "status": "saved",
            "service": service,
            "fields_saved": saved,
        }
    except Exception as e:
        logger.exception(f"Failed to save {service} credentials: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/connections/{service}")
def delete_connection(service: str):
    """Remove stored credentials for a service."""
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        cm.delete_credentials(service)
        return {"status": "deleted", "service": service}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/connections/{service}/test")
def test_connection(service: str):
    """Test the stored credentials for a service by attempting authentication.

    Injects credentials into os.environ and tries to establish a connection.
    """
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()

    try:
        cm.inject_credentials_to_env(service)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No credentials stored: {e}")

    if service == "fabric":
        try:
            from semabridge.core.settings import reload_settings
            cfg = reload_settings()
            from semabridge.connectors.fabric_extractor import FabricExtractor
            extractor = FabricExtractor(cfg.fabric)
            # Attempt token acquisition as a connectivity test
            extractor._get_access_token()
            return {"status": "success", "message": "Fabric authentication successful"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    elif service == "snowflake":
        try:
            from semabridge.core.settings import reload_settings
            cfg = reload_settings()
            import snowflake.connector
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

            connect_kwargs = get_snowflake_connect_kwargs(cfg.snowflake)
            conn = snowflake.connector.connect(**connect_kwargs)
            conn.cursor().execute("SELECT CURRENT_VERSION()")
            conn.close()
            return {"status": "success", "message": "Snowflake connection successful"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    elif service == "databricks":
        try:
            from semabridge.core.settings import reload_settings
            from semabridge.connectors.databricks_publisher import DatabricksPublisher
            cfg = reload_settings()

            dbx = cfg.databricks
            if not dbx.host or not dbx.warehouse_id:
                return {
                    "status": "failed",
                    "message": "Databricks host and warehouse_id must be configured.",
                }

            publisher = DatabricksPublisher(dbx, getattr(cfg, "behavior", None))
            
            # Execute a simple validation query. execute_statements() handles 
            # 401 retries and MSAL token resolution automatically.
            try:
                results = publisher.execute_statements(["SELECT 1 AS semabridge_test"])
            except Exception as e:
                # Intercept auth errors for a clearer UI message
                if "401" in str(e) or "403" in str(e):
                    return {
                        "status": "failed", 
                        "message": f"Authentication failed. Please verify credentials. ({e})"
                    }
                raise e

            return {"status": "success", "message": "Databricks connection successful"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown service: {service}")


@app.post("/api/connections/snowflake/sso-login")
async def snowflake_sso_login():
    """Initiate Snowflake SSO login via external browser.

    Saves authenticator=externalbrowser to DuckDB and attempts a test connection
    which will pop open the user's default browser for identity provider login.
    """
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()

    # Retrieve stored credentials to get account/user
    creds = cm.get_credentials("snowflake")
    if not creds or "account" not in creds or "user" not in creds:
        raise HTTPException(
            status_code=400,
            detail="Please configure Account and Username first, then use SSO to sign in.",
        )

    # Save the authenticator mode
    cm.save_credentials("snowflake", {"authenticator": "externalbrowser"})

    # Inject into env and attempt connection (browser will pop up)
    try:
        cm.inject_credentials_to_env("snowflake")
        import snowflake.connector

        connect_kwargs: Dict[str, Any] = {
            "account": creds["account"],
            "user": creds["user"],
            "authenticator": "externalbrowser",
        }
        if creds.get("warehouse"):
            connect_kwargs["warehouse"] = creds["warehouse"]
        if creds.get("database"):
            connect_kwargs["database"] = creds["database"]

        conn = snowflake.connector.connect(**connect_kwargs)
        cur = conn.cursor()
        cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE()")
        row = cur.fetchone()
        conn.close()

        username = row[0] if row else creds.get("user", "unknown")
        role = row[1] if row else "N/A"

        logger.info(f"Snowflake SSO login successful: {username}")

        # Invalidate cached settings so discovery picks up the new credentials
        from semabridge.core.settings import reload_settings
        reload_settings()

        return {
            "status": "success",
            "message": f"SSO login successful as {username} (role: {role})",
            "username": username,
            "role": role,
        }
    except Exception as e:
        logger.error(f"Snowflake SSO login failed: {e}")
        return {"status": "failed", "message": str(e)}


# -------------------------------------------------------
# Fabric Interactive Login (MSAL Device Code Flow)
# -------------------------------------------------------

# Azure CLI public client (multi-tenant, preauthorized for most MS APIs)
_FABRIC_PUBLIC_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
_FABRIC_SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]

# Background poll state for device-code flow — per-session isolation.
# Each /login call issues a UUID flow_id. Background threads write results
# to _poll_sessions[flow_id].  This prevents concurrent logins from
# different users/tabs from overwriting each other.
import threading as _threading
import uuid as _uuid
_poll_sessions: Dict[str, Dict[str, Any]] = {}
_poll_sessions_lock = _threading.Lock()
_last_poll_time: Dict[str, float] = {}

def _get_client_ip(request: Request) -> str:
    """Extract client IP, preferring X-Forwarded-For to handle reverse proxies."""
    x_forward = request.headers.get("X-Forwarded-For")
    if x_forward:
        return x_forward.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"

# Legacy single-value kept ONLY for CLI/background-sync fallback.
_fabric_session_token: Optional[str] = None
_fabric_session_token_expires_at: float = 0.0
# TTL for cleaning up abandoned poll sessions (15 minutes).
_POLL_SESSION_TTL = 900

# Module-level MSAL app instance cache (keyed by authority URL)
_msal_app_cache: Dict[str, Any] = {}

# Module-level HTTP client with retries for MSAL
_msal_http_client: Optional[Any] = None

def _get_msal_http_client():
    global _msal_http_client
    if _msal_http_client is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        session = requests.Session()
        # Retry on transient network errors (e.g. 10054 ConnectionResetError) and 5xx
        retry = Retry(
            total=3, read=3, connect=3, backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _msal_http_client = session
    return _msal_http_client


# Discovery result cache
import time as _time
_discovery_cache: Dict[str, Any] = {}
_DISCOVERY_CACHE_TTL = 60  # seconds


def _get_msal_app(authority: str):
    """Return a cached PublicClientApplication for *authority*."""
    import msal
    if authority not in _msal_app_cache:
        _msal_app_cache[authority] = msal.PublicClientApplication(
            client_id=_FABRIC_PUBLIC_CLIENT_ID,
            authority=authority,
            http_client=_get_msal_http_client()
        )
    return _msal_app_cache[authority]


def clear_msal_cache():
    """Clear internal MSAL token caches and the app cache."""
    for authority, app in list(_msal_app_cache.items()):
        if hasattr(app, "token_cache"):
            pass
    _msal_app_cache.clear()
    global _fabric_session_token, _fabric_session_token_expires_at
    _fabric_session_token = None
    _fabric_session_token_expires_at = 0.0
    with _poll_sessions_lock:
        _poll_sessions.clear()


def _cleanup_stale_poll_sessions() -> None:
    """Remove poll sessions older than their explicit expires_at timestamp."""
    now = _time.time()
    with _poll_sessions_lock:
        stale = [fid for fid, s in _poll_sessions.items() if s.get("expires_at", 0) < now]
        for fid in stale:
            del _poll_sessions[fid]
            _last_poll_time.pop(fid, None)



def _run_background_msal_poll(
    app_msal: Any, flow: Dict[str, Any], tenant: str, flow_id: str
) -> None:
    """Run the blocking MSAL device-code poll in a background thread.

    Each invocation writes its result to ``_poll_sessions[flow_id]`` so
    that concurrent logins from different users/tabs are fully isolated.
    """
    try:
        result: Dict[str, Any] = app_msal.acquire_token_by_device_flow(flow)

        if "access_token" in result:
            account_username = "unknown"
            accounts = app_msal.get_accounts()
            if accounts:
                account_username = accounts[0].get("username", "unknown")

            actual_tenant = tenant
            claims = result.get("id_token_claims", {})
            if claims.get("tid"):
                actual_tenant = claims["tid"]

            with _poll_sessions_lock:
                if flow_id in _poll_sessions:
                    _poll_sessions[flow_id].update({
                        "status": "success",
                        "username": account_username,
                        "tenant_id": actual_tenant,
                        "access_token": result["access_token"],
                        "refresh_token": result.get("refresh_token", ""),
                        "expires_in": result.get("expires_in", 3600),
                    })
        else:
            error = result.get("error", "unknown_error")
            error_desc = result.get("error_description", "")
            with _poll_sessions_lock:
                if flow_id in _poll_sessions:
                    _poll_sessions[flow_id].update({
                        "status": "failed",
                        "message": f"{error}: {error_desc}",
                    })
    except Exception as exc:
        logger.exception("Background MSAL poll failed for flow %s: %s", flow_id, exc)
        with _poll_sessions_lock:
            if flow_id in _poll_sessions:
                _poll_sessions[flow_id].update({
                    "status": "failed",
                    "message": str(exc),
                })


@app.post("/api/connections/fabric/login")
async def fabric_device_code_login(request: Request, payload: Dict[str, Any] = None):
    """Initiate MSAL Device Code flow for Fabric interactive login.

    Returns a unique ``flow_id`` along with the device code.  The frontend
    must pass this ``flow_id`` to ``/poll`` to retrieve the result for
    *this specific* login attempt — enabling multiple concurrent logins.
    """
    import asyncio

    # Housekeeping: purge stale sessions before creating a new one.
    _cleanup_stale_poll_sessions()

    tenant = "organizations"
    if payload and payload.get("tenant_id"):
        tenant = payload["tenant_id"]

    authority = f"https://login.microsoftonline.com/{tenant}"

    def _initiate_flow():
        app_msal = _get_msal_app(authority)
        flow = app_msal.initiate_device_flow(scopes=_FABRIC_SCOPES)
        return app_msal, flow

    try:
        loop = asyncio.get_event_loop()
        app_msal, flow = await loop.run_in_executor(None, _initiate_flow)

        if "user_code" not in flow:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initiate device flow: {flow.get('error_description', 'Unknown error')}"
            )

        # Issue a unique flow_id for this login session.
        flow_id = str(_uuid.uuid4())
        user_ip = _get_client_ip(request)
        
        with _poll_sessions_lock:
            _poll_sessions[flow_id] = {
                "status": "polling",
                "expires_at": _time.time() + _POLL_SESSION_TTL,
                "user_ip": user_ip,
            }

        poll_thread = _threading.Thread(
            target=_run_background_msal_poll,
            args=(app_msal, flow, tenant, flow_id),
            daemon=True,
        )
        poll_thread.start()

        logger.info("[FlowID=%s] Fabric login started from IP: %s", flow_id, user_ip)

        return {
            "flow_id": flow_id,
            "user_code": flow["user_code"],
            "verification_uri": flow["verification_uri"],
            "message": flow.get("message", ""),
            "expires_in": flow.get("expires_in", 900),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to initiate device code flow: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/connections/fabric/poll")
async def fabric_device_code_poll(request: Request, payload: Dict[str, Any] = None):
    """Return the device-code flow status for a specific ``flow_id``.

    The frontend must pass the ``flow_id`` received from ``/login``.
    On success, ``access_token`` is returned so the frontend can store
    it and send it as a Bearer header on subsequent Fabric API calls.
    """
    from semabridge.repository.credential_manager import CredentialManager
    global _fabric_session_token, _fabric_session_token_expires_at

    flow_id = (payload or {}).get("flow_id", "")
    if not flow_id:
        raise HTTPException(status_code=400, detail={"status": "error", "message": "Missing flow_id."})

    # Rate Limiting: max 1 poll per second per flow_id
    now = _time.time()
    if now - _last_poll_time.get(flow_id, 0) < 1.0:
        # Soft delay or throttle
        raise HTTPException(status_code=429, detail={"status": "too_many_requests", "message": "Slow down polling."})
    _last_poll_time[flow_id] = now

    with _poll_sessions_lock:
        state = _poll_sessions.get(flow_id, {}).copy()

    if not state:
        raise HTTPException(status_code=400, detail={"status": "expired", "message": "Unknown or expired flow_id. Please login again."})

    # CSRF/IP Validation: Soft reject (log only) to support VPN/cellular hops
    user_ip = _get_client_ip(request)
    session_ip = state.get("user_ip")
    if session_ip and session_ip != user_ip:
        logger.warning(f"[FlowID={flow_id}] IP Mismatch! Login originated from {session_ip}, but poll is from {user_ip}")

    # Enforce strict TTL
    if now > state.get("expires_at", 0):
        with _poll_sessions_lock:
            _poll_sessions.pop(flow_id, None)
            _last_poll_time.pop(flow_id, None)
        raise HTTPException(status_code=401, detail={"status": "expired", "message": "Login session expired. Please login again."})

    if state["status"] == "polling":
        return {"status": "pending", "message": "Waiting for user to authenticate..."}

    if state["status"] == "success":
        # Consume the session so duplicate polls are safe, Replay Protection
        logger.info(f"[FlowID={flow_id}] Fabric login success via MSAL for user: {state.get('username')}")
        with _poll_sessions_lock:
            _poll_sessions.pop(flow_id, None)
            _last_poll_time.pop(flow_id, None)

        # Keep legacy in-memory token for CLI/background-sync fallback.
        _fabric_session_token = state["access_token"]
        _fabric_session_token_expires_at = _time.time() + int(state.get("expires_in", 3600))

        # Persist only shared auth cache artifacts. Account rows are created
        # explicitly by the Connections save action with a unique connection_id.
        try:
            from semabridge.repository.orm.session_factory import db_manager

            cm = CredentialManager()
            cm.save_msal_token(
                access_token=state["access_token"],
                refresh_token=state.get("refresh_token", ""),
                account_username=state.get("username", "unknown"),
                tenant_id=state.get("tenant_id", "organizations"),
                expires_in=state.get("expires_in", 3600),
            )
            cm.save_credentials(
                "fabric",
                {
                    "access_token": state["access_token"],
                    "refresh_token": state.get("refresh_token", ""),
                },
            )
        except Exception as exc:
            logger.exception("Failed to persist MSAL token: %s", exc)
            if isinstance(exc, HTTPException):
                raise exc
            raise HTTPException(status_code=500, detail=f"Database write failed during token persistence: {exc}")

        logger.info("Fabric interactive login successful for %s (flow_id=%s)", state.get("username"), flow_id)
        return {
            "status": "success",
            "username": state.get("username", "unknown"),
            "tenant_id": state.get("tenant_id", ""),
            "access_token": state["access_token"],
            "expires_in": state.get("expires_in", 3600),
            "message": f"Signed in as {state.get('username', 'unknown')}",
        }

    # status == "failed"
    with _poll_sessions_lock:
        _poll_sessions.pop(flow_id, None)
    return {"status": "failed", "message": state.get("message", "Unknown error")}


@app.get("/api/connections/fabric/auth-status")
async def fabric_auth_status():
    """Get the current Fabric authentication status.

    Returns the auth method (interactive/service_principal/none),
    username if logged in, and token validity.
    """
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        method = cm.get_fabric_auth_method()
        token = cm.get_msal_token()

        if method == "interactive" and token:
            return {
                "auth_method": "interactive",
                "username": token.get("account_username", "unknown"),
                "tenant_id": token.get("tenant_id", ""),
                "token_valid": cm.has_valid_token(),
                "logged_in": True,
            }
        elif method == "service_principal":
            return {
                "auth_method": "service_principal",
                "logged_in": True,
                "token_valid": True,
            }
        else:
            return {
                "auth_method": "none",
                "logged_in": False,
                "token_valid": False,
            }
    except Exception as e:
        return {"auth_method": "none", "logged_in": False, "error": str(e)}


@app.post("/api/connections/fabric/logout")
async def fabric_logout():
    """Clear stored Fabric tokens and workspace config (full logout)."""
    from semabridge.repository.credential_manager import CredentialManager
    global _fabric_session_token, _fabric_session_token_expires_at

    try:
        cm = CredentialManager()
        cm.delete_credentials("fabric_token")
        cm.delete_credentials("fabric")
        _fabric_session_token = None
        _fabric_session_token_expires_at = 0.0
        _clear_fabric_from_config()
        logger.info("Fabric interactive session and workspace config cleared")
        return {"status": "logged_out"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _clear_fabric_from_config() -> None:
    """Remove the fabric section from config.yaml on logout."""
    import yaml
    from pathlib import Path

    candidates = [
        Path.cwd() / "config" / "config.yaml",
        Path.cwd() / "config" / "semabridge.yaml",
        Path.cwd() / "config.yaml",
        Path.cwd() / "semabridge.yaml",
        Path.cwd().parent / "config" / "config.yaml",
        Path.cwd().parent / "config" / "semabridge.yaml",
        Path.cwd().parent / "config.yaml",
        Path.cwd().parent / "semabridge.yaml",
    ]
    if hasattr(settings, "config_path") and settings.config_path:
        candidates.insert(0, Path(settings.config_path))

    config_path = None
    for candidate in candidates:
        if candidate.exists():
            config_path = candidate
            break

    if not config_path:
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}

        if "fabric" in config_data:
            del config_data["fabric"]
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
            logger.info(f"Cleared fabric config from {config_path}")
    except Exception as exc:
        logger.error(f"Failed to clear fabric from config: {exc}")


# -------------------------------------------------------
# Databricks Native User-to-Machine (U2M) OAuth (PKCE)
# -------------------------------------------------------

@app.post("/api/connections/databricks/login")
async def databricks_native_oauth_login(request: Request, payload: Dict[str, Any] = None):
    """Initiate Native Databricks OAuth Authorization Code Flow with PKCE."""
    import base64
    import hashlib
    import os
    import urllib.parse

    _cleanup_stale_poll_sessions()

    if not payload:
        raise HTTPException(status_code=400, detail="Missing payload")

    host = payload.get("host")
    client_id = payload.get("client_id")
    redirect_uri = payload.get("redirect_uri")

    if not host or not client_id or not redirect_uri:
        raise HTTPException(status_code=400, detail="host, client_id, and redirect_uri are required.")

    # 1. Generate PKCE verifier and challenge
    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')

    # 2. Store session state
    flow_id = str(_uuid.uuid4())
    user_ip = _get_client_ip(request)

    with _poll_sessions_lock:
        _poll_sessions[flow_id] = {
            "status": "polling",
            "service": "databricks",
            "expires_at": _time.time() + _POLL_SESSION_TTL,
            "user_ip": user_ip,
            "host": host,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "warehouse_id": payload.get("warehouse_id", ""),
            "catalog": payload.get("catalog", "main"),
            "schema_name": payload.get("schema", "semabridge"),
        }

    # 3. Construct Authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "offline_access all-apis",
        "state": flow_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }

    # Use HTTPS strictly
    if not host.startswith("https://"):
        host = f"https://{host}"

    auth_url = f"{host}/oidc/v1/authorize?" + urllib.parse.urlencode(auth_params)

    # Note: We return user_code as empty string because we are no longer using device flow.
    return {
        "flow_id": flow_id,
        "verification_uri": auth_url,
        "user_code": "", 
        "message": "Please log in using the opened browser tab.",
    }


@app.get("/api/connections/databricks/callback")
async def databricks_oauth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None
):
    """Callback endpoint for Databricks Native OAuth redirect."""
    from fastapi.responses import HTMLResponse
    import requests

    html_template = """
    <html>
        <head><title>Databricks Login</title><style>body {{ font-family: sans-serif; text-align: center; padding-top: 50px; }}</style></head>
        <body>
            <h2>{title}</h2>
            <p>{message}</p>
            <script>setTimeout(function() {{ window.close(); }}, 3000);</script>
        </body>
    </html>
    """

    if error:
        return HTMLResponse(html_template.format(
            title="Authentication Failed",
            message=f"Databricks returned an error: {error} - {error_description}"
        ))

    if not code or not state:
        return HTMLResponse(html_template.format(title="Error", message="Missing code or state parameter."))

    # Find the local session
    with _poll_sessions_lock:
        session_data = _poll_sessions.get(state)
        if not session_data or session_data.get("service") != "databricks":
            return HTMLResponse(html_template.format(title="Error", message="Session expired or invalid. Please try logging in again."))

    # Exchange code for token
    host = session_data["host"]
    if not host.startswith("https://"):
        host = f"https://{host}"

    token_url = f"{host}/oidc/v1/token"
    
    payload = {
        "grant_type": "authorization_code",
        "client_id": session_data["client_id"],
        "redirect_uri": session_data["redirect_uri"],
        "code": code,
        "code_verifier": session_data["code_verifier"]
    }

    try:
        resp = requests.post(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15
        )
        if resp.status_code != 200:
            logger.error(f"Failed to exchange Databricks token: {resp.text}")
            with _poll_sessions_lock:
                _poll_sessions[state]["status"] = "failed"
                _poll_sessions[state]["message"] = f"Token exchange failed: {resp.text[:200]}"
            return HTMLResponse(html_template.format(title="Error", message="Failed to exchange authorization code."))

        token_data = resp.json()
        
        # Databricks returns tokens
        with _poll_sessions_lock:
            _poll_sessions[state].update({
                "status": "success",
                "access_token": token_data.get("access_token"),
                "refresh_token": token_data.get("refresh_token", ""),
                "expires_in": token_data.get("expires_in", 3600),
                "username": token_data.get("user_id", "databricks_user") # Sometimes username is not explicitly in token payload
            })

        return HTMLResponse(html_template.format(
            title="Authentication Successful!",
            message="You have successfully logged in. You can close this tab and return to Semabridge."
        ))

    except Exception as e:
        logger.exception(f"Error during Databricks token exchange: {e}")
        with _poll_sessions_lock:
            _poll_sessions[state]["status"] = "failed"
            _poll_sessions[state]["message"] = str(e)
        return HTMLResponse(html_template.format(title="Error", message="Internal error during token exchange."))


@app.post("/api/connections/databricks/poll")
async def databricks_device_code_poll(request: Request, payload: Dict[str, Any] = None):
    """Return the Databricks OAuth U2M flow status for a specific ``flow_id``."""
    from semabridge.repository.credential_manager import CredentialManager

    flow_id = (payload or {}).get("flow_id", "")
    if not flow_id:
        raise HTTPException(status_code=400, detail={"status": "error", "message": "Missing flow_id."})

    # Rate limiting
    now = _time.time()
    if now - _last_poll_time.get(flow_id, 0) < 1.0:
        raise HTTPException(status_code=429, detail={"status": "too_many_requests", "message": "Slow down polling."})
    _last_poll_time[flow_id] = now

    with _poll_sessions_lock:
        state = _poll_sessions.get(flow_id, {}).copy()

    if not state:
        raise HTTPException(status_code=400, detail={"status": "expired", "message": "Unknown or expired flow_id. Please login again."})

    # CSRF/IP validation
    user_ip = _get_client_ip(request)
    session_ip = state.get("user_ip")
    if session_ip and session_ip != user_ip:
        logger.warning(f"[FlowID={flow_id}] Databricks IP Mismatch! Login: {session_ip}, Poll: {user_ip}")

    if state["status"] == "polling":
        return {"status": "pending", "message": "Waiting for user to authenticate in browser..."}

    if state["status"] == "success":
        logger.info("[FlowID=%s] Databricks login success.", flow_id)
        with _poll_sessions_lock:
            _poll_sessions.pop(flow_id, None)
            _last_poll_time.pop(flow_id, None)

        try:
            from semabridge.repository.orm.session_factory import db_manager

            cm = CredentialManager()
            cm.save_databricks_token(
                access_token=state["access_token"],
                refresh_token=state.get("refresh_token", ""),
                account_username=state.get("username", "unknown"),
                tenant_id="",  # N/A for native DBX
                host=state.get("host", ""),
                warehouse_id=state.get("warehouse_id", ""),
                catalog=state.get("catalog", "main"),
                schema_name=state.get("schema_name", "semabridge"),
                expires_in=state.get("expires_in", 3600),
            )
            
            # Save client_id since we need it to refresh
            cm.save_credentials(
                "databricks",
                {"client_id": state.get("client_id")}
            )

            import os
            os.environ["DATABRICKS_HOST"] = state.get("host", "")
            os.environ["DATABRICKS_WAREHOUSE_ID"] = state.get("warehouse_id", "")
            from semabridge.core.settings import reload_settings
            reload_settings()

        except Exception as exc:
            logger.exception("Failed to persist Databricks OAuth token: %s", exc)
            raise HTTPException(status_code=500, detail=f"Database write failed: {exc}")

        return {
            "status": "success",
            "username": state.get("username", "unknown"),
            "tenant_id": "",
            "message": "Signed in successfully.",
        }

    # status == "failed"
    with _poll_sessions_lock:
        _poll_sessions.pop(flow_id, None)
    return {"status": "failed", "message": state.get("message", "Unknown error")}


@app.get("/api/connections/databricks/auth-status")
def databricks_auth_status():
    """Get the current Databricks authentication status."""
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        method = cm.get_databricks_auth_method()

        if method == "pat":
            return {
                "auth_method": "pat",
                "logged_in": True,
                "token_valid": True,
            }
        elif method == "service_principal":
            return {
                "auth_method": "service_principal",
                "logged_in": True,
                "token_valid": True,
            }
        elif method != "none":
            # Interactive MSAL login
            creds = cm.get_credentials("databricks", mask_secrets=True)
            return {
                "auth_method": "interactive",
                "logged_in": True,
                "token_valid": True,
                "username": creds.get("username", ""),
            }
        else:
            return {
                "auth_method": "none",
                "logged_in": False,
                "token_valid": False,
            }
    except Exception as e:
        return {"auth_method": "none", "logged_in": False, "error": str(e)}


@app.post("/api/connections/databricks/logout")
def databricks_logout():
    """Clear stored Databricks tokens (full logout)."""
    from semabridge.repository.credential_manager import CredentialManager
    global _databricks_session_token, _databricks_session_token_expires_at

    try:
        cm = CredentialManager()
        cm.delete_credentials("databricks")
        _databricks_session_token = None
        _databricks_session_token_expires_at = 0.0

        # Clear env vars
        import os
        for key in ["DATABRICKS_TOKEN", "DATABRICKS_HOST", "DATABRICKS_WAREHOUSE_ID"]:
            os.environ.pop(key, None)

        reload_settings()
        logger.info("Databricks interactive session cleared")
        return {"status": "logged_out"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# Fabric Workspace Discovery
# -------------------------------------------------------


def _get_valid_fabric_token() -> str:
    """Get a valid Fabric access token, refreshing if expired.

    Uses the stored refresh token to silently acquire a new access token
    when the current one has expired, avoiding re-login.

    Returns:
        A valid access token string.

    Raises:
        HTTPException: If no token exists or refresh fails.
    """
    import msal
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()
    token_data = cm.get_msal_token()

    if not token_data or "access_token" not in token_data:
        raise HTTPException(status_code=401, detail="Not logged in. Please sign in first.")

    # If the token is still valid, return it
    if cm.has_valid_token():
        return token_data["access_token"]

    # Token expired â€” attempt silent refresh using refresh_token
    refresh_token = token_data.get("refresh_token")
    tenant_id = token_data.get("tenant_id", "organizations")

    if not refresh_token:
        # Backward-compat: some older saved sessions may not contain refresh token
        # metadata even though an access token still works.
        logger.warning("Fabric token has no refresh token; trying existing access token as fallback")
        return token_data["access_token"]

    authority = f"https://login.microsoftonline.com/{tenant_id}"

    try:
        # Reuse cached MSAL app instance to avoid repeated initialization overhead
        app_msal = _get_msal_app(authority)

        result = app_msal.acquire_token_by_refresh_token(
            refresh_token,
            scopes=_FABRIC_SCOPES,
        )

        if "access_token" in result:
            # Save refreshed tokens
            cm.save_msal_token(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token", refresh_token),
                account_username=token_data.get("account_username", "unknown"),
                tenant_id=tenant_id,
                expires_in=result.get("expires_in", 3600),
            )
            logger.info("Fabric token refreshed silently")
            return result["access_token"]
        else:
            error = result.get("error_description", result.get("error", "unknown"))
            logger.warning(f"Token refresh failed: {error}")
            # Backward-compat fallback to existing token if present.
            if token_data.get("access_token"):
                logger.warning("Using existing Fabric access token after refresh failure")
                return token_data["access_token"]
            raise HTTPException(
                status_code=401,
                detail=f"Token refresh failed: {error}. Please sign in again.",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Silent token refresh error: {e}")
        raise HTTPException(
            status_code=401,
            detail="Token expired and refresh failed. Please sign in again.",
        )


def _extract_bearer_token(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> Optional[str]:
    """Extract a bearer token from Authorization header.

    Accepts header format: ``Authorization: Bearer <token>``.
    Returns ``None`` when no header is provided so existing auth flows can
    continue to use stored credentials or env-token fallback.
    """
    if not authorization:
        logger.info("Fabric request received without Authorization header")
        return None

    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        logger.warning("Invalid Authorization header format for Fabric request")
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Expected: Bearer <token>",
        )

    logger.info("Fabric bearer token received in Authorization header")
    return token


def _resolve_fabric_access_token(
    header_bearer_token: Optional[str],
    identity_id: Optional[str] = None,
) -> str:
    """Resolve Fabric access token with compatibility-safe precedence.

    Precedence:
    1. Explicit identity-scoped account token, if ``identity_id`` is supplied.
    2. Authorization header bearer token from current request.
    3. Default stored MSAL token from interactive Connections login flow.
       - If expired, silently refresh using the stored refresh_token.
    4. FABRIC_ACCESS_TOKEN environment variable from the process or .env file.

    Explicit ``identity_id`` selection wins over any ambient bearer token so
    the UI-selected account is always honored.
    """
    # ── Phase 0: Header token / temporary env token ──────────────────────
    if identity_id:
        header_bearer_token = None

    if header_bearer_token:
        try:
            fabric_validator.validate_msal_token(header_bearer_token)
            logger.info("Using and validated Fabric token from Authorization header")
            return header_bearer_token
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Fabric token validation error: {e}")
            raise HTTPException(status_code=401, detail={"status": "invalid_token", "message": "Signature or claim validation failed."})

    if not identity_id:
        env_token = get_fabric_access_token_from_env()
        if env_token:
            logger.info("Using temporary Fabric access token from .env / environment")
            return env_token

    # ── Phase 1: Read everything we need from DB in ONE session ──────────
    # Variables populated by Phase 1:
    encrypted_token: Optional[str] = None
    account_tag: Optional[str] = None
    credential_token_data: dict = {}      # from Credential table fallback
    has_account_row: bool = False

    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Account, Credential
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
            session.expire_all()

            if identity_id:
                default_account = session.execute(
                    select(Account).where(
                        Account.connector_type == "FABRIC",
                        Account.id == identity_id,
                    )
                ).scalars().first()
            else:
                fabric_accounts = session.execute(
                    select(Account).where(Account.connector_type == "FABRIC")
                ).scalars().all()
                
                default_account = next((a for a in fabric_accounts if a.is_default), None)
                if not default_account and fabric_accounts:
                    default_account = fabric_accounts[0]

            if default_account:
                has_account_row = True
                account_tag = default_account.tag
                encrypted_token = default_account.encrypted_token

                if not encrypted_token:
                    # Fallback: read from Credential table
                    rows = session.execute(
                        select(Credential).where(Credential.service == "fabric_token")
                    ).scalars().all()
                    credential_token_data = {row.key: row.value for row in rows} if rows else {}
        # ── Session is now CLOSED — connection returned to pool ──────────

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"Failed to query default account token in DB: {exc}")

    # ── Phase 2: Validate / refresh tokens (no session held) ─────────────
    if has_account_row:
        logger.debug(f"Attempting Fabric token resolution for: {account_tag}")

        # Path A: Credential table fallback (no encrypted_token on Account)
        if not encrypted_token and credential_token_data:
            import time as _time
            raw_access_token = credential_token_data.get("access_token", "")

            if not raw_access_token:
                raise HTTPException(status_code=401, detail={"error": "reauth_required"})

            try:
                expires_at = int(credential_token_data.get("expires_at", "0"))
                if _time.time() >= expires_at - 60:
                    logger.warning("Credential-table Fabric token expired. Attempting silent refresh...")
                    refreshed = _try_silent_refresh()
                    if refreshed:
                        return refreshed
                    raise HTTPException(status_code=401, detail={"error": "reauth_required"})
            except (ValueError, TypeError):
                raise HTTPException(status_code=401, detail={"error": "reauth_required"})

            logger.info(f"Using fallback Credential table access token for Fabric account: {account_tag}")
            return raw_access_token

        # Path B: Decrypted token from Account row
        if encrypted_token:
            try:
                from semabridge.auth.encryption import decrypt_token
                tok = decrypt_token(encrypted_token)
                try:
                    fabric_validator.validate_msal_token(tok)
                except HTTPException:
                    logger.warning("Decrypted Fabric token from DB is expired. Attempting silent refresh...")
                    refreshed = _try_silent_refresh()
                    if refreshed:
                        return refreshed
                    logger.warning("Silent refresh failed. Forcing reauthentication.")
                    raise HTTPException(status_code=401, detail={"error": "reauth_required"})

                logger.info(f"Using access token from default Fabric account: {account_tag}")
                return tok
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Failed to decrypt token for account {account_tag}: {e}")

        # Path C: Account row exists but has neither token source
        if not encrypted_token and not credential_token_data:
            raise HTTPException(status_code=401, detail={"error": "reauth_required"})

    logger.warning("No valid Fabric access token available")
    raise HTTPException(
        status_code=401,
        detail={"error": "reauth_required"}
    )


def _try_silent_refresh() -> Optional[str]:
    """Attempt to silently acquire a fresh access token using the
    stored refresh_token.  Returns the new access_token on success,
    or None on failure.

    This function opens its own short-lived DB sessions internally,
    so it must NEVER be called while a caller is holding an open session
    (QueuePool(1) deadlock).
    """
    try:
        from semabridge.repository.credential_manager import CredentialManager
        import msal

        cm = CredentialManager()
        token_data = cm.get_msal_token()
        if not token_data or not token_data.get("refresh_token"):
            return None

        tenant_id = token_data.get("tenant_id", "organizations")
        app_msal = _get_msal_app(f"https://login.microsoftonline.com/{tenant_id}")
        result = app_msal.acquire_token_by_refresh_token(
            token_data["refresh_token"],
            scopes=["https://analysis.windows.net/powerbi/api/.default"],
        )
        if "access_token" in result:
            cm.save_msal_token(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token", token_data["refresh_token"]),
                account_username=token_data.get("account_username", "unknown"),
                tenant_id=tenant_id,
                expires_in=result.get("expires_in", 3600),
            )
            logger.info("Silently refreshed Fabric access token via MSAL refresh_token")
            return result["access_token"]
        else:
            logger.warning(
                "Silent token refresh failed: %s",
                result.get("error_description", result.get("error", "unknown")),
            )
            return None
    except Exception as exc:
        logger.warning("Silent token refresh exception: %s", exc)
        return None


@app.get("/api/connections/fabric/workspaces")
async def fabric_list_workspaces(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
    connection_id: Optional[str] = Query(None, alias="connectionId"),
):
    """Discover all Fabric workspaces for the selected Fabric account.

    The account identifier must be supplied explicitly so workspace discovery
    never falls back to a different identity.
    Returns a list of {id, displayName} objects.
    """
    import httpx

    import anyio
    resolved_identity_id = (identity_id or connection_id or "").strip() or None

    if not resolved_identity_id and not bearer_token:
        raise HTTPException(status_code=400, detail="account_id is required")
    access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, bearer_token, resolved_identity_id)
    logger.info("Calling Fabric workspaces API with resolved access token (Identity: %s)", resolved_identity_id)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.fabric.microsoft.com/v1/workspaces",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 401:
            logger.error(f"Microsoft Fabric API rejected the token with 401: {resp.text}")
            raise HTTPException(status_code=401, detail={"status": "expired", "message": "Token rejected by Microsoft. Please sign in again."})

        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Fabric API error: {resp.text}",
            )

        data = resp.json()
        workspaces = data.get("value", [])

        result = [
            {
                "id": ws.get("id", ""),
                "displayName": ws.get("displayName", "Unknown"),
                "type": ws.get("type", ""),
                "capacityId": ws.get("capacityId", ""),
            }
            for ws in workspaces
        ]

        logger.info(f"Discovered {len(result)} Fabric workspaces")
        return {"workspaces": result}

    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Network error reaching Fabric API: {exc}")


@app.get("/api/debug/token")
async def debug_token_header(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
):
    """Debug helper for validating Authorization header wiring in development."""
    if not bearer_token:
        return {
            "received_authorization_header": False,
            "token_present": False,
            "message": "No Authorization header provided",
        }

    return {
        "received_authorization_header": True,
        "token_present": True,
        "token_preview": f"{bearer_token[:8]}...",
        "message": "Bearer token parsed successfully",
    }


@app.get("/api/connections/fabric/default-workspace")
async def fabric_get_default_workspace(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
):
    """Return the primary workspace for the selected Fabric account."""
    import httpx

    if not identity_id:
        raise HTTPException(status_code=400, detail="account_id is required")

    import anyio
    try:
        access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, bearer_token, identity_id)
    except HTTPException:
        raise

    if not access_token:
        return {
            "workspace_id": "",
            "workspace_name": "",
            "configured": False,
            "source": "none",
            "all_workspaces": [],
        }

    # --- Step 2: Dynamic Fetching ---
    _PROJECT_NAME = "semabridge"
    normalized = []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.fabric.microsoft.com/v1/workspaces",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 200:
            raw_workspaces = resp.json().get("value", [])
            normalized = [
                {
                    "id": ws.get("id", ""),
                    "name": ws.get("displayName", ""),
                    "type": ws.get("type", ""),
                }
                for ws in raw_workspaces
                if ws.get("id")
            ]
    except Exception as exc:
        logger.debug("Fabric API workspace auto-detect failed: %s", exc)

    # --- Step 3 & 4: UI Population and Default Selection ---
    if normalized:
        # Priority a: exact name match with project name (case-insensitive)
        matched = next(
            (ws for ws in normalized if ws["name"].lower() == _PROJECT_NAME.lower()),
            None,
        )
        # Priority b: first non-Personal workspace
        if matched is None:
            matched = next(
                (ws for ws in normalized if ws.get("type", "").lower() != "personal" and ws["name"] != "My workspace"),
                None,
            )
        # Priority c: any workspace
        if matched is None:
            matched = normalized[0]

        logger.info(
            "Auto-detected Fabric workspace: %s (%s)",
            matched["name"],
            matched["id"],
        )
        return {
            "workspace_id": matched["id"],
            "workspace_name": matched["name"],
            "configured": True,
            "source": "auto",
            "all_workspaces": normalized,
        }

    return {
        "workspace_id": "",
        "workspace_name": "",
        "configured": False,
        "source": "none",
        "all_workspaces": [],
    }



@app.post("/api/connections/fabric/select-workspace")
async def fabric_select_workspace(payload: Dict[str, str]):
    """Save selected workspace and sync it to the config YAML file.

    Args:
        payload: Dict with workspace_id and optionally workspace_name.
    """
    from semabridge.repository.credential_manager import CredentialManager

    workspace_id = payload.get("workspace_id", "").strip()
    workspace_name = payload.get("workspace_name", "").strip()

    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required")

    # Save to DuckDB credentials (single source of truth)
    cm = CredentialManager()
    cm.save_credentials("fabric", {
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
    })

    # Sync to config.yaml for CLI/offline compatibility
    _sync_workspace_to_config(workspace_id, workspace_name)

    # Inject into live process environment so FabricConfig picks it up
    # without requiring a server restart. This is safe because env vars
    # are process-scoped and never persisted to disk.
    os.environ["FABRIC_WORKSPACE_ID"] = workspace_id

    # Clear the cached Settings singleton so the next FabricConfig()
    # instantiation reads the freshly injected env var.
    from semabridge.core.settings import get_settings
    get_settings.cache_clear()

    logger.info(f"Set active Fabric workspace: {workspace_name} ({workspace_id})")
    return {
        "status": "saved",
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
    }


def _sync_workspace_to_config(workspace_id: str, workspace_name: str) -> None:
    """Update the local config.yaml with the selected Fabric workspace.

    Uses PyYAML to modify and rewrite the file, preserving existing content.
    """
    import yaml
    from pathlib import Path

    # Locate config file â€” check common paths
    candidates = [
        Path.cwd() / "config" / "config.yaml",
        Path.cwd() / "config" / "semabridge.yaml",
        Path.cwd() / "config.yaml",
        Path.cwd() / "semabridge.yaml",
        Path.cwd().parent / "config" / "config.yaml",
        Path.cwd().parent / "config" / "semabridge.yaml",
        Path.cwd().parent / "config.yaml",
        Path.cwd().parent / "semabridge.yaml",
    ]

    # Also check the config_path stored in the current config
    if hasattr(settings, "config_path") and settings.config_path:
        candidates.insert(0, Path(settings.config_path))

    config_path = None
    for candidate in candidates:
        if candidate.exists():
            config_path = candidate
            break

    if not config_path:
        logger.warning("No config.yaml found to sync workspace_id")
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}

        # Ensure the fabric section exists
        if "fabric" not in config_data:
            config_data["fabric"] = {}

        config_data["fabric"]["workspace_id"] = workspace_id
        if workspace_name:
            config_data["fabric"]["workspace_name"] = workspace_name

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Config file updated: {config_path}")
    except Exception as exc:
        logger.error(f"Failed to sync workspace to config: {exc}")


# -------------------------------------------------------
# Uvicorn Entry
# -------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    import sys
    
    # Use unbuffered Python (-u flag) for real-time logs
    # If running directly, use the app object
    # Port is configurable via API_PORT environment variable, defaults to 8001
    port = int(os.environ.get("API_PORT", "8001"))
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        reload=False  # Disable reload when running main.py directly
    )
