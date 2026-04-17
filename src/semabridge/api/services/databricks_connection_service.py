import logging
from typing import Any, Dict

from fastapi import HTTPException, Request
from semabridge.core.settings import reload_settings
from semabridge.utils.logger import setup_logging

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

# setup_logging(level="INFO")  # Centralized in app_setup.py
logger = logging.getLogger("semabridge.api")
_databricks_session_token = None
_databricks_session_token_expires_at = 0.0


async def databricks_native_oauth_login(request: Request, payload: Dict[str, Any] = None):
    """Initiate native Databricks OAuth authorization-code flow with PKCE."""
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
        raise HTTPException(
            status_code=400,
            detail="host, client_id, and redirect_uri are required.",
        )

    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8").rstrip("=")
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("utf-8")).digest()
    ).decode("utf-8").rstrip("=")

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

    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "offline_access all-apis",
        "state": flow_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    if not host.startswith("https://"):
        host = f"https://{host}"

    auth_url = f"{host}/oidc/v1/authorize?" + urllib.parse.urlencode(auth_params)
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
    error_description: str = None,
):
    """Callback endpoint for Databricks native OAuth redirect."""
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
        return HTMLResponse(
            html_template.format(
                title="Authentication Failed",
                message=f"Databricks returned an error: {error} - {error_description}",
            )
        )

    if not code or not state:
        return HTMLResponse(
            html_template.format(title="Error", message="Missing code or state parameter.")
        )

    with _poll_sessions_lock:
        session_data = _poll_sessions.get(state)
        if not session_data or session_data.get("service") != "databricks":
            return HTMLResponse(
                html_template.format(
                    title="Error",
                    message="Session expired or invalid. Please try logging in again.",
                )
            )

    host = session_data["host"]
    if not host.startswith("https://"):
        host = f"https://{host}"

    token_url = f"{host}/oidc/v1/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": session_data["client_id"],
        "redirect_uri": session_data["redirect_uri"],
        "code": code,
        "code_verifier": session_data["code_verifier"],
    }

    try:
        resp = requests.post(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error("Failed to exchange Databricks token: %s", resp.text)
            with _poll_sessions_lock:
                _poll_sessions[state]["status"] = "failed"
                _poll_sessions[state]["message"] = f"Token exchange failed: {resp.text[:200]}"
            return HTMLResponse(
                html_template.format(
                    title="Error",
                    message="Failed to exchange authorization code.",
                )
            )

        token_data = resp.json()
        with _poll_sessions_lock:
            _poll_sessions[state].update(
                {
                    "status": "success",
                    "access_token": token_data.get("access_token"),
                    "refresh_token": token_data.get("refresh_token", ""),
                    "expires_in": token_data.get("expires_in", 3600),
                    "username": token_data.get("user_id", "databricks_user"),
                }
            )

        return HTMLResponse(
            html_template.format(
                title="Authentication Successful!",
                message="You have successfully logged in. You can close this tab and return to Semabridge.",
            )
        )
    except Exception as exc:
        logger.exception("Error during Databricks token exchange: %s", exc)
        with _poll_sessions_lock:
            _poll_sessions[state]["status"] = "failed"
            _poll_sessions[state]["message"] = str(exc)
        return HTMLResponse(
            html_template.format(title="Error", message="Internal error during token exchange.")
        )


async def databricks_device_code_poll(request: Request, payload: Dict[str, Any] = None):
    """Return the Databricks OAuth flow status for a specific flow id."""
    from semabridge.repository.credential_manager import CredentialManager

    flow_id = (payload or {}).get("flow_id", "")
    if not flow_id:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Missing flow_id."},
        )

    now = _time.time()
    if now - _last_poll_time.get(flow_id, 0) < 1.0:
        raise HTTPException(
            status_code=429,
            detail={"status": "too_many_requests", "message": "Slow down polling."},
        )
    _last_poll_time[flow_id] = now

    with _poll_sessions_lock:
        state = _poll_sessions.get(flow_id, {}).copy()

    if not state:
        raise HTTPException(
            status_code=400,
            detail={"status": "expired", "message": "Unknown or expired flow_id. Please login again."},
        )

    user_ip = _get_client_ip(request)
    session_ip = state.get("user_ip")
    if session_ip and session_ip != user_ip:
        logger.warning(
            "[FlowID=%s] Databricks IP Mismatch! Login: %s, Poll: %s",
            flow_id,
            session_ip,
            user_ip,
        )

    if state["status"] == "polling":
        return {"status": "pending", "message": "Waiting for user to authenticate in browser..."}

    if state["status"] == "success":
        logger.info("[FlowID=%s] Databricks login success.", flow_id)
        with _poll_sessions_lock:
            _poll_sessions.pop(flow_id, None)
            _last_poll_time.pop(flow_id, None)

        try:
            cm = CredentialManager()
            cm.save_databricks_token(
                access_token=state["access_token"],
                refresh_token=state.get("refresh_token", ""),
                account_username=state.get("username", "unknown"),
                tenant_id="",
                host=state.get("host", ""),
                warehouse_id=state.get("warehouse_id", ""),
                catalog=state.get("catalog", "main"),
                schema_name=state.get("schema_name", "semabridge"),
                expires_in=state.get("expires_in", 3600),
            )
            cm.save_credentials("databricks", {"client_id": state.get("client_id")})

            # Persist to Account table for multi-user token refresh
            from semabridge.repository.orm.session_factory import db_manager
            from semabridge.repository.account_repository import AccountRepository

            _expires_at = _time.time() + int(state.get("expires_in", 3600))
            acct_state = {
                "access_token": state["access_token"],
                "refresh_token": state.get("refresh_token", ""),
                "username": state.get("username", "unknown"),
                "auth_type": state.get("auth_type", "interactive"),
            }
            session = db_manager.get_session_factory()()
            try:
                acct_repo = AccountRepository(session)
                acct_repo.upsert_account("DATABRICKS", acct_state, _expires_at)
            finally:
                session.close()

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
            return {"auth_method": "pat", "logged_in": True, "token_valid": True}
        if method == "service_principal":
            return {"auth_method": "service_principal", "logged_in": True, "token_valid": True}
        if method != "none":
            creds = cm.get_credentials("databricks", mask_secrets=True)
            return {
                "auth_method": "interactive",
                "logged_in": True,
                "token_valid": True,
                "username": creds.get("username", ""),
            }
        return {"auth_method": "none", "logged_in": False, "token_valid": False}
    except Exception as exc:
        return {"auth_method": "none", "logged_in": False, "error": str(exc)}


def databricks_logout():
    """Clear stored Databricks tokens."""
    from semabridge.repository.credential_manager import CredentialManager

    global _databricks_session_token, _databricks_session_token_expires_at

    try:
        cm = CredentialManager()
        cm.delete_credentials("databricks")
        _databricks_session_token = None
        _databricks_session_token_expires_at = 0.0

        reload_settings()
        logger.info("Databricks interactive session cleared")
        return {"status": "logged_out"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
