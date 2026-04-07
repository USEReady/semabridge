from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading as _threading
import time as _time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import Depends, Header, HTTPException, Query, Request
from semabridge.api.deps import get_db
from semabridge.auth.fabric_validator import fabric_validator
from semabridge.core.env import get_fabric_access_token_from_env
from semabridge.core.settings import get_settings, reload_settings
from semabridge.utils.logger import setup_logging
from sqlalchemy.orm import Session

setup_logging(level="INFO")
logger = logging.getLogger("semabridge.api")
settings = get_settings()

_FABRIC_PUBLIC_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
_FABRIC_SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]
_poll_sessions: Dict[str, Dict[str, Any]] = {}
_poll_sessions_lock = _threading.Lock()
_last_poll_time: Dict[str, float] = {}
_fabric_session_token: Optional[str] = None
_fabric_session_token_expires_at: float = 0.0
_POLL_SESSION_TTL = 900
_msal_app_cache: Dict[str, Any] = {}
_msal_http_client: Optional[Any] = None
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

def delete_connection(service: str):
    """Remove stored credentials for a service."""
    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager()
        cm.delete_credentials(service)
        return {"status": "deleted", "service": service}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

def _get_client_ip(request: Request) -> str:
    """Extract client IP, preferring X-Forwarded-For to handle reverse proxies."""
    x_forward = request.headers.get("X-Forwarded-For")
    if x_forward:
        return x_forward.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"

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


