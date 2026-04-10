"""
Per-user credential resolution for SemaBridge connectors.

Reads credentials from the ORM ``UserCredential`` table and injects
them into ``os.environ`` on a per-request basis so that existing
connectors (which read from environment variables) continue to work
without modification.

Usage::

    from semabridge.auth.user_credentials import inject_user_credentials

    # Inside a protected route:
    inject_user_credentials(user_id=current_user.id, service="fabric")
    # → now os.environ["FABRIC_TENANT_ID"] etc. are set for this user
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semabridge.repository.orm.models import UserCredential
from semabridge.repository.orm.session_factory import get_session_factory
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Mapping: (service, credential_key) → environment variable name
# Mirrors the legacy CredentialManager._ENV_MAP but sourced from ORM.
_ENV_MAP: Dict[str, Dict[str, str]] = {
    "fabric": {
        "tenant_id": "FABRIC_TENANT_ID",
        "client_id": "FABRIC_CLIENT_ID",
        "client_secret": "FABRIC_CLIENT_SECRET",
        "workspace_id": "FABRIC_WORKSPACE_ID",
        "workspace_ids": "FABRIC_WORKSPACE_IDS",
        "api_base_url": "FABRIC_API_BASE_URL",
        "access_token": "FABRIC_ACCESS_TOKEN",
        "refresh_token": "FABRIC_REFRESH_TOKEN",
    },
    "snowflake": {
        "account": "SNOWFLAKE_ACCOUNT",
        "user": "SNOWFLAKE_USER",
        "password": "SNOWFLAKE_PASSWORD",
        "warehouse": "SNOWFLAKE_WAREHOUSE",
        "database": "SNOWFLAKE_DATABASE",
        "schema_name": "SNOWFLAKE_SCHEMA",
        "role": "SNOWFLAKE_ROLE",
    },
    "databricks": {
        "host": "DATABRICKS_HOST",
        "auth_type": "DATABRICKS_AUTH_TYPE",
        "token": "DATABRICKS_TOKEN",
        "access_token": "DATABRICKS_ACCESS_TOKEN",
        "refresh_token": "DATABRICKS_REFRESH_TOKEN",
        "expires_at": "DATABRICKS_TOKEN_EXPIRES_AT",
        "account_username": "DATABRICKS_ACCOUNT_USERNAME",
        "tenant_id": "DATABRICKS_TENANT_ID",
        "client_id": "DATABRICKS_CLIENT_ID",
        "client_secret": "DATABRICKS_CLIENT_SECRET",
        "warehouse_id": "DATABRICKS_WAREHOUSE_ID",
        "catalog": "DATABRICKS_CATALOG",
        "schema_name": "DATABRICKS_SCHEMA",
    },
}


def get_user_credentials(
    user_id: int,
    service: str,
    db: Optional[Session] = None,
) -> Dict[str, str]:
    """Retrieve all credential key/value pairs for a user + service.

    Args:
        user_id: The authenticated user's primary key.
        service: Service name (``fabric``, ``snowflake``).
        db: Optional SQLAlchemy session; a new one is created if omitted.

    Returns:
        Dictionary mapping credential keys to their values.
    """
    close_session = False
    if db is None:
        SessionLocal = get_session_factory()
        db = SessionLocal()
        close_session = True

    try:
        rows = db.execute(
            select(UserCredential).where(
                UserCredential.user_id == user_id,
                UserCredential.service == service.lower(),
            )
        ).scalars().all()

        return {row.key: row.value for row in rows}
    finally:
        if close_session:
            db.close()


def inject_user_credentials(
    user_id: int,
    service: str,
    db: Optional[Session] = None,
) -> int:
    """Inject a user's stored credentials into ``os.environ``.

    This allows existing connectors (which read from env vars) to work
    transparently with user-scoped credentials.

    Args:
        user_id: The authenticated user's primary key.
        service: Service name (``fabric``, ``snowflake``).
        db: Optional SQLAlchemy session.

    Returns:
        Number of environment variables set.
    """
    credentials = get_user_credentials(user_id, service, db)
    env_map = _ENV_MAP.get(service.lower(), {})
    injected = 0

    for key, value in credentials.items():
        env_var = env_map.get(key)
        if env_var and value:
            os.environ[env_var] = value
            injected += 1
            logger.debug("Injected %s into os.environ (user=%s)", env_var, user_id)

    if injected:
        logger.info(
            "Injected %d env vars for user=%d service=%s", injected, user_id, service
        )
    return injected


@contextmanager
def scoped_user_env(
    user_id: int,
    service: str,
    db: Optional[Session] = None,
) -> Generator[Dict[str, str], None, None]:
    """Context manager that injects user credentials and restores env on exit.

    This avoids env-var bleed between concurrent requests in a
    multi-user server. The original values are restored when the
    context exits.

    Usage::

        with scoped_user_env(user.id, "fabric") as creds:
            extractor = FabricExtractor()
            ...

    Args:
        user_id: Authenticated user's PK.
        service: Service name.
        db: Optional session.

    Yields:
        The credential dict that was injected.
    """
    credentials = get_user_credentials(user_id, service, db)
    env_map = _ENV_MAP.get(service.lower(), {})

    # Save originals
    saved: Dict[str, Optional[str]] = {}
    for key, value in credentials.items():
        env_var = env_map.get(key)
        if env_var and value:
            saved[env_var] = os.environ.get(env_var)
            os.environ[env_var] = value

    try:
        yield credentials
    finally:
        # Restore originals
        for env_var, original in saved.items():
            if original is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = original
