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
import threading as _threading
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
from semabridge.core.env import get_fabric_access_token_from_env
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
from semabridge.api.services.connection_session_store import (
    _POLL_SESSION_TTL,
    _cleanup_stale_poll_sessions,
    _get_client_ip,
    _last_poll_time,
    _poll_sessions,
    _poll_sessions_lock,
    _time,
    _uuid,
)
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

# setup_logging(level="INFO")  # Centralized in app_setup.py

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
# We use the native Fabric API scope. This token will be accepted by Fabric endpoints (list, create, update models).
# To talk to Power BI endpoints (like executeQueries), the system will use the refresh_token to acquire a secondary token.
_FABRIC_SCOPES = ["https://api.fabric.microsoft.com/.default"]


# Background poll state for device-code flow — per-session isolation.
# Each /login call issues a UUID flow_id. Background threads write results
# to _poll_sessions[flow_id].  This prevents concurrent logins from
# different users/tabs from overwriting each other.
# Legacy single-value kept ONLY for CLI/background-sync fallback.
_fabric_session_token: Optional[str] = None
_fabric_session_token_expires_at: float = 0.0

# Module-level MSAL app instance cache (keyed by authority URL)
_msal_app_cache: Dict[str, Any] = {}
_msal_http_client: Optional[Any] = None

# Discovery result cache
_discovery_cache: Dict[str, Any] = {}
_DISCOVERY_CACHE_TTL = 60  # seconds


