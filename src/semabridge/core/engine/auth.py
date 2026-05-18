from __future__ import annotations

import time
import uuid
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Literal, Optional
import yaml
from pydantic import Field
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.core.settings import Settings, get_settings
from semabridge.core.config_loader import get_project_file_path
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.run_summary import (
    STEP_NAMES,
    RunStatus,
    RunSummary,
    StepStatus,
    create_run_summary,
)
from semabridge.core.source_format import (
    SourceFormat,
    from_fabric_tmdl,
    from_pbix_tmdl,
    from_snowflake_metadata,
)
from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel, SMLRelationship
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.exceptions import (
    ConfigValidationError,
    AuthenticationError,
    ExtractionError,
    SourceFormatError,
    ConversionError,
    PersistenceError,
    DeploymentError,
)

logger = get_logger(__name__)

def _step3_resolve_auth(self, context: RunContext) -> None:
    """
    Step 3: Resolve authentication.

    - Resolve credentials from environment variables
    - Validate all required variables present
    - Do not allow inline secrets in config files
    """
    self._current_step = 3
    logger.info("Step 3: Resolving authentication")

    config = context.config
    missing = []
    auth_sources: list[str] = []

    # Validate source auth
    if context.source_type == "snowflake":
        if not config.validate_snowflake():
            missing.append("Snowflake credentials (SNOWFLAKE_*)")
    elif context.source_type == "fabric":
        if context.behavior.features.offline_mode:
            logger.info("Step 3: OFFLINE mode enabled - skipping Fabric auth validation")
            auth_sources.append("OFFLINE")
        else:
            fabric_env_ok = config.validate_fabric()
            identity_id = str(getattr(getattr(config, "source", None), "identity_id", "") or "").strip()
            fabric_identity_ok = self._has_fabric_identity_auth(identity_id)
            fabric_ui_ok = self._has_fabric_interactive_auth()
            if not (fabric_env_ok or fabric_identity_ok or fabric_ui_ok):
                missing.append("Fabric credentials (FABRIC_*)")
            elif fabric_identity_ok:
                auth_sources.append("DB identity")
            elif fabric_ui_ok:
                auth_sources.append("UI token")
            else:
                auth_sources.append("ENV")
    # PBIX source needs no external auth — local file
    elif context.source_type == "pbix":
        pass

    # Validate target auth
    if context.target_type == "snowflake":
        if not config.validate_snowflake():
            missing.append("Snowflake credentials (SNOWFLAKE_*)")
    elif context.target_type == "fabric":
        fabric_env_ok = config.validate_fabric()
        identity_id = str(getattr(getattr(config, "target", None), "identity_id", "") or "").strip()
        fabric_identity_ok = self._has_fabric_identity_auth(identity_id)
        fabric_ui_ok = self._has_fabric_interactive_auth()
        if not (fabric_env_ok or fabric_identity_ok or fabric_ui_ok):
            missing.append("Fabric credentials (FABRIC_*)")
        elif fabric_identity_ok:
            auth_sources.append("DB identity")
        elif fabric_ui_ok:
            auth_sources.append("UI token")
        else:
            auth_sources.append("ENV")
    elif context.target_type == "databricks":
        if not config.validate_databricks():
            missing.append("Databricks credentials (DATABRICKS_*)")

    if missing:
        msg = f"Missing authentication: {', '.join(missing)}"
        self._record_step(3, StepStatus.FAILED, msg)
        raise AuthenticationError(msg)

    if auth_sources:
        source_label = "/".join(sorted(set(auth_sources)))
        self._record_step(3, StepStatus.SUCCESS, f"Authentication resolved from {source_label}")
    else:
        self._record_step(3, StepStatus.SUCCESS, "Authentication resolved")

def _has_fabric_interactive_auth(self) -> bool:
    """Return True when a valid Fabric interactive token exists in credential store."""
    try:
        from semabridge.repository.credential_manager import CredentialManager

        cm = CredentialManager()
        return cm.get_fabric_auth_method() == "interactive" and cm.has_valid_token()
    except Exception as exc:
        logger.warning("_has_fabric_interactive_auth: credential lookup failed: %s", exc)
        return False

def _has_fabric_identity_auth(self, identity_id: str) -> bool:
    """Return True when a selected Fabric identity is resolvable from the DB."""
    if not identity_id:
        return False
    try:
        from semabridge.api.services.connection_domain_service import _resolve_fabric_access_token

        token = _resolve_fabric_access_token(None, identity_id)
        return bool(token)
    except Exception as exc:
        logger.warning("_has_fabric_identity_auth: identity lookup failed for %s: %s", identity_id, exc)
        return False
