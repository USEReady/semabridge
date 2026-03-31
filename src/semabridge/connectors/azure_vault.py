"""
Azure Key Vault Client for Semabridge.

Provides secure access to Azure Key Vault secrets using OAuth tokens
obtained via MSAL device-code flow (same pattern as Fabric login).

Features:
    - Auto token refresh via MSAL ``acquire_token_silent()``
    - In-memory secret caching (5 min TTL)
    - Actionable error messages for 403/404/network failures

Security:
    - No secrets passed as parameters
    - OAuth tokens managed by CredentialManager (DuckDB-backed)
    - Access token refreshed automatically before vault calls
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from semabridge.core.exceptions import ConnectorError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Vault OAuth scope (Azure Key Vault data-plane access)
VAULT_SCOPE = "https://vault.azure.net/.default"

# Cache settings
_SECRET_CACHE_TTL = 300  # 5 minutes


class AzureVaultClient:
    """Client for Azure Key Vault with auto-refresh and caching.

    Uses MSAL ``PublicClientApplication`` for token management,
    reusing the same device-code OAuth flow as Fabric login.

    Args:
        msal_app: MSAL PublicClientApplication instance.
        account: MSAL account dict from ``get_accounts()``.
    """

    _instance: Optional["AzureVaultClient"] = None

    def __init__(
        self,
        msal_app: Any,
        account: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._msal_app = msal_app
        self._account = account
        self._secret_cache: Dict[str, Tuple[str, float]] = {}
        self._last_access_token: Optional[str] = None

    @classmethod
    def set_instance(
        cls,
        msal_app: Any,
        account: Optional[Dict[str, Any]] = None,
    ) -> "AzureVaultClient":
        """Create or replace the singleton instance.

        Called once after a successful vault device-code login.

        Args:
            msal_app: MSAL PublicClientApplication.
            account: MSAL account dict.

        Returns:
            The singleton instance.
        """
        cls._instance = cls(msal_app=msal_app, account=account)
        return cls._instance

    @classmethod
    def get_instance(cls) -> "AzureVaultClient":
        """Return the singleton instance.

        Raises:
            ConnectorError: If no vault session is active.
        """
        if cls._instance is None:
            raise ConnectorError(
                "Azure Key Vault is not connected. "
                "Please login via Settings → Snowflake → Key Pair → Connect Azure Vault."
            )
        return cls._instance

    @classmethod
    def clear_instance(cls) -> None:
        """Clear the singleton (on vault logout)."""
        if cls._instance:
            cls._instance._secret_cache.clear()
        cls._instance = None

    # ------------------------------------------------------------------
    # Token Management
    # ------------------------------------------------------------------

    def _get_valid_token(self) -> str:
        """Return a valid access token, refreshing silently if expired.

        Uses MSAL ``acquire_token_silent()`` which automatically uses
        the refresh token when the access token has expired.

        Returns:
            A valid access token string.

        Raises:
            ConnectorError: If the token cannot be refreshed (user
                must re-login).
        """
        result = self._msal_app.acquire_token_silent(
            scopes=[VAULT_SCOPE],
            account=self._account,
        )

        if result and "access_token" in result:
            self._last_access_token = result["access_token"]
            return result["access_token"]

        raise ConnectorError(
            "Azure Key Vault session has expired. "
            "Please re-login via Settings → Snowflake → Key Pair → Connect Azure Vault."
        )

    # ------------------------------------------------------------------
    # Secret Operations
    # ------------------------------------------------------------------

    def get_secret(self, vault_url: str, secret_name: str) -> str:
        """Fetch a secret from Azure Key Vault.

        Returns a cached value if available and within TTL, otherwise
        fetches from the vault and updates the cache.

        Args:
            vault_url: Key Vault URL (e.g. ``https://my-vault.vault.azure.net``).
            secret_name: Name of the secret to retrieve.

        Returns:
            The secret value string.

        Raises:
            ConnectorError: On permission denied, not found, or network errors.
        """
        cache_key = f"{vault_url.rstrip('/')}/{secret_name}"

        # Check cache
        if cache_key in self._secret_cache:
            value, cached_at = self._secret_cache[cache_key]
            if time.time() - cached_at < _SECRET_CACHE_TTL:
                logger.debug("Cache hit for vault secret '%s'", secret_name)
                return value

        # Fetch from vault
        value = self._fetch_secret(vault_url, secret_name)
        self._secret_cache[cache_key] = (value, time.time())
        return value

    def _fetch_secret(self, vault_url: str, secret_name: str) -> str:
        """Fetch a secret directly from Azure Key Vault.

        Args:
            vault_url: Key Vault URL.
            secret_name: Secret name.

        Returns:
            Secret value.

        Raises:
            ConnectorError: With actionable error messages.
        """
        try:
            from azure.keyvault.secrets import SecretClient
            from azure.core.credentials import AccessToken

            token = self._get_valid_token()

            # Wrap the raw token into a credential object for the SDK
            credential = _StaticTokenCredential(token)
            client = SecretClient(
                vault_url=vault_url.rstrip("/"),
                credential=credential,
            )
            secret = client.get_secret(secret_name)
            logger.info(
                "Fetched secret '%s' from vault '%s'",
                secret_name,
                vault_url,
            )
            return secret.value

        except ImportError:
            raise ConnectorError(
                "Azure Key Vault SDK not installed. "
                "Run: pip install azure-keyvault-secrets azure-identity"
            )
        except Exception as exc:
            error_msg = str(exc)

            if "403" in error_msg or "Forbidden" in error_msg:
                raise ConnectorError(
                    f"Access denied to secret '{secret_name}' in vault "
                    f"'{vault_url}'. Ask your Azure admin to grant you "
                    f"the 'Key Vault Secrets User' role on this vault."
                ) from exc

            if "404" in error_msg or "SecretNotFound" in error_msg:
                raise ConnectorError(
                    f"Secret '{secret_name}' not found in vault "
                    f"'{vault_url}'. Check the secret name and try again."
                ) from exc

            if "connection" in error_msg.lower() or "resolve" in error_msg.lower():
                raise ConnectorError(
                    f"Cannot reach Azure Key Vault '{vault_url}'. "
                    f"Check your network connection and vault URL."
                ) from exc

            raise ConnectorError(
                f"Failed to fetch secret '{secret_name}' from vault "
                f"'{vault_url}': {error_msg}"
            ) from exc

    def list_secrets(self, vault_url: str) -> List[str]:
        """List all secret names in a vault.

        Args:
            vault_url: Key Vault URL.

        Returns:
            List of secret names.

        Raises:
            ConnectorError: On permission or connectivity errors.
        """
        try:
            from azure.keyvault.secrets import SecretClient

            token = self._get_valid_token()
            credential = _StaticTokenCredential(token)
            client = SecretClient(
                vault_url=vault_url.rstrip("/"),
                credential=credential,
            )
            secrets = client.list_properties_of_secrets()
            return sorted([s.name for s in secrets if s.enabled])

        except Exception as exc:
            error_msg = str(exc)

            if "403" in error_msg or "Forbidden" in error_msg:
                raise ConnectorError(
                    f"No access to list secrets in vault '{vault_url}'. "
                    f"Ask your Azure admin to grant you the "
                    f"'Key Vault Secrets User' role."
                ) from exc

            raise ConnectorError(
                f"Failed to list secrets in vault '{vault_url}': {error_msg}"
            ) from exc

    def clear_cache(self) -> None:
        """Clear the in-memory secret cache."""
        self._secret_cache.clear()
        logger.info("Vault secret cache cleared")


class _StaticTokenCredential:
    """Minimal credential wrapper for Azure SDK that uses a pre-fetched token.

    The Azure ``SecretClient`` requires a credential object with a
    ``get_token()`` method.  This wraps a raw access-token string so
    we can reuse our MSAL-managed tokens.

    Args:
        token: A valid OAuth access token.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    def get_token(
        self,
        *scopes: str,
        **kwargs: Any,
    ) -> "AccessToken":
        """Return the static token as an AccessToken namedtuple."""
        from azure.core.credentials import AccessToken

        return AccessToken(self._token, int(time.time()) + 3600)
