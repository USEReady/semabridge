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
    from_fabric_tmsl,
    from_pbix_tmsl,
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

def _deploy_to_fabric(self, context: RunContext) -> None:
    """Deploy to Fabric with multi-account isolation."""
    from semabridge.connectors.fabric_publisher import FabricPublisher

    # Check for per-account identity_id scoping
    target_configs = getattr(context.config, "targets", None) or []
    identity_id = ""
    for tc in target_configs:
        if isinstance(tc, dict) and tc.get("type") == "fabric":
            identity_id = str(tc.get("identity_id", "") or "").strip()
            break

    if not identity_id:
        # Also check the flat target_config if present
        target_config = getattr(context.config, "target", None)
        if target_config and getattr(target_config, "type", "") == "fabric":
            identity_id = str(getattr(target_config, "identity_id", "") or "").strip()

    if identity_id:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Account
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.auth.credential_builder import build_fabric_config

        with db_manager.get_session() as session:
            account = session.execute(
                select(Account).where(
                    Account.connector_type == "FABRIC",
                    Account.id == identity_id,
                )
            ).scalars().first()

            if not account:
                raise DeploymentError(
                    f"No Fabric account found for identity_id '{identity_id}'. "
                    "Please link this account in the Connections panel."
                )

            # Thread-safe: builds an isolated FabricConfig object rather than
            # mutating os.environ (scoped_account_env), which is unsafe when
            # multiple syncs from the same batch run concurrently.
            fabric_cfg, fabric_token = build_fabric_config(account, session, context.config.fabric)
            logger.info(
                "Fabric deployment scoped to account %s (%s) — thread-safe config, no os.environ mutation",
                account.tag, identity_id,
            )

            # Ensure the workspace_id explicitly requested by the project is used
            # instead of any default ambient workspace on the Account
            target_workspace = ""
            for tc in target_configs:
                if isinstance(tc, dict) and tc.get("type") == "fabric":
                    target_workspace = str(tc.get("workspace_id", "") or "").strip()
                    break
            if not target_workspace:
                if getattr(context.config, "target", None) and getattr(context.config.target, "type", "") == "fabric":
                    target_workspace = getattr(context.config.target, "workspace_id", "")
            if target_workspace:
                fabric_cfg.workspace_id = target_workspace

            sf_cfg = context.config.snowflake
            publisher = FabricPublisher(fabric_cfg)
            if fabric_token:
                # FabricPublisher has no constructor param for a pre-resolved
                # token; it otherwise falls back to FABRIC_ACCESS_TOKEN from
                # os.environ (see get_fabric_access_token_from_env()), which
                # is exactly the shared-process-global state this fix avoids.
                publisher._access_token = fabric_token
                publisher._token_expiry = time.time() + 1800
            publisher.publish(
                sml_model=context.sml_model,
                model_name=context.project_id,
                snowflake_server=sf_cfg.account,
                snowflake_warehouse=sf_cfg.warehouse,
                snowflake_database=sf_cfg.database,
                snowflake_schema=sf_cfg.schema_name,
                overwrite=True,
            )
            return

    # --- Fallback to legacy global logic ---
    try:
        from semabridge.repository.credential_manager import CredentialManager
        cm = CredentialManager()
        cm.inject_credentials_to_env("fabric")
    except Exception as _inj_exc:
        logger.debug("Credential re-injection skipping: %s", _inj_exc)

    os.environ.pop("FABRIC_ACCESS_TOKEN", None)
    os.environ.pop("FABRIC_REFRESH_TOKEN", None)

    from semabridge.core.settings import get_settings
    get_settings.cache_clear()
    config = get_settings()

    publisher = FabricPublisher(config.fabric)
    publisher.publish(
        sml_model=context.sml_model,
        model_name=context.project_id,
        snowflake_server=config.snowflake.account,
        snowflake_warehouse=config.snowflake.warehouse,
        snowflake_database=config.snowflake.database,
        snowflake_schema=config.snowflake.schema_name,
        overwrite=True,
    )
