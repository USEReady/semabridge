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
Set ``AUTH_ENABLED=false`` (or omit) to bypass enforcement entirely.
This allows the existing frontend to keep working while auth is
integrated progressively.
"""

from __future__ import annotations

import os
from typing import Set

from fastapi import Request, status
from fastapi.responses import JSONResponse
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from semabridge.auth.tokens import decode_access_token
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Paths that never require authentication
PUBLIC_PATHS: Set[str] = {
    "/api/health",
    "/api/discovery/fabric",       # Fabric discovery (uses Fabric credentials)
    "/api/discovery/snowflake",    # Snowflake discovery (uses Snowflake credentials)  
    "/api/discovery/semantic",     # Unified semantic discovery (uses both)
    "/api/discovery/repository",   # Repository discovery (uses local DB)
    "/auth/register",
    "/auth/login",
    "/auth/auto-login",
    "/auth/refresh",
    "/auth/logout",
    "/docs",
    "/redoc",
    "/openapi.json",
}

# Path prefixes that never require authentication
PUBLIC_PREFIXES = (
    "/ws",       # WebSocket endpoints
    "/static",   # Static files
    "/api/connections",  # Connector OAuth flows (device code, callbacks)
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without a valid JWT on protected paths.

    Skips enforcement entirely when ``AUTH_ENABLED`` is not ``"true"``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Feature flag — off by default so nothing breaks during rollout
        if os.environ.get("AUTH_ENABLED", "").lower() != "true":
            return await call_next(request)

        path = request.url.path.rstrip("/")

        # Allow public paths unconditionally
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        # Extract and validate the bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.removeprefix("Bearer ").strip()
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
