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

def _deploy_to_databricks(self, context: RunContext) -> None:
    """Deploy SML metadata projection and measure views to Databricks.

    When ``identity_id`` is present in the target config, credentials
    are resolved from the linked Account row.
    """
    from semabridge.connectors.databricks_publisher import DatabricksPublisher

    # Check for per-account identity_id scoping
    target_configs = getattr(context.config, "targets", None) or []
    identity_id = ""
    for tc in target_configs:
        if isinstance(tc, dict) and tc.get("type") == "databricks":
            identity_id = str(tc.get("identity_id", "") or "").strip()
            break

    if not identity_id:
        # Also check the flat target_config if present
        target_config = getattr(context.config, "target", None)
        if target_config:
            identity_id = str(getattr(target_config, "identity_id", "") or "").strip()

    if identity_id:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Account
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.auth.account_credential_resolver import scoped_account_env

        with db_manager.get_session() as session:
            account = session.execute(
                select(Account).where(
                    Account.connector_type == "DATABRICKS",
                    Account.id == identity_id,
                )
            ).scalars().first()

            if not account:
                raise DeploymentError(
                    f"No Databricks account found for identity_id '{identity_id}'. "
                    "Please link this account in the Connections panel."
                )

            with scoped_account_env(account, session):
                logger.info(
                    "Databricks deployment scoped to account %s (%s)",
                    account.tag, identity_id,
                )
                from semabridge.core.settings import reload_settings
                scoped_settings = reload_settings()
                publisher = DatabricksPublisher(
                    scoped_settings.databricks, behavior=context.behavior
                )
                context.target_artifact_path = publisher.publish(context.sml_model)
                publish_summary = publisher.get_last_publish_summary()
                context.routing_summary = publish_summary.get("routing_summary") if isinstance(publish_summary, dict) else None
                self._raise_if_databricks_fallback_failed(context, publish_summary)
                return

    # Default: use global env vars
    publisher = DatabricksPublisher(context.config.databricks, behavior=context.behavior)
    context.target_artifact_path = publisher.publish(context.sml_model)
    publish_summary = publisher.get_last_publish_summary()
    context.routing_summary = publish_summary.get("routing_summary") if isinstance(publish_summary, dict) else None
    self._raise_if_databricks_fallback_failed(context, publish_summary)

def _raise_if_databricks_fallback_failed(
    self,
    context: RunContext,
    publish_summary: Any,
) -> None:
    """Fail deployment when Databricks SQL fallback enters terminal FAILED state."""
    if not isinstance(publish_summary, dict):
        return

    fallback_state = str(publish_summary.get("sql_fallback_state") or "").strip().upper()
    if fallback_state != "FALLBACK_FAILED":
        return

    fallback_reason = str(publish_summary.get("sql_fallback_reason") or "").strip()
    if fallback_reason:
        raise DeploymentError(
            f"Databricks SQL fallback failed for model '{context.project_id}': {fallback_reason}"
        )
    raise DeploymentError(f"Databricks SQL fallback failed for model '{context.project_id}'")
