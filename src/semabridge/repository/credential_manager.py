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

from semabridge.core.exceptions import RepositoryError
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Credential
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


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
        "warehouse": "SNOWFLAKE_WAREHOUSE",
        "database": "SNOWFLAKE_DATABASE",
        "schema_name": "SNOWFLAKE_SCHEMA",
        "role": "SNOWFLAKE_ROLE",
    },
}

# Keys that are exclusive to each auth mode — used to purge stale
# credentials when the user switches authentication methods.
_AUTH_EXCLUSIVE_KEYS: Dict[str, list[str]] = {
    "password": ["private_key", "private_key_passphrase", "authenticator"],
    "keypair": ["password", "authenticator"],
    "externalbrowser": ["password", "private_key", "private_key_passphrase"],
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
            # For other databases, create tables as needed
            if engine.dialect.name != "snowflake":
                Base.metadata.create_all(engine)
            self._SessionLocal = get_session_factory()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session(self) -> Session:
        return self._SessionLocal()

    # ------------------------------------------------------------------
    # Write Operations
    # ------------------------------------------------------------------

    def save_credentials(self, service: str, credentials: Dict[str, Any]) -> int:
        """Store or update credentials for a service.

        Args:
            service: Service name ('fabric' or 'snowflake').
            credentials: Key-value pairs of configuration fields.

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
                if service == "snowflake" and new_auth_type:
                    keys_to_purge = _AUTH_EXCLUSIVE_KEYS.get(new_auth_type, [])
                    for stale_key in keys_to_purge:
                        existing_stale = session.get(
                            Credential, (service, stale_key)
                        )
                        if existing_stale:
                            session.delete(existing_stale)
                            logger.debug(
                                "Purged stale key '%s' for '%s' (switched to %s)",
                                stale_key, service, new_auth_type,
                            )

                for key, value in credentials.items():
                    if not value and value != 0:
                        continue
                    is_secret = key in secret_keys
                    existing = session.get(Credential, (service, key))
                    if existing:
                        existing.value = str(value)
                        existing.is_secret = is_secret
                    else:
                        session.add(
                            Credential(
                                service=service,
                                key=key,
                                value=str(value),
                                is_secret=is_secret,
                            )
                        )
                    saved += 1
                session.commit()

            logger.info("Saved %d credentials for '%s'", saved, service)
            return saved
        except Exception as exc:
            raise RepositoryError(
                f"Failed to save credentials for '{service}': {exc}"
            ) from exc

    def delete_credentials(self, service: str) -> int:
        """Remove all stored credentials for a service.

        Args:
            service: Service name to delete credentials for.

        Returns:
            Number of credentials removed.
        """
        try:
            with self._session() as session:
                result = session.execute(
                    delete(Credential).where(Credential.service == service)
                )
                session.commit()
                count = result.rowcount or 0
            logger.info("Deleted credentials for '%s'", service)
            return count
        except Exception as exc:
            raise RepositoryError(f"Failed to delete credentials: {exc}") from exc

    # ------------------------------------------------------------------
    # Read Operations
    # ------------------------------------------------------------------

    def get_credentials(
        self, service: str, mask_secrets: bool = True
    ) -> Dict[str, str]:
        """Retrieve stored credentials for a service.

        Args:
            service: Service name ('fabric' or 'snowflake').
            mask_secrets: If True, secret values are replaced with 'â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢'.

        Returns:
            Dictionary of credential key-value pairs.
        """
        try:
            with self._session() as session:
                rows = session.execute(
                    select(Credential).where(Credential.service == service)
                ).scalars().all()

                result: Dict[str, str] = {}
                for row in rows:
                    if mask_secrets and row.is_secret:
                        result[row.key] = "â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢"
                    else:
                        result[row.key] = row.value
                return result
        except Exception:
            return {}

    def get_connection_status(self) -> Dict[str, Any]:
        """Get the configuration status for all supported services.

        Returns a dict per service with ``configured``, ``missing_fields``,
        and service-specific flags (``auth_method`` / ``has_auth`` for Fabric,
        ``auth_type`` for Snowflake) so the UI can show accurate status.
        """
        status: Dict[str, Any] = {}
        for service in _ENV_MAP:
            stored = self.get_credentials(service, mask_secrets=True)
            # Use raw (unmasked) credentials for auth-type detection
            raw = self.get_credentials(service, mask_secrets=False)
            required = self._get_required_keys(service, raw)
            missing = [k for k in required if k not in stored]

            svc_status: Dict[str, Any] = {
                "configured": len(missing) == 0 and len(stored) > 0,
                "fields_stored": len(stored),
                "fields_required": len(required),
                "missing_fields": missing,
                "credentials": stored,
            }

            # Fabric-specific: check auth method availability
            if service == "fabric":
                auth_method = self.get_fabric_auth_method()
                svc_status["auth_method"] = auth_method
                svc_status["has_auth"] = auth_method != "none"
                # Only truly configured if workspace + auth both exist
                svc_status["configured"] = (
                    svc_status["configured"] and auth_method != "none"
                )

            # Snowflake-specific: include auth_type in response
            if service == "snowflake":
                svc_status["auth_type"] = stored.get("auth_type", "password")

            status[service] = svc_status
        return status

    # ------------------------------------------------------------------
    # Environment Injection
    # ------------------------------------------------------------------

    def inject_credentials_to_env(self, service: str) -> int:
        """Inject stored credentials into os.environ.

        Args:
            service: Service name ('fabric' or 'snowflake').

        Returns:
            Number of environment variables set.

        Raises:
            RepositoryError: If the service has no stored credentials.
        """
        credentials = self.get_credentials(service, mask_secrets=False)
        if not credentials:
            raise RepositoryError(
                f"No stored credentials for '{service}'. "
                "Configure them via the UI (Settings â†’ Connections)."
            )

        env_map = _ENV_MAP.get(service, {})
        injected = 0

        skip_keys: set = set()
        if service == "fabric":
            has_service_principal = bool(
                credentials.get("client_id") and credentials.get("client_secret")
            )
            if not has_service_principal:
                skip_keys = {"tenant_id", "client_id", "client_secret"}

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
        """Inject credentials for all configured services."""
        results: Dict[str, int] = {}
        for service in _ENV_MAP:
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
            "snowflake": {"password", "private_key", "private_key_passphrase"},
            "fabric_token": {"access_token", "refresh_token"},
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
            else:
                return base + ["password"]

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
    ) -> None:
        """Store MSAL tokens from an interactive login."""
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
                    existing = session.get(Credential, ("fabric_token", key))
                    if existing:
                        existing.value = value
                        existing.is_secret = is_secret
                    else:
                        session.add(
                            Credential(
                                service="fabric_token",
                                key=key,
                                value=value,
                                is_secret=is_secret,
                            )
                        )
                session.commit()

            # Also save tenant_id into fabric service for env injection
            self.save_credentials("fabric", {"tenant_id": tenant_id})
            logger.info("Saved MSAL tokens for user '%s'", account_username)
        except Exception as exc:
            raise RepositoryError(f"Failed to save MSAL token: {exc}") from exc

    def get_msal_token(self) -> Optional[Dict[str, str]]:
        """Retrieve the stored MSAL token."""
        try:
            with self._session() as session:
                rows = session.execute(
                    select(Credential).where(Credential.service == "fabric_token")
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



