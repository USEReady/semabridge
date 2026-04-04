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

        Reads from the database first.  If the database has no rows for this
        service, falls back to os.environ (populated from .env at startup) so
        that credentials configured only in .env are still surfaced in the UI.

        Args:
            service: Service name ('fabric', 'snowflake', 'databricks').
            mask_secrets: If True, secret values are replaced with '••••••••'.

        Returns:
            Dictionary of credential key-value pairs.
        """
        secret_keys = self._get_secret_keys(service)
        env_map = _ENV_MAP.get(service, {})
        result: Dict[str, str] = {}

        # --- Step 1: Read from database ---
        try:
            with self._session() as session:
                rows = session.execute(
                    select(Credential).where(Credential.service == service)
                ).scalars().all()
                for row in rows:
                    if mask_secrets and row.is_secret:
                        result[row.key] = "••••••••"
                    else:
                        result[row.key] = row.value
        except Exception:
            pass

        # --- Step 2: Supplement from os.environ for any keys NOT already in DB.
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

    def get_connection_status(self) -> Dict[str, Any]:
        """Get the configuration status for all supported services.

        Returns a dict per service with standardized 'status' field ("connected"/"disconnected"),
        plus existing details for UI display.
        """
        status: Dict[str, Any] = {}
        for service in _ENV_MAP:
            stored = self.get_credentials(service, mask_secrets=True)
            # Use raw (unmasked) credentials for auth-type detection
            raw = self.get_credentials(service, mask_secrets=False)
            required = self._get_required_keys(service, raw)
            missing = [k for k in required if k not in stored]

            is_configured = len(missing) == 0 and len(stored) > 0

            svc_status: Dict[str, Any] = {
                "configured": is_configured,
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
                is_configured = is_configured and auth_method != "none"
                svc_status["configured"] = is_configured

            # Snowflake-specific: include auth_type in response
            if service == "snowflake":
                svc_status["auth_type"] = stored.get("auth_type", "password")

            # Databricks-specific: include auth_type and auth_method
            if service == "databricks":
                auth_method = self.get_databricks_auth_method()
                svc_status["auth_method"] = auth_method
                svc_status["auth_type"] = stored.get("auth_type", "pat")

            # Add standardized status field
            svc_status["status"] = "connected" if is_configured else "disconnected"

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
            "databricks": {"token", "client_secret", "access_token", "refresh_token"},
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
    ) -> None:
        """Store Databricks MSAL tokens from an interactive device code login.

        Args:
            access_token: The Azure AD access token scoped to Databricks.
            refresh_token: The refresh token for silent re-auth.
            account_username: Azure AD user principal name.
            tenant_id: Azure AD tenant ID.
            host: Databricks workspace hostname.
            warehouse_id: SQL Warehouse ID.
            catalog: Unity Catalog name.
            schema_name: Target schema name.
            expires_in: Token lifetime in seconds.
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

        self.save_credentials("databricks", token_data)
        logger.info(
            "Saved Databricks MSAL tokens for user '%s' (tenant: %s)",
            account_username,
            tenant_id[:8] + "..." if len(tenant_id) > 8 else tenant_id,
        )

    def get_databricks_token(self) -> Optional[Dict[str, str]]:
        """Retrieve stored Databricks MSAL token data.

        Returns:
            Dictionary with access_token, refresh_token, expires_at, etc.
            or None if no interactive token exists.
        """
        creds = self.get_credentials("databricks", mask_secrets=False)
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