def _get_msal_http_client():
    global _msal_http_client
    if _msal_http_client is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        from semabridge.core.settings import get_settings

        session = requests.Session()
        
        # Inject explicit proxy settings if configured
        proxies = get_settings().network.proxies
        if proxies:
            session.proxies.update(proxies)
            logger.info("MSAL session initialized with explicit proxies: %s", list(proxies.keys()))

        retry = Retry(
            total=5,
            read=5,
            connect=5,
            backoff_factor=0.75,
            status_forcelist=(429, 500, 502, 503, 504),
            # MSAL token requests are POST calls; allow retries for all methods.
            allowed_methods=None,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _msal_http_client = session
    return _msal_http_client


def _get_msal_app(authority: str):
    """Return a cached PublicClientApplication for *authority*."""
    import msal
    if authority not in _msal_app_cache:
        _msal_app_cache[authority] = msal.PublicClientApplication(
            client_id=_FABRIC_PUBLIC_CLIENT_ID,
            authority=authority,
            http_client=_get_msal_http_client(),
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


def _run_background_msal_poll(
    app_msal: Any, flow: Dict[str, Any], tenant: str, flow_id: str
) -> None:
    """Run the blocking MSAL device-code poll in a background thread.

    Each invocation writes its result to ``_poll_sessions[flow_id]`` so
    that concurrent logins from different users/tabs are fully isolated.
    """
    try:
        # Retry device-flow poll on transient network disconnects.
        result: Dict[str, Any] = {}
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                result = app_msal.acquire_token_by_device_flow(flow)
                break
            except Exception as poll_exc:
                err_text = str(poll_exc).lower()
                transient = any(
                    marker in err_text
                    for marker in (
                        "remote end closed connection",
                        "remotedisconnected",
                        "connection aborted",
                        "connection reset",
                        "temporarily unavailable",
                        "timeout",
                    )
                )
                if (not transient) or attempt == max_attempts:
                    raise
                delay = 1.5 * attempt
                logger.warning(
                    "[FlowID=%s] MSAL poll transient network failure "
                    "(attempt %s/%s): %s. Retrying in %.1fs",
                    flow_id,
                    attempt,
                    max_attempts,
                    poll_exc,
                    delay,
                )
                _time.sleep(delay)

        if "access_token" in result:
            # Extract the username from the id_token_claims of THIS specific
            # login — NOT from app_msal.get_accounts() which returns ALL
            # cached accounts and would always pick the first one.
            claims = result.get("id_token_claims", {})
            account_username = (
                claims.get("preferred_username")
                or claims.get("upn")
                or claims.get("email")
                or "unknown"
            )

            # Fallback: if claims didn't have a username, try the account list
            # but match by the home_account_id from the result.
            if account_username == "unknown":
                home_id = result.get("id_token_claims", {}).get("oid", "")
                for acct in app_msal.get_accounts():
                    if acct.get("local_account_id") == home_id:
                        account_username = acct.get("username", "unknown")
                        break
                else:
                    # Last resort: first account (legacy behavior)
                    accounts = app_msal.get_accounts()
                    if accounts:
                        account_username = accounts[0].get("username", "unknown")

            actual_tenant = tenant
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
    except HTTPException:
        raise
    except Exception as e:
        # Check for DNS/Network errors specifically
        error_msg = str(e)
        if "getaddrinfo failed" in error_msg or "NameResolutionError" in error_msg:
            friendly_msg = (
                "Network Error: Cannot resolve Microsoft login services (DNS failure). "
                "Please verify your internet connection or configure a proxy in .env."
            )
            logger.error("Fabric login DNS failure: %s", error_msg)
            raise HTTPException(status_code=503, detail=friendly_msg)
        
        if "ConnectionPool" in error_msg or "timeout" in error_msg.lower():
            friendly_msg = "Network Error: Connection to Microsoft timed out. Please check your firewall or proxy settings."
            logger.error("Fabric login connection timeout: %s", error_msg)
            raise HTTPException(status_code=503, detail=friendly_msg)

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

        # Persist to CredentialManager only (for CLI/background-sync fallback).
        # Account DB row is created later by the frontend via POST /api/accounts.
        # We intentionally do NOT call upsert_account() here because it
        # would overwrite an existing FABRIC account's token/email with
        # the newly-authenticated user's credentials.
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
            logger.exception("Failed to persist MSAL token to CredentialManager: %s", exc)

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

    if not identity_id:
        env_token = get_fabric_access_token_from_env()
        if env_token:
            logger.info("Using temporary Fabric access token from .env / environment")
            return env_token

    # ── Phase 1: Read everything we need from DB in ONE session ──────────
    # Variables populated by Phase 1:
    encrypted_token: Optional[str] = None
    account_tag: Optional[str] = None
    matched_account_id: Optional[str] = None
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
                default_account = next((account for account in fabric_accounts if account.is_default), None)
                if not default_account and fabric_accounts:
                    default_account = fabric_accounts[0]

            if default_account:
                has_account_row = True
                account_tag = default_account.tag
                matched_account_id = default_account.id
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
                    # Pass account context so refresh writes to the correct Account row,
                    # not the global CredentialManager (multi-account-safe).
                    refreshed = _try_silent_refresh(
                        account_id=matched_account_id,
                        account_tag=account_tag,
                        credential_payload=credential_token_data,
                    )
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
                import json
                from semabridge.auth.encryption import decrypt_token
                tok_raw = decrypt_token(encrypted_token)
                
                is_json_payload = False
                access_token = tok_raw
                refresh_token = None
                tenant_id = "organizations"
                payload_dict = {}
                
                try:
                    payload_dict = json.loads(tok_raw)
                    if isinstance(payload_dict, dict) and "access_token" in payload_dict:
                        is_json_payload = True
                        access_token = payload_dict["access_token"]
                        refresh_token = payload_dict.get("refresh_token")
                        tenant_id = payload_dict.get("tenant_id", "organizations")
                except json.JSONDecodeError:
                    pass

                try:
                    fabric_validator.validate_msal_token(access_token)
                except HTTPException:
                    logger.warning("Decrypted Fabric token from DB is expired.")
                    if is_json_payload and refresh_token and matched_account_id:
                        logger.info(f"Attempting isolated silent refresh for account {account_tag}...")
                        refreshed_access_token = _refresh_account_token(
                            matched_account_id, account_tag, refresh_token, tenant_id, payload_dict
                        )
                        if refreshed_access_token:
                            return refreshed_access_token
                    
                    logger.warning("Silent refresh failed. Forcing reauthentication.")
                    raise HTTPException(status_code=401, detail={"error": "reauth_required"})

                logger.info(f"Using access token from default Fabric account: {account_tag}")
                return access_token
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


def _refresh_account_token(account_id: str, account_tag: str, refresh_token: str, tenant_id: str, original_payload: dict) -> Optional[str]:
    """Isolated per-account MSAL refresh to prevent multi-account contamination."""
    try:
        import msal
        import json
        from sqlalchemy import update
        from semabridge.repository.orm.models import Account
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.auth.encryption import encrypt_token

        app_msal = msal.PublicClientApplication(
            client_id="04b07795-8ddb-461a-bbee-02f9e1bf7b46",
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
        # IMPORTANT: Must use the Fabric Items API scope, NOT the Power BI scope.
        # Using analysis.windows.net/powerbi/api/.default produces a token that
        # the Fabric REST API rejects with 404 EntityNotFound (not 403!) when
        # trying to create/update semantic model items.
        result = app_msal.acquire_token_by_refresh_token(
            refresh_token,
            scopes=["https://api.fabric.microsoft.com/.default"],
        )
        if "access_token" in result:
            new_payload = dict(original_payload)
            new_payload["access_token"] = result["access_token"]
            new_payload["refresh_token"] = result.get("refresh_token", refresh_token)
            new_payload["expires_in"] = result.get("expires_in", 3600)
            
            safe_token = encrypt_token(json.dumps(new_payload))
            
            with db_manager.get_session() as session:
                session.execute(
                    update(Account)
                    .where(Account.id == account_id)
                    .values(encrypted_token=safe_token)
                )
                session.commit()
                
            logger.info(f"Silently refreshed and isolated Fabric access token for account: {account_tag}")
            return result["access_token"]
        else:
            logger.warning(
                "Isolated token refresh failed for %s: %s",
                account_tag,
                result.get("error_description", result.get("error", "unknown")),
            )
            return None
    except Exception as exc:
        logger.warning("Isolated silent token refresh exception: %s", exc)
        return None


def _try_silent_refresh(
    account_id: Optional[str] = None,
    account_tag: Optional[str] = None,
    credential_payload: Optional[dict] = None,
) -> Optional[str]:
    """Attempt to silently acquire a fresh Fabric access token.

    Multi-account-safe behaviour:
    - When ``account_id`` is supplied (the common case for Path A accounts), the
      refresh is delegated to ``_refresh_account_token`` which writes the result
      back to the **specific Account row** — identical to the Path B refresh path.
      This eliminates cross-account contamination via the global CredentialManager.
    - When no ``account_id`` is known (legacy / CLI fallback), the function falls
      back to the global CredentialManager as before.

    This function opens its own short-lived DB sessions internally,
    so it must NEVER be called while a caller is holding an open session
    (QueuePool deadlock risk).
    """
    # ── Account-aware path (preferred) ─────────────────────────────────────
    if account_id and credential_payload:
        refresh_token = credential_payload.get("refresh_token", "")
        tenant_id = credential_payload.get("tenant_id", "organizations")
        if refresh_token:
            logger.info(
                "_try_silent_refresh: using isolated per-account refresh for %s",
                account_tag or account_id,
            )
            return _refresh_account_token(
                account_id, account_tag or account_id, refresh_token, tenant_id, credential_payload
            )

    # ── Legacy fallback: global CredentialManager (single-account / CLI) ───
    try:
        from semabridge.repository.credential_manager import CredentialManager
        import msal

        cm = CredentialManager()
        token_data = cm.get_msal_token()
        if not token_data or not token_data.get("refresh_token"):
            return None

        tenant_id = token_data.get("tenant_id", "organizations")
        app_msal = _get_msal_app(f"https://login.microsoftonline.com/{tenant_id}")
        # IMPORTANT: Use the Fabric Items API scope. Power BI scope tokens
        # are rejected by the Fabric Items API with 404 (not 403).
        result = app_msal.acquire_token_by_refresh_token(
            token_data["refresh_token"],
            scopes=["https://api.fabric.microsoft.com/.default"],
        )
        if "access_token" in result:
            cm.save_msal_token(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token", token_data["refresh_token"]),
                account_username=token_data.get("account_username", "unknown"),
                tenant_id=tenant_id,
                expires_in=result.get("expires_in", 3600),
            )
            logger.info(
                "_try_silent_refresh: refreshed via global CredentialManager "
                "(legacy fallback — single-account mode only)"
            )
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
    connection_id: Optional[str] = Query(None, alias="connectionId"),
):
    """Discover all Fabric workspaces for the selected Fabric account."""
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
                "name": ws.get("displayName", "Unknown"),
                "displayName": ws.get("displayName", "Unknown"),
                "type": ws.get("type", ""),
                "capacityId": ws.get("capacityId", ""),
            }
            for ws in workspaces
        ]

        logger.info(f"Discovered {len(result)} Fabric workspaces")
        return {"workspaces": result}

    except httpx.RequestError as exc:
        logger.warning("fabric_list_workspaces: Fabric API request failed, returning empty workspace list: %s", exc)
        return {"workspaces": []}


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
    """Return the primary workspace for the selected Fabric account."""
    import httpx

    import anyio
    if not identity_id:
        raise HTTPException(status_code=400, detail="account_id is required")
    access_token = await anyio.to_thread.run_sync(_resolve_fabric_access_token, bearer_token, identity_id)

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

