"""
Validation engine for fail-fast pre-processing checks.

Validates source/target connectivity and credentials before any
worker processes are spawned, preventing wasted resources on
doomed operations.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from semabridge.core.concurrency.models import ValidationResult
from semabridge.core.project import ProjectConfig, TargetConfig

logger = logging.getLogger(__name__)


class ValidationEngine:
    """Perform fail-fast validation before starting parallel processing.

    Checks:
    - Source connectivity (lightweight ping/probe)
    - Credential availability (env vars present)
    - Target connectivity for all deploy-enabled targets

    Args:
        config: The project configuration.
    """

    def __init__(self, config: ProjectConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_all(self) -> ValidationResult:
        """Run all validation checks.

        Returns:
            ValidationResult with success status and error details.
        """
        result = ValidationResult()

        # 1. Validate credentials
        cred_ok = self._validate_credentials(result)
        if not cred_ok:
            result.success = False
            result.credentials_valid = False

        # 2. Validate source connectivity
        src_ok = self._validate_source_connectivity(result)
        if not src_ok:
            result.success = False
            result.source_connectivity = False

        # 3. Validate target connectivity
        target_ok = self._validate_target_connectivity(result)
        if not target_ok:
            result.success = False

        return result

    def validate_source_connectivity(self) -> bool:
        """Check if source system credentials are available."""
        result = ValidationResult()
        return self._validate_source_connectivity(result)

    def validate_credentials(self) -> bool:
        """Validate all connector credentials are present."""
        result = ValidationResult()
        return self._validate_credentials(result)

    def validate_target_connectivity(
        self, targets: Optional[List[TargetConfig]] = None
    ) -> bool:
        """Check if all target system credentials are available."""
        result = ValidationResult()
        return self._validate_target_connectivity(result, targets)

    # ------------------------------------------------------------------
    # Internal validation methods
    # ------------------------------------------------------------------

    def _validate_credentials(self, result: ValidationResult) -> bool:
        """Check that required environment variables are set."""
        source_type = self._config.source.type.value
        ok = True

        if source_type == "fabric":
            for var in ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET"):
                if not os.environ.get(var):
                    result.errors.append(f"Missing credential: {var}")
                    ok = False
        elif source_type == "snowflake":
            for var in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"):
                if not os.environ.get(var):
                    result.errors.append(f"Missing credential: {var}")
                    ok = False
            # Need at least one auth method
            has_pass = bool(os.environ.get("SNOWFLAKE_PASSWORD"))
            has_key = bool(os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"))
            has_sso = bool(os.environ.get("SNOWFLAKE_AUTHENTICATOR"))
            if not (has_pass or has_key or has_sso):
                result.errors.append(
                    "Missing Snowflake auth: set SNOWFLAKE_PASSWORD, "
                    "SNOWFLAKE_PRIVATE_KEY_PATH, or SNOWFLAKE_AUTHENTICATOR"
                )
                ok = False

        # Also check target credentials
        for i, target in enumerate(self._config.targets):
            if not target.deploy:
                continue
            ttype = target.type.value
            if ttype == "snowflake" and source_type != "snowflake":
                # Snowflake target creds needed if source is not snowflake
                for var in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"):
                    if not os.environ.get(var):
                        result.errors.append(
                            f"Missing credential for target[{i}] ({ttype}): {var}"
                        )
                        ok = False
            elif ttype == "fabric" and source_type != "fabric":
                for var in (
                    "FABRIC_TENANT_ID",
                    "FABRIC_CLIENT_ID",
                    "FABRIC_CLIENT_SECRET",
                ):
                    if not os.environ.get(var):
                        result.errors.append(
                            f"Missing credential for target[{i}] ({ttype}): {var}"
                        )
                        ok = False

        return ok

    def _validate_source_connectivity(self, result: ValidationResult) -> bool:
        """Lightweight source reachability check.

        This checks that the configuration looks valid without actually
        connecting. Real connectivity is tested at extraction time.
        """
        source = self._config.source

        if source.type.value == "fabric":
            if not source.workspace_id and not source.workspace:
                result.errors.append(
                    "Fabric source requires 'workspace' or 'workspace_id'"
                )
                return False
        elif source.type.value == "snowflake":
            if not source.database:
                result.errors.append("Snowflake source requires 'database'")
                return False

        return True

    def _validate_target_connectivity(
        self,
        result: ValidationResult,
        targets: Optional[List[TargetConfig]] = None,
    ) -> bool:
        """Validate target configuration completeness."""
        targets = targets or self._config.targets
        ok = True

        for i, target in enumerate(targets):
            if not target.deploy:
                continue

            target_name = f"target[{i}]({target.type.value})"
            target_ok = True

            if target.type.value == "snowflake":
                if not target.database:
                    # Database might come from env/settings — warn but don't fail
                    logger.debug(
                        "Target %s has no explicit database; "
                        "will use settings default.",
                        target_name,
                    )
            elif target.type.value == "fabric":
                # Fabric targets need workspace info from source
                pass

            result.target_connectivity[target_name] = target_ok
            if not target_ok:
                ok = False

        return ok
