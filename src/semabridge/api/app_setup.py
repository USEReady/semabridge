"""Backward-compatible shim — all content has moved to api/bootstrap/."""
from semabridge.api.bootstrap.app_factory import configure_app, lifespan  # noqa
from semabridge.api.bootstrap.middleware import (  # noqa
    ContentSizeLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    CSRFMiddleware,
    RequestResponseLoggingMiddleware,
)