async def get_connections_status(user_id: int = 0):
    """Get configuration status for all supported services.

    Args:
        user_id: Authenticated user ID.  ``0`` returns global/system status.
    """
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        return cm.get_connection_status(user_id=user_id)
    except Exception as e:
        logger.exception(f"Failed to fetch connection status: {e}")
        # Keep the Settings page usable even when credential storage is unavailable.
        # Returning a safe disconnected snapshot avoids a hard 500 loop in the UI.
        msg = f"Credential status unavailable: {e}"
        return {
            "fabric": {
                "configured": False,
                "fields_stored": 0,
                "fields_required": 1,
                "missing_fields": ["workspace_id"],
                "credentials": {},
                "auth_method": "none",
                "has_auth": False,
                "status": "disconnected",
                "error": msg,
            },
            "snowflake": {
                "configured": False,
                "fields_stored": 0,
                "fields_required": 5,
                "missing_fields": ["account", "user", "warehouse", "database", "password"],
                "credentials": {},
                "auth_type": "password",
                "status": "disconnected",
                "error": msg,
            },
            "databricks": {
                "configured": False,
                "fields_stored": 0,
                "fields_required": 3,
                "missing_fields": ["host", "warehouse_id", "token"],
                "credentials": {},
                "auth_type": "pat",
                "auth_method": "none",
                "status": "disconnected",
                "error": msg,
            },
        }


