"""Backward-compatible shim — all content has moved to api/bootstrap/."""
from semabridge.api.bootstrap.app_factory import configure_app, lifespan  # noqa
from semabridge.api.bootstrap.middleware import (  # noqa
    ContentSizeLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    CSRFMiddleware,
    RequestResponseLoggingMiddleware,
)
from semabridge.api.bootstrap.db_migrations import (  # noqa
    _apply_schema_compatibility_fixes,
    _migrate_credentials_table,
    _apply_rls_policies,
    _assign_orphaned_accounts_to_dev_user,
)
