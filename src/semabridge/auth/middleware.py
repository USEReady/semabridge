"""
JWT authentication middleware for FastAPI.

When ``AUTH_ENABLED=true`` (environment variable), every request to a
protected path must carry a valid ``Authorization: Bearer <token>`` header.

Public paths
------------
The following paths are **always** accessible without a token:

- ``/api/health``              — health check
- ``/auth/register``           — user registration
- ``/auth/login``              — user login
- ``/docs``, ``/redoc``, ``/openapi.json`` — API docs
- Any WebSocket upgrade (``/ws/*``)

Toggle
------
Set ``AUTH_ENABLED=false`` to bypass enforcement entirely (single-user / dev mode).
Auth is **enabled by default**: parsing is centralised in
:func:`semabridge.auth.flags.auth_enabled` and fails closed (anything other
than an explicit false-like value means enabled).

``/auth/auto-login`` is listed as public, but the route itself refuses to
issue tokens when auth is enabled unless ``SEMABRIDGE_DEV_AUTO_LOGIN=true``.
"""

from __future__ import annotations

from typing import Set

from fastapi import Request, status
from fastapi.responses import JSONResponse
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from semabridge.auth.flags import auth_enabled
from semabridge.auth.tokens import decode_access_token
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Paths that never require authentication
PUBLIC_PATHS: Set[str] = {
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
    # Discovery endpoints removed from public paths — they return tenant data and require auth.
    # If a specific discovery endpoint must be public (e.g. for OAuth callback), add it here
    # with a comment explaining the reason.
    "/auth/register",
    "/auth/login",
    "/auth/auto-login",
    "/auth/platform-login",
    "/auth/refresh",
    "/auth/logout",
    "/docs",
    "/redoc",
    "/openapi.json",
}

# Connector OAuth endpoints that are reached by a browser *redirect* from the
# identity provider (no Authorization header) and therefore must stay public.
# They are validated by the OAuth ``state`` parameter instead. Everything else
# under /api/connections (save / delete / test credentials, device-code
# login + poll, status) requires a valid token when auth is enabled.
PUBLIC_PATHS |= {
    "/api/connections/databricks/callback",
}

# Path prefixes that never require authentication
PUBLIC_PREFIXES = (
    "/ws",       # WebSocket endpoints
    "/static",   # Static files
    # /health/* (network diagnostics: probes configured hosts, reports proxy
    # setup) requires sign-in; the liveness/readiness probes are the exact
    # /api/health paths in PUBLIC_PATHS above.
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without a valid JWT on protected paths.

    Skips enforcement entirely when ``AUTH_ENABLED`` is not ``"true"``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Feature flag — on by default; set AUTH_ENABLED=false explicitly for dev/single-user mode
        if not auth_enabled():
            # Still populate request.state so get_current_user deps don't fail.
            # Try to resolve from Bearer token; fall back to dev user id=1.
            token = None
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[len("Bearer "):].strip()
            if not token:
                token = request.cookies.get("access_token")
            if token:
                try:
                    payload = decode_access_token(token)
                    request.state.user_id = payload.get("sub")
                    request.state.user_role = payload.get("role", "admin")
                except Exception:
                    request.state.user_id = "1"
                    request.state.user_role = "admin"
            else:
                request.state.user_id = "1"
                request.state.user_role = "admin"
            return await call_next(request)

        path = request.url.path.rstrip("/")

        # CORS preflight requests (OPTIONS) must never require authentication.
        # Browsers do not attach Authorization headers to preflight requests per
        # the CORS specification.  Blocking them causes intermittent failures
        # whenever the browser's preflight-response cache expires.
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow public paths unconditionally
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        # The web app shell (index.html, JS/CSS assets) carries no data; the
        # API calls it makes are authenticated.
        if request.method in ("GET", "HEAD"):
            from semabridge.api.bootstrap.spa import is_spa_path

            if is_spa_path(path):
                return await call_next(request)

        # Extract token: prefer Authorization header, fall back to HttpOnly cookie
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):].strip()
        if not token:
            token = request.cookies.get("access_token")

        if not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            payload = decode_access_token(token)
            # Attach user info to request state for downstream handlers
            request.state.user_id = payload.get("sub")
            request.state.user_role = payload.get("role", "viewer")
        except (JWTError, RuntimeError, Exception) as exc:
            logger.debug("Token validation failed: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid or expired token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