async def save_connection(service: str, payload: Dict[str, Any], user_id: int = 0):
    """Save credentials for a service and refresh in-process settings.

    Args:
        user_id: Owner of these credentials.  ``0`` stores as global/system.
                 Pass the authenticated user's ID for user-scoped storage.
    """
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        saved = cm.save_credentials(service, payload, user_id=user_id)
        # Inject to os.environ using the full precedence chain so that
        # connectors initialised in this process pick up the new values.
        # For user-scoped saves, we inject the user's credentials (not global)
        # so that the in-process settings.reload() picks them up correctly.
        cm.inject_credentials_to_env(service, user_id=user_id)
        reload_settings()
        return {
            "status": "saved",
            "service": service,
            "fields_saved": saved,
        }
    except Exception as e:
        logger.exception(f"Failed to save {service} credentials: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def delete_connection(service: str, user_id: int = 0):
    """Remove stored credentials for a service.

    Args:
        user_id: Scope to delete.  ``0`` removes global rows; pass a user
                 ID to remove only that user's rows.
    """
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        cm.delete_credentials(service, user_id=user_id)
        return {"status": "deleted", "service": service}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def test_connection(service: str, user_id: int = 0):
    """Test the stored credentials for a service by attempting authentication.

    Args:
        user_id: Authenticated user ID.  ``0`` uses global/system credentials.
    """
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()

    try:
        cm.inject_credentials_to_env(service, user_id=user_id)
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

    if service == "databricks":
        try:
            cfg = reload_settings()
            from semabridge.connectors.databricks_publisher import DatabricksPublisher

            publisher = DatabricksPublisher(cfg.databricks)
            token = publisher._resolve_token()
            # Fire a lightweight query to validate warehouse access
            import requests as _requests

            resp = _requests.post(
                f"https://{cfg.databricks.host}/api/2.0/sql/statements",
                json={
                    "statement": "SHOW CATALOGS",
                    "warehouse_id": cfg.databricks.warehouse_id,
                    "wait_timeout": "10s",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return {"status": "success", "message": "Databricks connection successful"}
            return {"status": "failed", "message": f"Databricks API returned {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    raise HTTPException(status_code=400, detail=f"Unknown service: {service}")


async def snowflake_oauth_test(body: Dict[str, Any]) -> Dict[str, Any]:
    """Test Snowflake OAuth S2S connectivity.

    Acquires a token using the provided client-credentials and attempts
    a live connection to Snowflake with ``authenticator=oauth``.

    Args:
        body: Dict with keys: account, user, oauth_client_id,
              oauth_client_secret, oauth_token_endpoint, oauth_scope (optional),
              warehouse (optional), database (optional).

    Returns:
        Dict with status, message, username, and role on success.
    """
    import requests as _requests

    account = (body.get("account") or "").strip()
    user = (body.get("user") or "").strip()
    client_id = (body.get("oauth_client_id") or "").strip()
    client_secret = (body.get("oauth_client_secret") or "").strip()
    token_endpoint = (body.get("oauth_token_endpoint") or "").strip()
    scope = (body.get("oauth_scope") or "").strip()

    if not account or not user:
        raise HTTPException(
            status_code=400,
            detail="Please provide Account and Username before testing OAuth.",
        )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="OAuth Client ID and Client Secret are required.",
        )

    if not token_endpoint:
        import os
        tenant_id = os.environ.get("AZURE_TENANT_ID", "organizations")
        token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    if not scope and client_id:
        scope = f"api://{client_id}/.default"

    # Step 1: Acquire token from IdP
    payload: Dict[str, str] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    if scope:
        payload["scope"] = scope

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = _requests.post(token_endpoint, data=payload, timeout=30, verify=False)
        if resp.status_code != 200:
            error_msg = f"Azure AD returned {resp.status_code}: {resp.text}"
            logger.error(f"Snowflake OAuth token acquisition failed: {error_msg}")
            return {"status": "failed", "message": error_msg}
            
        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return {
                "status": "failed",
                "message": f"Token endpoint returned no access_token: {token_data}",
            }
            
        try:
            import base64, json, os, snowflake.connector
            parts = access_token.split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1]
                payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                decoded_claims = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
                
                # Write to file so agent can read it automatically
                with open("token_dump.json", "w") as f:
                    json.dump(decoded_claims, f, indent=2)
                    
                with open("token_raw.txt", "w") as f:
                    f.write(access_token)
                    
            # Run diagnostic if we have a password
            sf_pass = os.environ.get("SNOWFLAKE_PASSWORD")
            if sf_pass:
                try:
                    ctx = snowflake.connector.connect(
                        user=user,
                        password=sf_pass,
                        account=account
                    )
                    cur = ctx.cursor()
                    cur.execute(f"SELECT SYSTEM$VERIFY_EXTERNAL_OAUTH_TOKEN('{access_token}')")
                    row = cur.fetchone()
                    diagnostic_result = row[0] if row else "No diagnostic result"
                    logger.warning(f"Snowflake OAuth Diagnostic: {diagnostic_result}")
                    if "Token validation failed" in diagnostic_result or "invalid" in diagnostic_result.lower():
                        return {"status": "failed", "message": f"Token Acquired but Snowflake Rejected it:\n {diagnostic_result}"}
                except Exception as sf_err:
                    logger.error(f"Failed to run Snowflake diagnostic: {sf_err}")
                
        except Exception as e:
            logger.error(f"Failed to decode JWT or run diagnostic: {e}")
    except Exception as e:
        logger.error(f"Snowflake OAuth token acquisition failed: {e}")
        return {"status": "failed", "message": f"Token acquisition failed: {e}"}

    # Step 2: Connect to Snowflake with the OAuth token
    try:
        import snowflake.connector

        connect_kwargs: Dict[str, Any] = {
            "account": account,
            "user": user,
            "authenticator": "oauth",
            "token": access_token,
        }
        if body.get("warehouse"):
            connect_kwargs["warehouse"] = body["warehouse"]
        if body.get("database"):
            connect_kwargs["database"] = body["database"]

        conn = snowflake.connector.connect(**connect_kwargs)
        cur = conn.cursor()
        cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE()")
        row = cur.fetchone()
        conn.close()

        username = row[0] if row else user
        role = row[1] if row else "N/A"

        logger.info(f"Snowflake OAuth S2S test successful: {username}")
        return {
            "status": "success",
            "message": f"OAuth S2S login successful as {username} (role: {role})",
            "username": username,
            "role": role,
        }
    except Exception as e:
        logger.error(f"Snowflake OAuth S2S connection failed: {e}")
        return {"status": "failed", "message": str(e)}
