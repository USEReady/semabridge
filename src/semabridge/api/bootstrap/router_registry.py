"""Router registration and CORS middleware setup for the SemaBridge API."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from semabridge.api.bootstrap.middleware import (
    ContentSizeLimitMiddleware,
    CSRFMiddleware,
    RequestIDMiddleware,
    RequestResponseLoggingMiddleware,
    SecurityHeadersMiddleware,
)

try:
    from semabridge.auth.middleware import AuthMiddleware
except ImportError:
    AuthMiddleware = None  # type: ignore[assignment]

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
    _SLOWAPI_AVAILABLE = True
except ImportError:
    _SLOWAPI_AVAILABLE = False
    Limiter = None  # type: ignore[assignment]


def register_routers(app: FastAPI) -> None:
    """Register all API routers and configure CORS middleware on *app*."""
    from semabridge.api.account_router import router as account_router
    from semabridge.api.browse import router as browse_router
    from semabridge.api.discovery_api import router as discovery_router
    from semabridge.api.repo_router import router as repo_router
    from semabridge.api.routers.connection_router import router as connection_router
    from semabridge.api.routers.core_router import router as core_router
    from semabridge.api.routers.diagnostics_router import router as diagnostics_router
    from semabridge.api.routers.mapping_router import router as mapping_router
    from semabridge.api.routers.project_router import router as project_router
    from semabridge.api.routers.synonyms_router import router as synonyms_router
    from semabridge.api.settings_api import router as settings_router
    from semabridge.api.sync_router import router as sync_router
    from semabridge.api.ui import router as ui_router
    from semabridge.api.websocket_alerts import alert_router

    try:
        from semabridge.api.auth_router import router as auth_router
    except ImportError:
        auth_router = None

    for router in [
        repo_router,
        sync_router,
        account_router,
        settings_router,
        discovery_router,
        browse_router,
        alert_router,
        core_router,
        project_router,
        connection_router,
        diagnostics_router,
        mapping_router,
        synonyms_router,
    ]:
        app.include_router(router)

    if auth_router is not None:
        app.include_router(auth_router)

    app.include_router(ui_router, prefix='/api')

    # CORS — must be added after routers so it wraps the full app.
    _cors_raw = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000",
    )
    _cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-Fabric-Context", "X-CSRF-Token"],
    )
