"""Fabric authentication and token resolution.

MSAL device-code login/poll, auth status, logout, and Fabric access-token
resolution/refresh. This module owns the shared in-memory session token and MSAL
caches; keeping every function that mutates them together preserves the ``global``
semantics. Re-exported through ``connection_domain_service``.
"""
from typing import Dict, Any, Optional
import asyncio
import threading as _threading
from starlette.requests import Request

from semabridge.domain.exceptions import (
    AuthenticationError,
    ExternalServiceError,
    InternalError,
    RateLimitError,
    SemaBridgeError,
    ValidationError,
)
from semabridge.api.services.connection_shared import (
    logger,
    settings,
    _FABRIC_PUBLIC_CLIENT_ID,
    _FABRIC_SCOPES,
)
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


def _get_msal_http_client():
    global _msal_http_client
    if _msal_http_client is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        from semabridge.core.settings import get_settings

        class TimeoutSession(requests.Session):
            def request(self, *args, **kwargs):
                kwargs.setdefault('timeout', 5.0)
                return super().request(*args, **kwargs)

        session = TimeoutSession()

        # Inject explicit proxy settings if configured
        proxies = get_settings().network.proxies
        if proxies:
            session.proxies.update(proxies)
            logger.info("MSAL session initialized with explicit proxies: %s", list(proxies.keys()))

        retry = Retry(
            total=2,
            read=2,
            connect=2,
            backoff_factor=0.3,
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
        loop = asyncio.get_running_loop()
        app_msal, flow = await loop.run_in_executor(None, _initiate_flow)

        if "user_code" not in flow:
            raise InternalError(f"Failed to initiate device flow: {flow.get('error_description', 'Unknown error')}")

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

    except SemaBridgeError:
        raise
    except SemaBridgeError:
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
            raise ExternalServiceError(friendly_msg)

        if "ConnectionPool" in error_msg or "timeout" in error_msg.lower():
            friendly_msg = "Network Error: Connection to Microsoft timed out. Please check your firewall or proxy settings."
            logger.error("Fabric login connection timeout: %s", error_msg)
            raise ExternalServiceError(friendly_msg)

        logger.exception("Failed to initiate device code flow: %s", e)
        raise InternalError(str(e))


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
        raise ValidationError(str({"status": "error", "message": "Missing flow_id."}))

    # Rate Limiting: max 1 poll per second per flow_id
    now = _time.time()
    if now - _last_poll_time.get(flow_id, 0) < 1.0:
        # Soft delay or throttle
        raise RateLimitError(str({"status": "too_many_requests", "message": "Slow down polling."}))
    _last_poll_time[flow_id] = now

    with _poll_sessions_lock:
        state = _poll_sessions.get(flow_id, {}).copy()

    if not state:
        raise ValidationError(str({"status": "expired", "message": "Unknown or expired flow_id. Please login again."}))

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
        raise AuthenticationError(str({"status": "expired", "message": "Login session expired. Please login again."}))

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
        await _clear_fabric_from_config()
        logger.info("Fabric interactive session and workspace config cleared")
        return {"status": "logged_out"}
    except Exception as e:
        raise InternalError(str(e))


async def _clear_fabric_from_config() -> None:
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
        def _read_config() -> dict:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

        config_data = await asyncio.to_thread(_read_config)

        if "fabric" in config_data:
            del config_data["fabric"]
            def _write_config() -> None:
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

            await asyncio.to_thread(_write_config)
            logger.info(f"Cleared fabric config from {config_path}")
    except Exception as exc:
        logger.error(f"Failed to clear fabric from config: {exc}")


# -------------------------------------------------------
# Fabric Token Resolution
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
        raise AuthenticationError("Not logged in. Please sign in first.")

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
            raise AuthenticationError(f"Token refresh failed: {error}. Please sign in again.")

    except SemaBridgeError:
        raise
    except Exception as e:
        logger.exception(f"Silent token refresh error: {e}")
        raise AuthenticationError("Token expired and refresh failed. Please sign in again.")



def _resolve_fabric_access_token(
    header_bearer_token,
    identity_id=None,
):
    """Delegates to auth.token_resolver. Kept for backward compatibility."""
    from semabridge.auth.token_resolver import resolve_fabric_access_token
    return resolve_fabric_access_token(header_bearer_token, identity_id)


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
