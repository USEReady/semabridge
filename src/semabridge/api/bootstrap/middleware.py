from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger('semabridge.api')


class ContentSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured maximum."""

    def __init__(self, app, max_content_size: int = 10 * 1024 * 1024) -> None:
        super().__init__(app)
        self.max_content_size = int(os.environ.get("MAX_REQUEST_BODY_BYTES", max_content_size))

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_content_size:
            return Response("Request body too large", status_code=413)
        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a X-Request-ID header to every request and response."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add defensive security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if os.environ.get("HTTPS_ENABLED", "false").lower() == "true":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


_CSRF_PUBLIC_PATHS = {
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
    "/auth/register",
    "/auth/login",
    "/auth/auto-login",
    "/auth/refresh",
    "/auth/logout",
    "/docs",
    "/redoc",
    "/openapi.json",
}
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_COOKIE_NAME = "csrf_token"
_CSRF_HEADER_NAME = "x-csrf-token"


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double Submit Cookie CSRF protection.

    - Safe methods (GET, HEAD, OPTIONS) are always allowed.
    - Public paths (login, health, docs) are always allowed.
    - For all other requests: the ``X-CSRF-Token`` header must match
      the ``csrf_token`` cookie value. If either is absent or they do
      not match, the request is rejected with HTTP 403.
    - On every response that does not already carry the cookie, a new
      random token is set so that the frontend can pick it up.
    """

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path

        # CSRF is only meaningful when auth (and cookies) are active.
        # In single-user / dev mode (AUTH_ENABLED != "true"), skip enforcement.
        auth_enabled = os.environ.get("AUTH_ENABLED", "").lower() == "true"

        # Always pass through safe methods and public paths without CSRF check.
        if auth_enabled and method not in _CSRF_SAFE_METHODS and path not in _CSRF_PUBLIC_PATHS:
            cookie_token = request.cookies.get(_CSRF_COOKIE_NAME, "")
            header_token = request.headers.get(_CSRF_HEADER_NAME, "")
            if not cookie_token or not header_token or cookie_token != header_token:
                return Response(
                    content='{"detail":"CSRF token missing or invalid"}',
                    status_code=403,
                    media_type="application/json",
                )

        response = await call_next(request)

        # Ensure every client receives a CSRF token cookie they can echo back.
        if _CSRF_COOKIE_NAME not in request.cookies:
            token = str(uuid.uuid4())
            response.set_cookie(
                _CSRF_COOKIE_NAME,
                token,
                httponly=False,  # Must be readable by JS to set the header
                samesite="strict",
                secure=os.environ.get("HTTPS_ENABLED", "false").lower() == "true",
            )

        return response


class RequestResponseLoggingMiddleware:
    """Pure ASGI logging middleware.

    Replaces BaseHTTPMiddleware to avoid the Starlette `call_next()` /
    `asyncio.CancelledError` crash that occurs on Windows when a client
    disconnects mid-request or a long-running operation is cancelled.
    BaseHTTPMiddleware wraps requests in a background task; when that task
    is cancelled the CancelledError bubbles up through call_next() and can
    kill the server worker. A pure ASGI middleware has no such wrapper and
    therefore handles cancellation gracefully.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        skip_paths = {'/api/health', '/docs', '/redoc', '/openapi.json'}
        path = scope.get("path", "")
        if path in skip_paths:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        query_string = scope.get("query_string", b"").decode("utf-8", errors="replace")
        log_msg = f'[REQUEST] {method} {path}'
        if query_string:
            log_msg += f'?{query_string}'
        logger.info(log_msg)

        start_time = time.time()
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except asyncio.CancelledError:
            # Client disconnected — log but do not re-raise to avoid crashing
            # the server worker (especially common on Windows ProactorEventLoop).
            elapsed = time.time() - start_time
            logger.debug(f'[CANCELLED] {method} {path} ({elapsed:.2f}s) — client disconnected')
            return
        except Exception:
            elapsed = time.time() - start_time
            logger.exception(f'[EXCEPTION] {method} {path} ({elapsed:.2f}s)')
            raise

        elapsed = time.time() - start_time
        if status_code >= 400:
            logger.warning(f'[RESPONSE] {method} {path} -> {status_code} ({elapsed:.2f}s)')
        else:
            logger.info(f'[RESPONSE] {method} {path} -> {status_code} ({elapsed:.2f}s)')
