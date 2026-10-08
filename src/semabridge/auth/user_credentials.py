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
import threading
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Tuple

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
    "api_secrets": {
        "OPENAI_API_KEY": "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY": "GOOGLE_API_KEY",
        "MISTRAL_API_KEY": "MISTRAL_API_KEY",
        "GROQ_API_KEY": "GROQ_API_KEY",
        "CEREBRAS_API_KEY": "CEREBRAS_API_KEY",
        "SAMBANOVA_API_KEY": "SAMBANOVA_API_KEY",
        "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
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


class _EnvScopeGate:
    """Process-wide gate for os.environ-mutating credential scopes.

    os.environ is process-global, so two concurrent scoped_user_env()
    sections that inject DIFFERENT values (two users' API keys) would see
    each other's credentials. The gate admits any number of concurrent
    holders whose injected env is identical (e.g. parallel runs of the same
    user -- no contention), and makes a holder with a different env wait
    until every other holder has left and the env has been restored. While
    a holder is inside, the env it injected is therefore stable for the
    whole section, so every read the engine makes during the run sees this
    run's own snapshot.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._signature: Optional[Tuple[Tuple[str, str], ...]] = None
        self._holders = 0
        self._saved: Dict[str, Optional[str]] = {}

    def acquire(self, env_values: Dict[str, str]) -> None:
        signature = tuple(sorted(env_values.items()))
        with self._cond:
            while self._holders and self._signature != signature:
                self._cond.wait()
            if self._holders == 0:
                self._signature = signature
                self._saved = {var: os.environ.get(var) for var in env_values}
                for var, value in env_values.items():
                    os.environ[var] = value
            self._holders += 1

    def release(self) -> None:
        with self._cond:
            self._holders -= 1
            if self._holders == 0:
                for var, original in self._saved.items():
                    if original is None:
                        os.environ.pop(var, None)
                    else:
                        os.environ[var] = original
                self._saved = {}
                self._signature = None
                self._cond.notify_all()


_ENV_SCOPE_GATE = _EnvScopeGate()
_scope_depth = threading.local()


@contextmanager
def scoped_user_env(
    user_id: int,
    service: str,
    db: Optional[Session] = None,
) -> Generator[Dict[str, str], None, None]:
    """Context manager that injects user credentials and restores env on exit.

    Concurrency-safe: sections for different users (different injected
    values) are serialized through a process-wide gate (see _EnvScopeGate),
    so a run never sees another user's credentials, and the env stays fixed
    for the whole section. Sections injecting identical values run
    concurrently. Re-entrant within one thread (a nested scope reuses the
    outer one's gate slot and only overlays/restores its own keys).

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
    env_values: Dict[str, str] = {}
    for key, value in credentials.items():
        env_var = env_map.get(key)
        if env_var and value:
            env_values[env_var] = value
    depth = getattr(_scope_depth, "value", 0)
    if depth:
        # Nested scope on a thread that already holds the gate: overlay and
        # restore only this scope's own keys.
        saved = {var: os.environ.get(var) for var in env_values}
        for var, value in env_values.items():
            os.environ[var] = value
        _scope_depth.value = depth + 1
        try:
            yield credentials
        finally:
            _scope_depth.value = depth
            for var, original in saved.items():
                if original is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = original
        return

    # The signature is exactly the injected values: a user with no stored
    # credentials (empty signature) still never overlaps a user whose keys
    # are injected, and keeps the process defaults as before.
    _ENV_SCOPE_GATE.acquire(env_values)
    _scope_depth.value = 1
    try:
        yield credentials
    finally:
        _scope_depth.value = 0
        _ENV_SCOPE_GATE.release()
