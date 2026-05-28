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
    "/api/debug/auth-test",        # Temp auth diagnostic test endpoint
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
    "/health",    # Health and diagnostics
    "/api/connections",  # Connector OAuth flows (device code, callbacks)
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without a valid JWT on protected paths.

    Skips enforcement entirely when ``AUTH_ENABLED`` is not ``"true"``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Allow OPTIONS CORS preflights unconditionally since they do not carry Authorization headers
        if request.method == "OPTIONS":
            return await call_next(request)

        # Feature flag — off by default so nothing breaks during rollout
        if os.environ.get("AUTH_ENABLED", "").lower() != "true":
            return await call_next(request)

        path = request.url.path.rstrip("/")

        # Allow public paths unconditionally
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        # Extract and validate the bearer token case-insensitively
        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            # Fallback to case-insensitive manual lookup
            auth_header = next((v for k, v in request.headers.items() if k.lower() == "authorization"), "")

        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:].strip()
        try:
            payload = decode_access_token(token)
            # Attach user info to request state for downstream handlers
            request.state.user_id = payload.get("sub")
            request.state.user_role = payload.get("role", "viewer")
        except Exception as exc:
            from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError
            error_type = exc.__class__.__name__
            rejection_reason = "Unknown token error"
            
            if isinstance(exc, ExpiredSignatureError):
                rejection_reason = "Token signature has expired"
            elif isinstance(exc, JWTClaimsError):
                rejection_reason = "Token has invalid claims"
            elif isinstance(exc, JWTError):
                rejection_reason = "Token signature verification failed (bad secret/malformed JWT)"
            else:
                rejection_reason = f"System decoding error: {str(exc)}"

            # Print first 20 characters of token safely for debugging
            token_prefix = token[:20] + "..." if len(token) > 20 else token
            logger.warning(
                "[AUTH MIDDLEWARE REJECTION] Path: %s, Error Type: %s, Reason: %s, Token prefix: %s",
                path, error_type, rejection_reason, token_prefix
            )

            # Structured logging requested by the user
            logger.warning({
                "token_present": bool(token),
                "jwt_secret_loaded": bool(os.environ.get("JWT_SECRET_KEY")),
                "decode_error": f"{error_type}: {str(exc)}",
            })

            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Invalid or expired token: {rejection_reason}"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
