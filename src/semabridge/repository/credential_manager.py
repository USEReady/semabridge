"""
Credential Manager for SemaBridge.

Stores connection configurations in the SQLAlchemy ORM layer and injects
them into `os.environ` at runtime so that existing connectors continue to
work without modification.

Security model:
    - Secrets are stored in the ORM-backed ``semabridge_credentials`` table.
    - They are injected into `os.environ` only during connector init.
    - Values are NEVER passed as function parameters.
    - The database file should be protected by OS-level permissions.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from semabridge.core.env import get_fabric_access_token_from_env
from semabridge.core.exceptions import RepositoryError
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Credential
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# Services whose stored values must NEVER be pushed into os.environ by
# inject_all() -- see inject_all()'s docstring. Currently just the Tier 5
# "llm_*" services (repository/llm_provider_credentials.py).
_SERVICE_PREFIX_NEVER_INJECTED = "llm_"

# Mapping: (service, key) -> environment variable name
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
        "auth_type": "SNOWFLAKE_AUTH_TYPE",
        "private_key": "SNOWFLAKE_PRIVATE_KEY",
        "private_key_passphrase": "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE",
        "authenticator": "SNOWFLAKE_AUTHENTICATOR",
        "oauth_client_id": "SNOWFLAKE_OAUTH_CLIENT_ID",
        "oauth_client_secret": "SNOWFLAKE_OAUTH_CLIENT_SECRET",
        "oauth_token_endpoint": "SNOWFLAKE_OAUTH_TOKEN_ENDPOINT",
        "oauth_scope": "SNOWFLAKE_OAUTH_SCOPE",
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
    # Tier 5 (DAX-to-SQL LLM translation) provider credentials, configured
    # via the Settings page (see repository/llm_provider_credentials.py).
    # Only "api_key" has an env fallback — "model" (the Settings-selected
    # model, saved separately) has no .env equivalent, since there was
    # never an existing convention for pinning a model via an env var;
    # get_credentials()'s Step 3 env-fallback loop only ever looks up
    # keys present in this map, so omitting "model" here means it's
    # correctly DB-only with no fallback attempted.
    "llm_openai": {"api_key": "OPENAI_API_KEY"},
    "llm_gemini": {"api_key": "GEMINI_API_KEY"},
    "llm_groq": {"api_key": "GROQ_API_KEY"},
    "llm_featherless": {"api_key": "FEATHERLESS_API_KEY"},
    "llm_anthropic": {"api_key": "ANTHROPIC_API_KEY"},
}

# Keys that are exclusive to each auth mode — used to purge stale
# credentials when the user switches authentication methods.
_AUTH_EXCLUSIVE_KEYS: Dict[str, list[str]] = {
    # Snowflake
    "password": ["private_key", "private_key_passphrase", "authenticator",
                 "oauth_client_id", "oauth_client_secret", "oauth_token_endpoint", "oauth_scope"],
    "keypair": ["password", "authenticator",
               "oauth_client_id", "oauth_client_secret", "oauth_token_endpoint", "oauth_scope"],
    "externalbrowser": ["password", "private_key", "private_key_passphrase",
                        "oauth_client_id", "oauth_client_secret", "oauth_token_endpoint", "oauth_scope"],
    "oauth": ["password", "private_key", "private_key_passphrase", "authenticator"],
    
    # Databricks
    "interactive": ["client_secret", "token"],
    "service_principal": ["token", "access_token", "refresh_token", "expires_at", "account_username"],
    "pat": ["client_id", "client_secret", "access_token", "refresh_token", "expires_at", "account_username"],
}


class CredentialManager:
    """Manages connection credentials via the SQLAlchemy ORM.

    Provides methods to:
    1. Store credentials from the UI.
    2. Retrieve credentials for display (secrets masked).
    3. Inject credentials into os.environ before connector init.
    4. Delete stored credentials.

    Args:
        url_override: Optional SQLAlchemy connection URL (for tests).
    """

    TABLE_NAME = "semabridge_credentials"

    def __init__(self, db_path: Optional[str] = None, url_override: Optional[str] = None) -> None:
        # Accept legacy db_path arg; translate to a DuckDB URL (never SQLite).
        if url_override:
            resolved_url = url_override
        elif db_path:
            resolved_url = f"duckdb:///{db_path}"
        else:
            resolved_url = None

        if resolved_url:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            engine = create_engine(resolved_url, echo=False, future=True)
            Base.metadata.create_all(engine)
            self._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        else:
            from semabridge.repository.orm.session_factory import (
                get_engine,
                get_session_factory,
            )

            engine = get_engine()
            # For Snowflake, skip table creation (should exist in production)
            # For other databases, table creation is handled globally during FastAPI startup_event.
            self._SessionLocal = get_session_factory()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session(self) -> Session:
        return self._SessionLocal()

    # ------------------------------------------------------------------
    # Write Operations
    # ------------------------------------------------------------------

    def save_credentials(self, service: str, credentials: Dict[str, Any], user_id: int = 0) -> int:
        """Store or update credentials for a service.

        Args:
            service:     Service name ('fabric', 'snowflake', 'databricks').
            credentials: Key-value pairs of configuration fields.
            user_id:     Owner of these credentials.  ``0`` (default) stores as
                         global/system credentials accessible by the CLI and
                         background sync.  Pass the authenticated user's ID for
                         user-scoped storage that takes precedence over the
                         global row.

        Returns:
            Number of credentials saved.

        Raises:
            RepositoryError: On database write failure.
        """
        if service not in _ENV_MAP and service != "fabric_token":
            raise RepositoryError(
                f"Unknown service '{service}'. Supported: {list(_ENV_MAP.keys())}"
            )

        secret_keys = self._get_secret_keys(service)
        saved = 0

        try:
            with self._session() as session:
                # Purge stale auth-mode-specific keys when auth_type changes
                new_auth_type = credentials.get("auth_type")
                if service in ("snowflake", "databricks") and new_auth_type:
                    keys_to_purge = _AUTH_EXCLUSIVE_KEYS.get(new_auth_type, [])
                    for stale_key in keys_to_purge:
                        existing_stale = session.get(
                            Credential, (user_id, service, stale_key)
                        )
                        if existing_stale:
                            session.delete(existing_stale)
                            logger.debug(
                                "Purged stale key '%s' for '%s' (switched to %s, owner=%d)",
                                stale_key, service, new_auth_type, user_id,
                            )

                for key, value in credentials.items():
                    if not value and value != 0:
                        continue
                    is_secret = key in secret_keys
                    existing = session.get(Credential, (user_id, service, key))
                    if existing:
                        existing.value = str(value)
                        existing.is_secret = is_secret
                    else:
                        session.add(
                            Credential(
                                owner_id=user_id,
                                service=service,
                                key=key,
                                value=str(value),
                                is_secret=is_secret,
                            )
                        )
                    saved += 1
                session.commit()

            logger.info("Saved %d credentials for '%s' (owner=%d)", saved, service, user_id)
            return saved
        except Exception as exc:
            raise RepositoryError(
                f"Failed to save credentials for '{service}': {exc}"
            ) from exc

    def delete_credentials(self, service: str, user_id: int = 0) -> int:
        """Remove all stored credentials for a service.

        Args:
            service: Service name to delete credentials for.
            user_id: Scope to delete.  ``0`` removes global/system rows;
                     pass a user ID to remove only that user's rows.

        Returns:
            Number of credentials removed.
        """
        try:
            with self._session() as session:
                result = session.execute(
                    delete(Credential).where(
                        Credential.service == service,
                        Credential.owner_id == user_id,
                    )
                )
                session.commit()
                count = result.rowcount or 0
            logger.info("Deleted credentials for '%s' (owner=%d): %d rows", service, user_id, count)
            return count
        except Exception as exc:
            raise RepositoryError(f"Failed to delete credentials: {exc}") from exc

    # ------------------------------------------------------------------
    # Read Operations
    # ------------------------------------------------------------------

    def get_credentials(
        self,
        service: str,
        mask_secrets: bool = True,
        user_id: int = 0,
        include_env_fallback: bool = True,
    ) -> Dict[str, str]:
        """Retrieve stored credentials for a service.

        Lookup precedence:
          1. User-scoped rows (``owner_id == user_id``) when ``user_id > 0``.
          2. Global/system rows (``owner_id == 0``) as fallback.
          3. ``os.environ`` (populated from .env at startup) for any key not
             found in the database.

        Args:
            service:      Service name ('fabric', 'snowflake', 'databricks').
            mask_secrets: If True, secret values are replaced with '••••••••'.
            user_id:      Authenticated user ID.  ``0`` reads global rows only.
            include_env_fallback: If True, fill missing keys from the current
                                  process environment. If False, return only
                                  credentials persisted in the database.

        Returns:
            Dictionary of credential key-value pairs.
        """
        secret_keys = self._get_secret_keys(service)
        env_map = _ENV_MAP.get(service, {})
        result: Dict[str, str] = {}

        def _rows_to_dict(rows: list) -> Dict[str, str]:
            out: Dict[str, str] = {}
            for row in rows:
                if mask_secrets and row.is_secret:
                    out[row.key] = "••••••••"
                else:
                    out[row.key] = row.value
            return out

        # --- Step 1: Try user-scoped rows first (when user_id > 0) ---
        if user_id > 0:
            try:
                with self._session() as session:
                    user_rows = session.execute(
                        select(Credential).where(
                            Credential.owner_id == user_id,
                            Credential.service == service,
                        )
                    ).scalars().all()
                if user_rows:
                    result = _rows_to_dict(user_rows)
            except Exception:
                pass

        # --- Step 2: Fall back to global rows (owner_id=0) ---
        if not result:
            try:
                with self._session() as session:
                    global_rows = session.execute(
                        select(Credential).where(
                            Credential.owner_id == 0,
                            Credential.service == service,
                        )
                    ).scalars().all()
                result = _rows_to_dict(global_rows)
            except Exception:
                pass

        if not include_env_fallback:
            return result

        # --- Step 3: Supplement from os.environ for any keys NOT already in DB ---
        # This ensures credentials set only in .env are always surfaced in the UI,
        # and that required fields like workspace_id are not hidden when the DB
        # only has the MSAL tokens (access_token / refresh_token).
        for cred_key, env_var in env_map.items():
            if cred_key not in result:
                value = os.environ.get(env_var, "")
                if value:
                    if mask_secrets and cred_key in secret_keys:
                        result[cred_key] = "••••••••"
                    else:
                        result[cred_key] = value

        return result

    def get_connection_status(self, user_id: int = 0) -> Dict[str, Any]:
        """Get the configuration status for all supported services.

        Args:
            user_id: Authenticated user ID.  ``0`` reads global rows only
                     (CLI / background sync mode).

        Returns a dict per service with standardized 'status' field ("connected"/"disconnected"),
        plus existing details for UI display.
        """
        status: Dict[str, Any] = {}
        for service in _ENV_MAP:
            # Tier 5 LLM provider services have their own dedicated status
            # endpoint (repository/llm_provider_credentials.get_all_provider_status)
            # -- they don't belong in the fabric/snowflake/databricks
            # connection-status dict this method's callers (the Settings
            # page's Connector Configuration section) expect.
            if service.startswith(_SERVICE_PREFIX_NEVER_INJECTED):
                continue
            stored = self.get_credentials(service, mask_secrets=True, user_id=user_id)
            persisted = self.get_credentials(
                service,
                mask_secrets=True,
                user_id=user_id,
                include_env_fallback=False,
            )
            raw_persisted = self.get_credentials(
                service,
                mask_secrets=False,
                user_id=user_id,
                include_env_fallback=False,
            )
            required = self._get_required_keys(service, raw_persisted)
            missing = [k for k in required if k not in persisted]

            is_configured = len(missing) == 0 and len(persisted) > 0

            svc_status: Dict[str, Any] = {
                "configured": is_configured,
                "fields_stored": len(persisted),
                "fields_required": len(required),
                "missing_fields": missing,
                "credentials": stored,
                "has_saved_credentials": len(persisted) > 0,
            }

            # Fabric-specific: check auth method availability
            if service == "fabric":
                token = self.get_msal_token(user_id=user_id)
                if token and token.get("access_token"):
                    auth_method = "interactive"
                elif raw_persisted.get("client_secret"):
                    auth_method = "service_principal"
                else:
                    auth_method = "none"
                svc_status["auth_method"] = auth_method
                svc_status["has_auth"] = auth_method != "none"
                # Only truly configured if workspace + auth both exist
                is_configured = is_configured and auth_method != "none"
                svc_status["configured"] = is_configured

            # Snowflake-specific: include auth_type in response
            if service == "snowflake":
                svc_status["auth_type"] = stored.get("auth_type", "password")

            # Databricks-specific: include auth_type and auth_method
            if service == "databricks":
                auth_type = raw_persisted.get("auth_type", "pat")
                if auth_type == "interactive" and raw_persisted.get("access_token"):
                    auth_method = "interactive"
                elif (
                    auth_type == "service_principal"
                    and raw_persisted.get("client_id")
                    and raw_persisted.get("client_secret")
                ):
                    auth_method = "service_principal"
                elif raw_persisted.get("token"):
                    auth_method = "pat"
                else:
                    auth_method = "none"
                svc_status["auth_method"] = auth_method
                svc_status["auth_type"] = stored.get("auth_type", "pat")

            # Add standardized status field
            svc_status["status"] = "connected" if is_configured else "disconnected"

            status[service] = svc_status
        return status

    # ------------------------------------------------------------------
    # Environment Injection
    # ------------------------------------------------------------------

    def inject_credentials_to_env(self, service: str, user_id: int = 0) -> int:
        """Inject stored credentials into os.environ.

        Reads credentials using the user_id precedence (user row first,
        global row fallback) then injects them into ``os.environ``.

        Args:
            service: Service name ('fabric', 'snowflake', 'databricks').
            user_id: Authenticated user ID.  ``0`` injects global rows only.

        Returns:
            Number of environment variables set.

        Raises:
            RepositoryError: If the service has no stored credentials.
        """
        credentials = self.get_credentials(service, mask_secrets=False, user_id=user_id)
        if not credentials:
            raise RepositoryError(
                f"No stored credentials for '{service}'. "
                "Configure them via the UI (Settings â†’ Connections)."
            )

        env_map = _ENV_MAP.get(service, {})
        injected = 0

        skip_keys: set = set()
        if service == "fabric":
            # NEVER inject ephemeral tokens into env vars.  They are
            # short-lived and must only flow through the CredentialManager's
            # managed refresh path.  If FABRIC_ACCESS_TOKEN lands in
            # os.environ, FabricPublisher treats it as a pre-issued CI token
            # and uses it without refresh — guaranteed 401 after expiry.
            skip_keys = {"access_token", "refresh_token"}

            has_service_principal = bool(
                credentials.get("client_id") and credentials.get("client_secret")
            )
            if not has_service_principal:
                skip_keys.update({"tenant_id", "client_id", "client_secret"})

        if service == "databricks":
            # Ephemeral MSAL tokens must never land in env vars.
            # DatabricksPublisher reads them via CredentialManager.
            auth_type = credentials.get("auth_type", "pat")
            if auth_type == "interactive":
                skip_keys = {"access_token", "refresh_token", "expires_at",
                             "account_username", "tenant_id"}

        for key, value in credentials.items():
            if key in skip_keys:
                continue
            env_var = env_map.get(key)
            if env_var and value:
                os.environ[env_var] = value
                injected += 1
                logger.debug("Injected %s into os.environ", env_var)

        logger.info("Injected %d environment variables for '%s'", injected, service)
        return injected

    def inject_all(self) -> Dict[str, int]:
        """Inject credentials for all configured services.

        Skips every ``llm_*`` service (Tier 5 LLM provider credentials —
        see repository/llm_provider_credentials.py). Those services'
        stored ``api_key`` value is Fernet-ciphertext, not a usable key —
        unlike fabric/snowflake/databricks, providers read a
        Settings-configured key through ``ProviderSettings.api_key``,
        never through ``os.environ`` (see ``Tier5Config.resolve()``'s
        docstring for why). Injecting them here would silently overwrite
        a real provider's env var with ciphertext on the next app start
        after an admin saves a key via Settings.
        """
        results: Dict[str, int] = {}
        for service in _ENV_MAP:
            if service.startswith(_SERVICE_PREFIX_NEVER_INJECTED):
                continue
            try:
                count = self.inject_credentials_to_env(service)
                results[service] = count
            except RepositoryError:
                results[service] = 0
        return results

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_secret_keys(service: str) -> set:
        """Keys that contain secrets and should be masked."""
        secret_map = {
            "fabric": {"client_secret", "access_token", "refresh_token"},
            "snowflake": {"password", "private_key", "private_key_passphrase", "oauth_client_secret"},
            "fabric_token": {"access_token", "refresh_token"},
            "databricks": {"token", "client_secret", "access_token", "refresh_token"},
            # Tier 5 provider API keys. Note this only controls masking
            # (mask_secrets=True → "••••••••") for generic listing paths —
            # the value stored is already Fernet-ciphertext by the time it
            # reaches this table (see repository/llm_provider_credentials.py),
            # so this is defense-in-depth against ever displaying the
            # ciphertext raw, not the encryption itself.
            "llm_openai": {"api_key"},
            "llm_gemini": {"api_key"},
            "llm_groq": {"api_key"},
            "llm_featherless": {"api_key"},
            "llm_anthropic": {"api_key"},
        }
        return secret_map.get(service, set())

    @staticmethod
    def _get_required_keys(
        service: str,
        credentials: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Keys that must be configured for a service to work.

        For Snowflake the list is auth-mode-aware:
        - password mode  → base + password
        - keypair mode   → base + private_key
        - SSO mode       → base only (no extra stored cred)
        """
        if service == "fabric":
            return ["workspace_id"]

        if service == "snowflake":
            base = ["account", "user", "warehouse", "database"]
            auth_type = (credentials or {}).get("auth_type", "password")
            if auth_type == "keypair":
                return base + ["private_key"]
            elif auth_type == "externalbrowser":
                return base  # SSO needs no extra stored credential
            elif auth_type == "oauth":
                return base + ["oauth_client_id", "oauth_client_secret", "oauth_token_endpoint"]
            else:
                return base + ["password"]

        if service == "databricks":
            base = ["host", "warehouse_id"]
            auth_type = (credentials or {}).get("auth_type", "pat")
            if auth_type == "interactive":
                return base + ["access_token"]
            if auth_type == "service_principal":
                return base + ["client_id", "client_secret"]
            return base + ["token"]

        return []

    # ------------------------------------------------------------------
    # MSAL Token Management
    # ------------------------------------------------------------------

    def save_msal_token(
        self,
        access_token: str,
        refresh_token: str,
        account_username: str,
        tenant_id: str,
        expires_in: int = 3600,
        user_id: int = 0,
    ) -> None:
        """Store MSAL tokens from an interactive Fabric login.

        Args:
            user_id: Owner of this token.  ``0`` stores as a global/system
                     token (legacy behaviour, used when no authenticated user
                     context is available — e.g. CLI background refresh).
        """
        import time

        token_data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_username": account_username,
            "tenant_id": tenant_id,
            "expires_at": str(int(time.time()) + expires_in),
            "auth_method": "interactive",
        }

        try:
            with self._session() as session:
                for key, value in token_data.items():
                    is_secret = key in ("access_token", "refresh_token")
                    existing = session.get(Credential, (user_id, "fabric_token", key))
                    if existing:
                        existing.value = value
                        existing.is_secret = is_secret
                    else:
                        session.add(
                            Credential(
                                owner_id=user_id,
                                service="fabric_token",
                                key=key,
                                value=value,
                                is_secret=is_secret,
                            )
                        )
                session.commit()

            # Also save tenant_id into fabric service for env injection
            self.save_credentials("fabric", {"tenant_id": tenant_id}, user_id=user_id)
            logger.info("Saved MSAL tokens for user '%s' (owner=%d)", account_username, user_id)
        except Exception as exc:
            raise RepositoryError(f"Failed to save MSAL token: {exc}") from exc

    def get_msal_token(self, user_id: int = 0) -> Optional[Dict[str, str]]:
        """Retrieve the stored Fabric MSAL token.

        Args:
            user_id: Owner ID.  ``0`` reads the global/system token.
        """
        try:
            with self._session() as session:
                # Try user-scoped first, fall back to global
                rows = None
                if user_id > 0:
                    rows = session.execute(
                        select(Credential).where(
                            Credential.owner_id == user_id,
                            Credential.service == "fabric_token",
                        )
                    ).scalars().all()
                if not rows:
                    rows = session.execute(
                        select(Credential).where(
                            Credential.owner_id == 0,
                            Credential.service == "fabric_token",
                        )
                    ).scalars().all()
                if not rows:
                    return None
                return {row.key: row.value for row in rows}
        except Exception:
            return None

    def has_valid_token(self) -> bool:
        """Check if a valid (non-expired) MSAL token exists."""
        import time

        token = self.get_msal_token()
        if not token or "access_token" not in token:
            return False
        try:
            expires_at = int(token.get("expires_at", "0"))
            return time.time() < expires_at - 60
        except (ValueError, TypeError):
            return False

    def get_fabric_auth_method(self) -> str:
        """Determine how Fabric is authenticated."""
        if get_fabric_access_token_from_env():
            return "env_token"

        token = self.get_msal_token()
        if token and token.get("access_token"):
            return "interactive"

        # Check FabricConfig directly (environment variables) for client_secret
        # This works even if credentials haven't been saved to the database
        try:
            from semabridge.core.settings import get_settings
            settings = get_settings()
            if settings.fabric.client_secret is not None:
                return "service_principal"
        except Exception:
            pass  # Fall through to legacy check

        # Legacy fallback: check stored credentials database
        creds = self.get_credentials("fabric", mask_secrets=False)
        if creds.get("client_secret"):
            return "service_principal"

        return "none"

    def get_databricks_auth_method(self) -> str:
        """Determine how Databricks is authenticated.

        Returns:
            One of ``'interactive'``, ``'pat'``, ``'service_principal'``, or ``'none'``.
        """
        creds = self.get_credentials("databricks", mask_secrets=False)
        auth_type = creds.get("auth_type", "pat")

        # Interactive MSAL login (device code flow)
        if auth_type == "interactive" and creds.get("access_token"):
            return "interactive"

        if (
            auth_type == "service_principal"
            and creds.get("client_id")
            and creds.get("client_secret")
        ):
            return "service_principal"
        if creds.get("token"):
            return "pat"
        return "none"

    # ------------------------------------------------------------------
    # Databricks MSAL Token Management
    # ------------------------------------------------------------------

    def save_databricks_token(
        self,
        access_token: str,
        refresh_token: str,
        account_username: str,
        tenant_id: str,
        host: str,
        warehouse_id: str = "",
        catalog: str = "main",
        schema_name: str = "semabridge",
        expires_in: int = 3600,
        user_id: int = 0,
    ) -> None:
        """Store Databricks MSAL tokens from an interactive device code login.

        Args:
            user_id: Owner of this token.  ``0`` (default) stores as a global
                     token.  Pass the authenticated user's ID for user-scoped
                     storage that takes precedence over the global row.
        """
        import time

        token_data = {
            "auth_type": "interactive",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_username": account_username,
            "tenant_id": tenant_id,
            "expires_at": str(int(time.time()) + expires_in),
            "host": host,
            "warehouse_id": warehouse_id,
            "catalog": catalog,
            "schema_name": schema_name,
        }

        self.save_credentials("databricks", token_data, user_id=user_id)
        logger.info(
            "Saved Databricks MSAL tokens for user '%s' (tenant: %s, owner=%d)",
            account_username,
            tenant_id[:8] + "..." if len(tenant_id) > 8 else tenant_id,
            user_id,
        )

    def get_databricks_token(self, user_id: int = 0) -> Optional[Dict[str, str]]:
        """Retrieve stored Databricks MSAL token data.

        Args:
            user_id: Owner ID.  ``0`` reads the global/system token.
        """
        creds = self.get_credentials("databricks", mask_secrets=False, user_id=user_id)
        if creds.get("auth_type") != "interactive":
            return None
        if not creds.get("access_token"):
            return None
        return creds

    def is_databricks_token_expired(self) -> bool:
        """Check if the stored Databricks MSAL token is expired.

        Uses a 60-second safety buffer to avoid using tokens that are
        about to expire.

        Returns:
            True if expired or no token exists, False if valid.
        """
        import time

        token = self.get_databricks_token()
        if not token:
            return True
        try:
            expires_at = int(token.get("expires_at", "0"))
            return time.time() > expires_at - 60
        except (ValueError, TypeError):
            return True

    def refresh_databricks_token(self) -> Optional[Dict[str, str]]:
        """Attempt to silently refresh the Databricks OAuth token.

        Uses the stored refresh_token to acquire a new access_token
        from Databricks Native OAuth without user interaction.

        Returns:
            Updated token data dict on success, None on failure.
        """
        import requests
        
        token = self.get_databricks_token()
        if not token or not token.get("refresh_token") or not token.get("host"):
            logger.warning("No Databricks refresh token or host available")
            return None

        # Fetch client_id which we stored separately during the login flow
        oauth_creds = self.get_credentials("databricks_oauth", mask_secrets=False)
        client_id = oauth_creds.get("client_id")
        
        if not client_id:
            logger.warning("Databricks Native OAuth refresh failed: Missing client_id")
            return None

        try:
            host = token["host"]
            if not host.startswith("https://"):
                host = f"https://{host}"
                
            token_url = f"{host}/oidc/v1/token"
            
            payload = {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": token["refresh_token"]
            }
            
            resp = requests.post(
                token_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15
            )
            
            if resp.status_code == 200:
                result = resp.json()
                self.save_databricks_token(
                    access_token=result["access_token"],
                    refresh_token=result.get("refresh_token", token["refresh_token"]),
                    account_username=token.get("account_username", "unknown"),
                    tenant_id="",  # N/A for native DBX
                    host=token["host"],
                    warehouse_id=token.get("warehouse_id", ""),
                    catalog=token.get("catalog", "main"),
                    schema_name=token.get("schema_name", "semabridge"),
                    expires_in=result.get("expires_in", 3600),
                )
                logger.info("Databricks Native OAuth token refreshed successfully")
                return self.get_databricks_token()
            else:
                logger.error("Databricks token refresh failed: %s", resp.text)
                return None

        except Exception as exc:
            logger.exception("Databricks token refresh error: %s", exc)
            return None
