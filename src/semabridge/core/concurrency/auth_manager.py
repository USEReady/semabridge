"""
Authentication manager for concurrent worker processes.

Validates and distributes credentials to workers before
spawning, avoiding per-worker credential resolution overhead
and providing clear fail-fast error messages.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Required environment variables per connector type
_FABRIC_REQUIRED: Dict[str, str] = {
    "FABRIC_TENANT_ID": "Azure AD Tenant ID",
    "FABRIC_CLIENT_ID": "Azure AD Application (Client) ID",
    "FABRIC_CLIENT_SECRET": "Azure AD Client Secret",
}

_FABRIC_WORKSPACE: Dict[str, str] = {
    "FABRIC_WORKSPACE_ID": "Fabric Workspace ID",
}

_SNOWFLAKE_REQUIRED: Dict[str, str] = {
    "SNOWFLAKE_ACCOUNT": "Snowflake Account",
    "SNOWFLAKE_USER": "Snowflake User",
}

_SNOWFLAKE_AUTH_METHODS: List[Dict[str, str]] = [
    {"SNOWFLAKE_PASSWORD": "Snowflake Password"},
    {"SNOWFLAKE_PRIVATE_KEY_PATH": "Snowflake Private Key Path"},
    {"SNOWFLAKE_AUTHENTICATOR": "Snowflake Authenticator (e.g. externalbrowser)"},
]


class AuthenticationManager:
    """Pre-validate credentials and provide them to worker processes.

    Before spawning workers, this manager checks that the required
    environment variables for the configured connector(s) are present.
    If a variable is missing, it raises a clear error instead of
    letting workers fail individually with obscure messages.

    Usage::

        auth = AuthenticationManager(source="snowflake", target="fabric")
        auth.validate()  # Raises on missing creds
        env_snapshot = auth.get_credential_snapshot()
        # Pass env_snapshot to workers

    Args:
        source: Source connector type.
        target: Target connector type (optional).
    """

    def __init__(
        self,
        source: str = "snowflake",
        target: Optional[str] = None,
    ) -> None:
        self._source = source.lower()
        self._target = target.lower() if target else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Validate that all required credentials are present.

        Raises:
            MissingCredentialError: If any required variable is unset.
        """
        missing = self._check_missing()
        if missing:
            from semabridge.core.exceptions import MissingCredentialError

            details = "\n".join(f"  • {var} — {desc}" for var, desc in missing)
            raise MissingCredentialError(
                f"Missing credentials for concurrent processing:\n{details}"
            )

        logger.info("All credentials validated for source=%s, target=%s",
                     self._source, self._target)

    def get_credential_snapshot(self) -> Dict[str, str]:
        """Capture a snapshot of credential environment variables.

        Returns only the credential-related variables needed by the
        configured connectors, so workers inherit a minimal env.

        Returns:
            Dictionary of environment variable name → value.
        """
        relevant_vars = self._get_relevant_vars()
        snapshot: Dict[str, str] = {}
        for var in relevant_vars:
            value = os.environ.get(var)
            if value:
                snapshot[var] = value
        return snapshot

    def get_missing_credentials(self) -> List[tuple[str, str]]:
        """Return a list of (var_name, description) for missing creds.

        Returns:
            List of (variable_name, human_description) tuples.
        """
        return self._check_missing()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_missing(self) -> List[tuple[str, str]]:
        """Check which required credentials are missing."""
        missing: List[tuple[str, str]] = []

        if self._source == "fabric" or self._target == "fabric":
            for var, desc in _FABRIC_REQUIRED.items():
                if not os.environ.get(var):
                    missing.append((var, desc))

        if self._source == "snowflake" or self._target == "snowflake":
            for var, desc in _SNOWFLAKE_REQUIRED.items():
                if not os.environ.get(var):
                    missing.append((var, desc))

            # At least one auth method must be present
            if not any(
                os.environ.get(var)
                for method in _SNOWFLAKE_AUTH_METHODS
                for var in method
            ):
                missing.append((
                    "SNOWFLAKE_PASSWORD|SNOWFLAKE_PRIVATE_KEY_PATH|SNOWFLAKE_AUTHENTICATOR",
                    "At least one Snowflake auth method",
                ))

        return missing

    def _get_relevant_vars(self) -> Set[str]:
        """Get the set of credential env vars for the configured connectors."""
        relevant: Set[str] = set()

        if self._source == "fabric" or self._target == "fabric":
            relevant.update(_FABRIC_REQUIRED.keys())
            relevant.update(_FABRIC_WORKSPACE.keys())

        if self._source == "snowflake" or self._target == "snowflake":
            relevant.update(_SNOWFLAKE_REQUIRED.keys())
            for method in _SNOWFLAKE_AUTH_METHODS:
                relevant.update(method.keys())

        # Also grab generic vars
        relevant.update({
            "SEMABRIDGE_CONFIG",
            "SEMABRIDGE_DB_PATH",
        })

        return relevant
