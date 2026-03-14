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

_dotenv_path = _Path(__file__).resolve().parents[3] / ".env"
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(dotenv_path=_dotenv_path, override=False)
except ImportError:
    pass  # python-dotenv optional — env vars already set by the OS are used as-is

from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException
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

# Core imports
from semabridge.core.settings import get_settings, reload_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.utils.logger import setup_logging
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.api.repo_router import router as repo_router
from semabridge.api.sync_router import router as sync_router
from semabridge.api.websocket_alerts import alert_router, install_websocket_alert_handler
from semabridge.api.semantic_models import SemanticSyncRequest, SemanticRefreshRequest

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

# Content hash tracker -- avoids duplicate versions for unchanged files
_last_snapshot_hash: dict[str, str] = {}


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

                    _ini = _Path(__file__).parents[4] / "alembic.ini"
                    _alembic_cfg = _AlembicConfig(str(_ini))
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

    yield  # ------- APPLICATION IS RUNNING -----------------------------------

    # ------ SHUTDOWN ---------------------------------------------------------

    from semabridge.repository.orm.session_factory import db_manager as _orm_db_manager
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
app.include_router(auth_router)

# WebSocket alert endpoints (real-time UI notifications)
app.include_router(alert_router)


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
        sema_yaml = Path("semabridge.yaml")
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
async def discover_fabric_models():
    import asyncio
    from pydantic import ValidationError
    from semabridge.repository.credential_manager import CredentialManager

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
        
        cm = CredentialManager()
        auth_method = cm.get_fabric_auth_method()

        # Also accept a pre-issued token injected via env (CI / dev shortcut)
        env_token = os.environ.get("FABRIC_ACCESS_TOKEN", "").strip()
        if env_token and auth_method == "none":
            auth_method = "env_token"

        # Interactive (device-code) or env-token path
        if auth_method in ("interactive", "env_token"):
            import httpx

            fabric_creds = cm.get_credentials("fabric", mask_secrets=False)
            workspace_id = (
                fabric_creds.get("workspace_id")
                or os.environ.get("FABRIC_WORKSPACE_ID", "")
            ).strip()

            if not workspace_id:
                raise HTTPException(
                    status_code=400,
                    detail="No Fabric workspace configured. Select a workspace in Settings -> Connections.",
                )

            cache_key = f"fabric:{workspace_id}"
            cached = _discovery_cache.get(cache_key)
            if cached and _time.monotonic() < cached["expires_at"]:
                return cached["data"]

            if auth_method == "env_token":
                access_token = env_token
            else:
                # Get token via thread to avoid blocking
                try:
                    access_token = await asyncio.to_thread(_get_valid_fabric_token)
                    logger.debug(f"Retrieved Fabric token via device-code flow (length: {len(access_token)})")
                except HTTPException as he:
                    # If token retrieval fails, provide clear guidance
                    logger.error(f"Failed to get Fabric token: {he.detail}")
                    raise

            logger.info(f"Attempting to discover Fabric models in workspace: {workspace_id}")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/semanticModels",
                    headers={"Authorization": f"Bearer {access_token}"},
                )

            if resp.status_code == 401:
                logger.error(f"Fabric API returned 401 Unauthorized. Token may be invalid or expired.")
                logger.error(f"Response: {resp.text[:500]}")
                # Try to give more helpful error message
                if "invalid_token" in resp.text.lower() or "expired" in resp.text.lower():
                    raise HTTPException(
                        status_code=401,
                        detail="Fabric token expired or invalid. Please sign in again via Connections."
                    )
                else:
                    raise HTTPException(
                        status_code=401,
                        detail="Fabric API returned 401. Possibly invalid workspace ID or insufficient permissions. Please sign in again."
                    )
            if resp.status_code != 200:
                logger.error(f"Fabric API error {resp.status_code}: {resp.text[:500]}")
                raise HTTPException(status_code=resp.status_code, detail=f"Fabric API error: {resp.text}")

            models = resp.json().get("value", [])
            logger.info(f"Successfully discovered {len(models)} Fabric semantic models")
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

        # No token at all -- raise 401 with sign-in guidance
        if auth_method == "none":
            logger.warning("No Fabric credentials found - user needs to sign in")
            raise HTTPException(
                status_code=401,
                detail=(
                    "No valid Fabric credentials found. "
                    "Sign in via the Connections panel (device-code login), "
                    "set FABRIC_ACCESS_TOKEN in .env, "
                    "or configure FABRIC_CLIENT_SECRET for service-principal auth."
                ),
            )

        # Service-principal fallback via FabricExtractor (client_secret set)
        logger.info(f"Using service-principal authentication for Fabric discovery")
        cache_key = f"fabric:{settings.fabric.workspace_id}"
        cached = _discovery_cache.get(cache_key)
        if cached and _time.monotonic() < cached["expires_at"]:
            return cached["data"]

        extractor = FabricExtractor(settings.fabric)
        models = await asyncio.to_thread(extractor.list_semantic_models)

        result = [
            {
                "id": m["id"],
                "name": m["displayName"],
                "type": "semantic_model",
                "status": "Available"
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
    try:
        from pydantic import ValidationError
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        
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
        return sorted(results, key=lambda x: x["name"])

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
        config_path = Path("semabridge.yaml")

        if not config_path.exists():
            raise HTTPException(
                status_code=404,
                detail="semabridge.yaml not found in project root"
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
    Generate full SemaBridge YAML configuration
    based on selected models.
    """
    try:
        settings = get_settings()
        selected_models: List[str] = payload.get("models", [])
        source_type: str = payload.get("sourceType", "fabric")
        target_type: str = payload.get("targetType", "snowflake")
        pbix_folder: str = str(payload.get("pbixFolder", "") or "").strip()

        # Default model_name logic
        if selected_models:
            # Clean up extension for model name
            model_name = selected_models[0].replace(".yaml", "").replace(".yml", "").replace(".json", "")
        else:
            model_name = settings.model.name

        lines: List[str] = [
            "# Generated SemaBridge Configuration",
            "# Use 'Generate from Selection' after picking models in the Source Browser",
            "",
            "# Required: Source connector configuration",
            "source:",
            f"  type: {source_type}",
        ]

        if source_type == "fabric":
             workspace_id = settings.fabric.workspace_id
             lines.append(f'  workspace_id: "{workspace_id}"')
        elif source_type == "snowflake":
             lines.append(f'  database: "{settings.snowflake.database}"')
             lines.append(f'  schema: "{settings.snowflake.schema_name}"')
        elif source_type == "pbix":
             local_models_path = pbix_folder or str(_resolve_models_path())
             safe_pbix_folder = local_models_path.replace("\\", "/")
             lines.append(f'  pbix_folder: "{safe_pbix_folder}"')

        # Models block
        if selected_models:
            lines.append("  models:")
            for model in selected_models:
                lines.append(f'    - "{model}"')
        else:
            lines.append('  model: "*"')

        # Target block
        lines.extend([
            "",
            "# Optional: Target connector configuration",
            "target:",
            f"  type: {target_type}",
            "  deploy: true  # Set to true to enable deployment",
            "",
            f'model_name: "{model_name}"',
            '# Optional: Semantic model name (defaults to source.model if omitted)',
            '# model_name: "continent"',
            "",
            "# Optional: Snapshot version to sync",
            'version_tag: "v1.0"',
            "",
            "# Optional: Sync direction",
            "# sync_direction: source_to_target",
            "",
            "# Optional: Logging configuration",
            "logging:",
            "  level: INFO",
            "  format: text",
            "",
            "# Optional: Behavior policy path",
            'policy_path: "policies/standard.yaml"',
            ""
        ])

        yaml_content = "\n".join(lines)

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
        # 1. Optionally save the current content if provided (to ensures we sync what the user sees)
        content = payload.get("content")
        if content:
            normalized_content = _normalize_yaml_windows_path_fields(content)
            Path("semabridge.yaml").write_text(normalized_content, encoding="utf-8")
        
        # 2. Reload settings to ensure we have latest config & env vars
        from semabridge.core.settings import reload_settings
        reload_settings()
        
        # 3. Determine source and target from semabridge.yaml
        from semabridge.core.config_loader import get_default_config_path, load_yaml_file
        config_path = get_default_config_path()
        if not config_path:
             raise HTTPException(status_code=404, detail="semabridge.yaml not found in project root")
        
        try:
            config = load_yaml_file(config_path)
        except Exception as parse_err:
            # Self-heal common Windows path escaping issues in YAML and retry once.
            raw_yaml = Path(config_path).read_text(encoding="utf-8")
            normalized_yaml = _normalize_yaml_windows_path_fields(raw_yaml)
            if normalized_yaml != raw_yaml:
                Path(config_path).write_text(normalized_yaml, encoding="utf-8")
                config = load_yaml_file(config_path)
            else:
                raise parse_err
        source_type = config.get("source", {}).get("type", "fabric")
        target_type = config.get("target", {}).get("type", "snowflake")
        
        # 4. Build list of sync jobs â€” one per model entry so ALL selected models run.
        # Each job: {"dataset_id": str|None, "pbix_path": str|None, "model_label": str}
        sync_jobs: List[Dict[str, Any]] = []

        if source_type == "fabric":
            explicit_id = config.get("source", {}).get("dataset_id")
            if explicit_id:
                sync_jobs.append({"dataset_id": explicit_id, "pbix_path": None, "model_label": explicit_id})
            else:
                model_list = config.get("source", {}).get("models") or []
                if not model_list:
                    raise HTTPException(status_code=400, detail="No models specified in source.models")
                for m in model_list:
                    mid = str(m).strip()
                    sync_jobs.append({"dataset_id": mid, "pbix_path": None, "model_label": mid})

        elif source_type == "pbix":
            source_cfg = config.get("source", {})
            # If pbix_path is explicitly set, treat it as a single job
            explicit_pbix = source_cfg.get("pbix_path")
            if explicit_pbix:
                sync_jobs.append({"dataset_id": None, "pbix_path": explicit_pbix, "model_label": Path(explicit_pbix).stem})
            else:
                model_list = source_cfg.get("models") or [config.get("model_name", "")]
                for raw_model in model_list:
                    if isinstance(raw_model, dict):
                        model_name = str(raw_model.get("pbixPath") or raw_model.get("name") or "")
                    else:
                        model_name = str(raw_model or "")

                    configured_folder = source_cfg.get("pbix_folder")
                    base_dir = Path(configured_folder).expanduser().resolve() if configured_folder else _resolve_models_path()
                    if not base_dir.exists() or not base_dir.is_dir():
                        fallback_dir = _resolve_models_path()
                        if fallback_dir.exists() and fallback_dir.is_dir():
                            base_dir = fallback_dir

                    pbix_path: Optional[str] = None
                    model_path = Path(model_name).expanduser() if model_name else None
                    if model_path and model_path.suffix.lower() == ".pbix" and model_path.exists():
                        pbix_path = str(model_path.resolve())

                    if not pbix_path and model_name:
                        model_stem = Path(model_name).stem
                        candidate = Path(base_dir) / model_name
                        if candidate.suffix.lower() != ".pbix":
                            candidate = candidate.with_suffix(".pbix")
                        if candidate.exists():
                            pbix_path = str(candidate)
                        else:
                            for p in Path(base_dir).glob(f"*{model_stem}*.pbix"):
                                pbix_path = str(p); break
                            if not pbix_path:
                                for p in Path.cwd().rglob(f"*{model_stem}*.pbix"):
                                    pbix_path = str(p); break

                    if not pbix_path and base_dir.exists() and base_dir.is_dir():
                        first_pbix = next(base_dir.glob("*.pbix"), None)
                        if first_pbix:
                            pbix_path = str(first_pbix)

                    if not pbix_path:
                        all_pbix = list(Path.cwd().rglob("*.pbix"))
                        if len(all_pbix) == 1:
                            pbix_path = str(all_pbix[0])

                    if not pbix_path:
                        raise HTTPException(
                            status_code=400,
                            detail=f"No .pbix file found for model '{model_name}'. "
                                   f"Ensure a .pbix file exists in source.pbix_folder or set source.pbix_path."
                        )
                    sync_jobs.append({"dataset_id": None, "pbix_path": pbix_path, "model_label": model_name or Path(pbix_path).stem})

        elif source_type in ("snowflake", "snowflake_semantic_view"):
            source_cfg = config.get("source", {})
            model_list = source_cfg.get("models") or []
            # If no models list, try single model_name at top level
            if not model_list:
                single = config.get("model_name") or source_cfg.get("view") or source_cfg.get("table")
                if single:
                    model_list = [single]
            if not model_list:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "No models specified for Snowflake source. "
                        "Add source.models: [...] or set model_name in semabridge.yaml."
                    ),
                )
            for raw_model in model_list:
                if isinstance(raw_model, dict):
                    view_name = str(raw_model.get("name") or raw_model.get("view") or raw_model.get("table") or "")
                else:
                    view_name = str(raw_model or "").strip()
                if view_name:
                    sync_jobs.append({"dataset_id": view_name, "pbix_path": None, "model_label": view_name})

        if not sync_jobs:
            raise HTTPException(status_code=400, detail="No sync jobs resolved from config.")

        # 5. Execute each model in sequence, collect per-model results.
        per_model_results: List[Dict[str, Any]] = []
        last_summary = None
        resolved_workspace_id = str(config.get("source", {}).get("workspace_id") or "default")

        # Normalise source_type for the execution engine which only accepts
        # "fabric", "snowflake", or "pbix".
        _ENGINE_SOURCE_MAP = {
            "snowflake_semantic_view": "snowflake",
        }
        engine_source_type = _ENGINE_SOURCE_MAP.get(source_type, source_type)

        for job in sync_jobs:
            model_label = job["model_label"]
            logger.info(f"Syncing model: {model_label}")
            try:
                summary = engine.execute(
                    source=engine_source_type,
                    target=target_type,
                    dataset_id=job["dataset_id"],
                    pbix_path=job["pbix_path"],
                    project_name=config.get("model_name") or model_label,
                    tag=str(config.get("version_tag", "v1.0")),
                    deploy=config.get("target", {}).get("deploy", True),
                    dry_run=False,
                )
                last_summary = summary
                summary_data = summary.model_dump(mode="json")
                job_ok = str(summary_data.get("status", "")).upper() == "SUCCESS"

                # Persist version row for this model
                if job_ok:
                    try:
                        snapshot_payload: Dict[str, Any] = config
                        sml_snap_id = summary_data.get("sml_snapshot_id")
                        if sml_snap_id:
                            snap = db_manager.get_snapshot(sml_snap_id)
                            if snap and isinstance(snap.sml_blob, dict):
                                snapshot_payload = snap.sml_blob
                        db_manager.insert_model_version(
                            model_id=model_label,
                            workspace_id=resolved_workspace_id,
                            snapshot=snapshot_payload,
                            author="ui",
                            change_summary=f"Sync run {summary_data.get('run_id', '')}".strip(),
                            version_tag=str(config.get("version_tag", "") or "") or None,
                        )
                    except Exception as ver_err:
                        logger.warning(f"Version row write failed for {model_label}: {ver_err}")

                per_model_results.append({
                    "model": model_label,
                    "status": "success" if job_ok else "failed",
                    "summary": summary_data,
                })
            except Exception as model_err:
                logger.error(f"Sync failed for model '{model_label}': {model_err}")
                per_model_results.append({
                    "model": model_label,
                    "status": "failed",
                    "summary": {"errors": [{"step_number": 0, "step_name": "Execution", "message": str(model_err)}]},
                })

        # 6. Aggregate overall status
        succeeded = [r for r in per_model_results if r["status"] == "success"]
        if len(succeeded) == len(per_model_results):
            overall_status = "success"
        elif succeeded:
            overall_status = "partial"
        else:
            overall_status = "failed"

        logger.info(f"Sync complete: {len(succeeded)}/{len(per_model_results)} models succeeded (status={overall_status})")

        return {
            "status": overall_status,
            "models_synced": len(succeeded),
            "total_models": len(per_model_results),
            "results": per_model_results,
            # Keep backward-compat single summary from last executed model
            "summary": last_summary.model_dump(mode="json") if last_summary else {},
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Sync execution failed")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# Live Validation â€” check models for missing tables + warnings
# -------------------------------------------------------

@app.post("/api/config/validate-live")
async def validate_live(payload: Dict[str, Any] = None):
    """
    Run live-validation on the current model state.

    Returns both errors and warnings (e.g. missing source tables).
    This endpoint is called periodically by the frontend.
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    try:
        conn = db_manager._get_connection()
        try:
            rows = conn.execute("""
                WITH RankedVersions AS (
                    SELECT model_id, snapshot, created_at,
                           ROW_NUMBER() OVER(PARTITION BY model_id ORDER BY created_at DESC) as rn
                    FROM model_versions
                )
                SELECT model_id, snapshot
                FROM RankedVersions
                WHERE rn = 1
            """).fetchall()

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

        finally:
            conn.close()
    except Exception as e:
        errors.append({"model": "system", "severity": "error", "message": str(e)})

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "total_issues": len(errors) + len(warnings),
    }


# -------------------------------------------------------
# Workspaces (Fabric)
# -------------------------------------------------------

@app.get("/api/workspaces")
async def list_workspaces():
    """List available Fabric workspaces."""
    try:
        settings = get_settings()
        # Return the configured workspace as the primary one
        workspace_id = settings.fabric.workspace_id if settings.fabric else ""
        workspaces = [
            {"id": workspace_id, "name": "Primary Workspace"},
        ]
        # Attempt to discover additional workspaces via the Fabric API
        try:
            extractor = FabricExtractor(settings.fabric)
            token = extractor._get_access_token()
            import requests as req
            resp = req.get(
                "https://api.fabric.microsoft.com/v1/workspaces",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if resp.ok:
                for ws in resp.json().get("value", []):
                    if ws["id"] != workspace_id:
                        workspaces.append({"id": ws["id"], "name": ws.get("displayName", ws["id"])})
        except Exception:
            pass  # Graceful degradation â€“ at minimum return the configured workspace
        return workspaces
    except Exception as e:
        logger.warning(f"Failed to list workspaces: {e}")
        return [{"id": "default", "name": "Default Workspace"}]


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
        all_versions: list[dict] = []

        # 1) DuckDB model_versions rows (works for filtered and unfiltered modes)
        if model_id:
            all_versions.extend(
                db_manager.list_model_versions(
                    model_id=model_id,
                    workspace_id=workspace_id or None,
                    limit=limit,
                )
            )
        else:
            try:
                conn = db_manager._get_connection()
                try:
                    model_ids = [
                        row[0]
                        for row in conn.execute(
                            "SELECT DISTINCT model_id FROM model_versions"
                        ).fetchall()
                    ]
                finally:
                    conn.close()
            except Exception:
                model_ids = []

            # Also add any local model files not yet in the DB
            models_path = _resolve_models_path()
            if models_path.exists():
                for f in sorted(models_path.iterdir()):
                    if f.is_file() and f.suffix.lower() in (".yaml", ".yml", ".json"):
                        mid = f.stem
                        if mid not in model_ids:
                            model_ids.append(mid)

            for mid in model_ids:
                vs = db_manager.list_model_versions(
                    model_id=mid,
                    workspace_id=workspace_id or None,
                    limit=limit,
                )
                all_versions.extend(vs)

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
        diffs = db_manager.compare_model_versions_tabular(v1, v2)
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
        deleted = db_manager.delete_model_versions(
            model_id=model_id,
            workspace_id=workspace_id or None,
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
        snapshot = db_manager.get_model_version_snapshot(version_id)
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
        # 1. Create new version row in DuckDB (flagged as rollback)
        new_id = db_manager.rollback_model_version(
            model_id=model_id,
            target_version_id=version_id,
            workspace_id=workspace_id,
            author="ui",
        )

        # 2. Write the snapshot back to the YAML file on disk
        snapshot = db_manager.get_model_version_snapshot(new_id)
        if snapshot and model_id != "default":
            models_path = _resolve_models_path()
            target_file = None
            for ext in (".yaml", ".yml", ".json"):
                candidate = models_path / f"{model_id}{ext}"
                if candidate.exists():
                    target_file = candidate
                    break
            if target_file is None:
                target_file = models_path / f"{model_id}.yaml"

            rolled_back_content = yaml.dump(
                snapshot, default_flow_style=False, sort_keys=False, allow_unicode=True,
            )
            target_file.write_text(rolled_back_content, encoding="utf-8")

            # Update hash tracker so next discovery doesn't double-snapshot
            _last_snapshot_hash[model_id] = hashlib.sha256(
                rolled_back_content.encode("utf-8")
            ).hexdigest()

            logger.info(
                f"ROLLBACK: model={model_id} restored to version {version_id[:8]}... "
                f"new_version={new_id[:8]}... file={target_file}"
            )

        return {
            "status": "success",
            "new_version_id": new_id,
            "model_id": model_id,
            "rolled_back_from": version_id,
            "is_rollback": True,
        }
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
async def get_connections_status():
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
async def save_connection(service: str, payload: Dict[str, Any]):
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
async def delete_connection(service: str):
    """Remove stored credentials for a service."""
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        cm.delete_credentials(service)
        return {"status": "deleted", "service": service}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/connections/{service}/test")
async def test_connection(service: str):
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

            # Check if SSO (externalbrowser) is configured
            authenticator = os.environ.get("SNOWFLAKE_AUTHENTICATOR", "")

            connect_kwargs: Dict[str, Any] = {
                "account": cfg.snowflake.account,
                "user": cfg.snowflake.user,
                "warehouse": cfg.snowflake.warehouse,
                "database": cfg.snowflake.database,
            }

            if authenticator == "externalbrowser":
                connect_kwargs["authenticator"] = "externalbrowser"
            else:
                connect_kwargs["password"] = cfg.snowflake.password.get_secret_value()

            conn = snowflake.connector.connect(**connect_kwargs)
            conn.cursor().execute("SELECT CURRENT_VERSION()")
            conn.close()
            return {"status": "success", "message": "Snowflake connection successful"}
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
_FABRIC_SCOPES = ["https://api.fabric.microsoft.com/.default"]

# Background poll state for device-code flow
import threading as _threading
# _poll_state: idle | polling | success | failed
_poll_state: Dict[str, Any] = {"status": "idle"}
_poll_thread: Any = None

# Module-level MSAL app instance cache (keyed by authority URL)
_msal_app_cache: Dict[str, Any] = {}

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
        )
    return _msal_app_cache[authority]


def _run_background_msal_poll(app_msal: Any, flow: Dict[str, Any], tenant: str) -> None:
    """Run the blocking MSAL device-code poll in a background thread.

    MSAL 1.x does not support an ``exit_condition`` parameter, so
    ``acquire_token_by_device_flow`` blocks until the user authenticates
    or the code expires (~15 min).  Running it in a daemon thread lets
    the event loop stay free while still delivering instant status
    responses to the frontend ``/poll`` endpoint.
    """
    global _poll_state
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

            _poll_state = {
                "status": "success",
                "username": account_username,
                "tenant_id": actual_tenant,
                "access_token": result["access_token"],
                "refresh_token": result.get("refresh_token", ""),
                "expires_in": result.get("expires_in", 3600),
            }
        else:
            error = result.get("error", "unknown_error")
            error_desc = result.get("error_description", "")
            _poll_state = {"status": "failed", "message": f"{error}: {error_desc}"}
    except Exception as exc:
        logger.exception("Background MSAL poll failed: %s", exc)
        _poll_state = {"status": "failed", "message": str(exc)}


@app.post("/api/connections/fabric/login")
async def fabric_device_code_login(payload: Dict[str, Any] = None):
    """Initiate MSAL Device Code flow for Fabric interactive login.

    Returns the user_code and verification_uri for the user to authenticate
    via their browser.  A background daemon thread starts the blocking MSAL
    poll immediately so the ``/poll`` endpoint can return instant status
    responses without blocking the event loop.
    """
    import asyncio
    global _poll_state, _poll_thread

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

        # Reset state and start background poll thread
        _poll_state = {"status": "polling"}
        _poll_thread = _threading.Thread(
            target=_run_background_msal_poll,
            args=(app_msal, flow, tenant),
            daemon=True,
        )
        _poll_thread.start()

        logger.info("Device code flow initiated - code: %s", flow["user_code"])

        return {
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
async def fabric_device_code_poll():
    """Return the current device-code flow status.

    The actual MSAL poll runs in a background thread started by
    ``/fabric/login``.  This endpoint just reads a shared dict so it
    responds instantly without blocking the event loop.
    """
    from semabridge.repository.credential_manager import CredentialManager
    global _poll_state

    state = _poll_state.copy()

    if state["status"] == "idle":
        raise HTTPException(status_code=400, detail="No active device code flow. Call /fabric/login first.")

    if state["status"] == "polling":
        return {"status": "pending", "message": "Waiting for user to authenticate..."}

    if state["status"] == "success":
        # Persist token now (only once — reset state so duplicate poll calls are safe)
        _poll_state = {"status": "idle"}
        try:
            cm = CredentialManager()
            cm.save_msal_token(
                access_token=state["access_token"],
                refresh_token=state.get("refresh_token", ""),
                account_username=state.get("username", "unknown"),
                tenant_id=state.get("tenant_id", "organizations"),
                expires_in=state.get("expires_in", 3600),
            )
        except Exception as exc:
            logger.exception("Failed to persist MSAL token: %s", exc)
            return {"status": "failed", "message": f"Login succeeded but token save failed: {exc}"}

        logger.info("Fabric interactive login successful for %s", state.get("username"))
        return {
            "status": "success",
            "username": state.get("username", "unknown"),
            "tenant_id": state.get("tenant_id", ""),
            "message": f"Signed in as {state.get('username', 'unknown')}",
        }

    # status == "failed"
    _poll_state = {"status": "idle"}
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

    try:
        cm = CredentialManager()
        cm.delete_credentials("fabric_token")
        cm.delete_credentials("fabric")
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
        Path.cwd() / "config.yaml",
        Path.cwd() / "semabridge.yaml",
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
        raise HTTPException(
            status_code=401,
            detail="Token expired and no refresh token available. Please sign in again.",
        )

    authority = f"https://login.microsoftonline.com/{tenant_id}"

    try:
        # Reuse cached MSAL app instance to avoid repeated initialization overhead
        app_msal = _msal_app_cache.get(authority)
        if app_msal is None:
            app_msal = msal.PublicClientApplication(
                client_id=_FABRIC_PUBLIC_CLIENT_ID,
                authority=authority,
            )
            _msal_app_cache[authority] = app_msal

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


@app.get("/api/connections/fabric/workspaces")
async def fabric_list_workspaces():
    """Discover all Fabric workspaces accessible to the logged-in user.

    Uses the stored MSAL access token (auto-refreshing if expired) to call
    the Fabric REST API.
    Returns a list of {id, displayName} objects.
    """
    import httpx

    access_token = _get_valid_fabric_token()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.fabric.microsoft.com/v1/workspaces",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Token expired or invalid. Please sign in again.")

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

    # Save to DuckDB credentials
    cm = CredentialManager()
    cm.save_credentials("fabric", {
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
    })

    # Sync to config.yaml
    _sync_workspace_to_config(workspace_id, workspace_name)

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
        Path.cwd() / "config.yaml",
        Path.cwd() / "semabridge.yaml",
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
