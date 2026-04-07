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
from datetime import datetime
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Any, List, Optional
import asyncio
from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Query
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
from semabridge.api.websocket_alerts import alert_router, install_websocket_alert_handler
from semabridge.api.semantic_models import SemanticSyncRequest, SemanticRefreshRequest
from semabridge.api.services.scheduler_service import SchedulerService
from semabridge.api.services.sync_execution_service import execute_sync_request
from semabridge.api.services.version_control_service import VersionControlService
from sqlalchemy.orm import Session
from semabridge.api.deps import get_db

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

async def list_workspaces(
    db: "Session" = Depends(get_db),
    identity_id: Optional[str] = Query(None),
):
    """List available Fabric workspaces for the current Default account.

    Identity is resolved fresh on every request from the DuckDB Account table
    so that 'Set Default' changes in Settings are immediately reflected without
    a server restart.
    """
    from sqlalchemy import select, text
    from semabridge.repository.orm.models import Account
    from semabridge.auth.encryption import decrypt_token
    import httpx

    access_token: str | None = None
    account_tag: str | None = None

    # If a UI-selected Fabric account is supplied, honor it first.
    if identity_id:
        try:
            import anyio
            access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, None, identity_id)
            account_tag = identity_id
            logger.info("list_workspaces: using identity-scoped account '%s'", account_tag)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("list_workspaces: identity-scoped lookup failed for %s: %s", identity_id, exc)

    # Fall back to the default Fabric account when no identity was supplied.
    if not access_token:
        try:
            default_account = db.execute(
                select(Account).where(
                    Account.connector_type == "FABRIC",
                    Account.is_default == True,  # noqa: E712
                )
            ).scalar_one_or_none()

            if not default_account:
                raise HTTPException(status_code=401, detail="token_missing")

            if default_account.encrypted_token:
                # Happy path: token already persisted in Account row
                account_tag = default_account.tag
                logger.info("list_workspaces: using default account '%s'", account_tag)
                access_token = decrypt_token(default_account.encrypted_token)
                if not access_token:
                    logger.warning("list_workspaces: token decrypt returned empty string")
                    raise HTTPException(status_code=401, detail="token_missing")
            else:
                # Fallback: account exists but no token row yet (pre-atomic-write accounts).
                # Query Credential directly to avoid DuckDB DDL locking from CredentialManager.
                from semabridge.repository.orm.models import Credential
                from semabridge.auth.encryption import encrypt_token as _encrypt
                import time

                rows = db.execute(
                    select(Credential).where(Credential.service == "fabric_token")
                ).scalars().all()
                raw_token_data = {row.key: row.value for row in rows} if rows else {}
                
                raw_access_token = raw_token_data.get("access_token", "")
                if not raw_access_token:
                    logger.warning("list_workspaces: no token in Account row nor Credential table")
                    raise HTTPException(status_code=401, detail="token_missing")
                    
                try:
                    expires_at = int(raw_token_data.get("expires_at", "0"))
                    if time.time() >= expires_at - 60:
                        logger.warning("list_workspaces: Credential table token is expired")
                        raise HTTPException(status_code=401, detail="token_missing")
                except (ValueError, TypeError):
                    raise HTTPException(status_code=401, detail="token_missing")

                # Backfill: write the token to the Account row so next request uses DB path
                try:
                    default_account.encrypted_token = _encrypt(raw_access_token)
                    default_account.identity_email = raw_token_data.get(
                        "account_username", default_account.identity_email
                    )
                    db.commit()
                    logger.info(
                        "list_workspaces: backfilled encrypted_token for account '%s'",
                        default_account.tag,
                    )
                except Exception as bf_exc:
                    logger.warning("list_workspaces: backfill write failed (non-fatal): %s", bf_exc)
                    db.rollback()

                account_tag = default_account.tag
                access_token = raw_access_token
                logger.info(
                    "list_workspaces: using CredentialManager fallback for account '%s'", account_tag
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("list_workspaces: unexpected DB error: %s", exc)
            raise HTTPException(status_code=503, detail="Workspace discovery temporarily unavailable")

    if not access_token:
        logger.warning("list_workspaces: no valid default account token — returning 401")
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

# Fabric and Databricks interactive auth both reuse this in-memory poll state
# so browser/device flows can be coordinated without duplicating session logic.
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

        # Persist to DB (Account + Credential tables)
        try:
            from semabridge.repository.orm.session_factory import db_manager
            from semabridge.repository.account_repository import AccountRepository

            session = db_manager.get_session_factory()()
            try:
                repo = AccountRepository(session)
                repo.upsert_account("FABRIC", state, _fabric_session_token_expires_at)
            finally:
                session.close()

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
    4. FABRIC_ACCESS_TOKEN environment variable (dev/CI fallback).

    Explicit ``identity_id`` selection wins over any ambient bearer token so
    the UI-selected account is always honored.
    """
    # ── Phase 0: Header token (no DB needed) ─────────────────────────────
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
                default_account = session.execute(
                    select(Account).where(Account.connector_type == "FABRIC")
                ).scalars().first()

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

    logger.warning("No valid Fabric access token available (bypassed .env fallback to respect UI state)")
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
        app_msal = msal.PublicClientApplication(
            client_id="04b07795-8ddb-461a-bbee-02f9e1bf7b46",
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
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


async def fabric_list_workspaces(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
):
    """Discover all Fabric workspaces accessible to the logged-in user.

    Uses the stored MSAL access token (auto-refreshing if expired) to call
    the Fabric REST API.
    Returns a list of {id, displayName} objects.
    """
    import httpx

    import anyio
    access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, bearer_token, identity_id)
    logger.info("Calling Fabric workspaces API with resolved access token (Identity: %s)", identity_id)

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


async def fabric_get_default_workspace(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
):
    """Return the best-available default workspace for the project wizard from the Default account."""
    from sqlalchemy import select
    from semabridge.repository.orm.models import Account
    from semabridge.auth.encryption import decrypt_token
    import httpx

    # --- Step 1: Identify Default Account ---
    access_token = None
    import anyio
    try:
        access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, bearer_token, identity_id)
    except HTTPException:
        pass

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
# Connections Management (UI-Driven Auth)
# -------------------------------------------------------

async def get_connections_status():
    """Get configuration status for all supported services."""
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        return cm.get_connection_status()
    except Exception as e:
        logger.exception(f"Failed to fetch connection status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def save_connection(service: str, payload: Dict[str, Any]):
    """Save credentials for a service and refresh in-process settings."""
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        saved = cm.save_credentials(service, payload)
        cm.inject_credentials_to_env(service)
        reload_settings()
        return {
            "status": "saved",
            "service": service,
            "fields_saved": saved,
        }
    except Exception as e:
        logger.exception(f"Failed to save {service} credentials: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def delete_connection(service: str):
    """Remove stored credentials for a service."""
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        cm.delete_credentials(service)
        return {"status": "deleted", "service": service}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def test_connection(service: str):
    """Test the stored credentials for a service by attempting authentication."""
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()

    try:
        cm.inject_credentials_to_env(service)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No credentials stored: {e}")

    if service == "fabric":
        try:
            cfg = reload_settings()
            extractor = FabricExtractor(cfg.fabric)
            extractor._get_access_token()
            return {"status": "success", "message": "Fabric authentication successful"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    if service == "snowflake":
        try:
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

    raise HTTPException(status_code=400, detail=f"Unknown service: {service}")


async def snowflake_sso_login():
    """Initiate Snowflake SSO login via external browser."""
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()
    creds = cm.get_credentials("snowflake")
    if not creds or "account" not in creds or "user" not in creds:
        raise HTTPException(
            status_code=400,
            detail="Please configure Account and Username first, then use SSO to sign in.",
        )

    cm.save_credentials("snowflake", {"authenticator": "externalbrowser"})

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


