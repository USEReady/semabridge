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

def _deploy_to_snowflake(self, context: RunContext) -> None:
    """
    Deploy to Snowflake, dispatching based on ``deployment_method`` setting
    and utilizing single-tenant environment scoping if ``identity_id`` is supplied.
    """
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    target_configs = getattr(context.config, "targets", None) or []
    identity_id = ""
    for tc in target_configs:
        if isinstance(tc, dict) and tc.get("type") == "snowflake":
            identity_id = str(tc.get("identity_id", "") or "").strip()
            break

    if not identity_id:
        # Also check the flat target config if present
        target_config = getattr(context.config, "target", None)
        if target_config and getattr(target_config, "type", "") == "snowflake":
            identity_id = str(getattr(target_config, "identity_id", "") or "").strip()

    if identity_id:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Account
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.auth.account_credential_resolver import scoped_account_env

        with db_manager.get_session() as session:
            account = session.execute(
                select(Account).where(
                    Account.connector_type == "SNOWFLAKE",
                    Account.id == identity_id,
                )
            ).scalars().first()

            if not account:
                logger.warning(
                    "No linked Snowflake account found for identity_id %s; falling back to global Snowflake settings",
                    identity_id,
                )
                sf_cfg = context.config.snowflake
                self._do_snowflake_deploy(context, sf_cfg)
                return

            with scoped_account_env(account, session):
                logger.info(
                    "Snowflake deployment scoped to account %s (%s)",
                    account.tag, identity_id,
                )
                from semabridge.core.settings import reload_settings
                scoped_settings = reload_settings()

                # Override default roles and warehouses if specified in target config
                sf_cfg = scoped_settings.snowflake
                self._do_snowflake_deploy(context, sf_cfg)
                return

    # Fallback to legacy global execution
    sf_cfg = context.config.snowflake
    self._do_snowflake_deploy(context, sf_cfg)

def _do_snowflake_deploy(self, context: RunContext, sf_cfg) -> None:
    """Inner helper to perform Snowflake deployment with resolved config."""
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    emitter = SnowflakeEmitter(sf_cfg, behavior=context.behavior)
    deployment_method = getattr(sf_cfg, "deployment_method", "ddl") or "ddl"

    # DDL path
    if deployment_method in ("ddl", "both"):
        deployed = emitter.deploy(
            context.sml_model,
            sync_mode=getattr(context, "sync_mode", "copy"),
        )
        if not deployed:
            error_msg = emitter.last_deployment_error or "unknown deployment error"

            # Check if this is a database-not-found error
            if "object does not exist" in error_msg.lower() and "use database" in error_msg.lower():
                db_name, _ = sf_cfg._resolved_db_schema() if hasattr(sf_cfg, "_resolved_db_schema") else ("UNKNOWN", "")
                raise DeploymentError(
                    f"Snowflake deployment failed: Target database does not exist.\n"
                    f"Database: {db_name}\n"
                    f"Error: {error_msg}\n\n"
                    f"SOLUTIONS:\n"
                    f"1. Create the database manually in Snowflake: CREATE DATABASE {db_name}\n"
                    f"2. Or enable auto-create in config: add 'auto_create_database: true' to snowflake section in semabridge.yaml\n"
                    f"3. Or check if the database name in config is correct."
                )

            raise DeploymentError(
                f"Snowflake DDL deployment returned unsuccessful status: {error_msg}"
            )
        self._export_inferred_osi_artifacts(context)

    # Stored-procedure / Cortex YAML path
    if deployment_method in ("yaml_stored_procedure", "both"):
        try:
            import snowflake.connector

            yaml_content = emitter.generate_cortex_yaml(context.sml_model)
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
            kwargs = get_snowflake_connect_kwargs(sf_cfg)
            kwargs["session_parameters"] = {
                "QUERY_TAG": "SemaBridge_CortexYamlDeploy"
            }
            conn = snowflake.connector.connect(**kwargs)
            try:
                cur = conn.cursor()
                emitter.deploy_cortex_yaml(cur, context.sml_model, yaml_content)
                logger.info("Cortex YAML stored-procedure deploy complete")
            finally:
                conn.close()
        except Exception as exc:
            raise DeploymentError(
                f"Cortex YAML stored-procedure deploy failed: {exc}"
            ) from exc

    # Optional: Sync materialized DAX measures to MEASURES_* tables
    if context.source_type == "fabric" and self._should_sync_measures(context):
        self._sync_fabric_measures(context, emitter)

def _export_inferred_osi_artifacts(self, context: RunContext) -> None:
    """Write OSI JSON/YAML with the latest inferred column datatypes."""
    try:
        import json
        import yaml
        from semabridge.converter.sml_to_osi import SMLToOSIConverter

        if not context.sml_model:
            return

        logger.info(
            "Pre-export SML summary: datasets=%s, metrics=%s",
            len(context.sml_model.datasets),
            len(context.sml_model.metrics),
        )
        logger.info(
            "Pre-export dataset names: %s",
            [ds.unique_name for ds in context.sml_model.datasets],
        )
        logger.info(
            "Pre-export metric names: %s",
            [m.unique_name for m in context.sml_model.metrics],
        )

        osi_model = SMLToOSIConverter().to_osi(context.sml_model)
        context.osi_model = osi_model

        out_dir = self._model_output_dir("inferred", model_name=context.project_id)
        json_path = out_dir / "osi_inferred.json"
        yaml_path = out_dir / "osi_inferred.yaml"

        osi_dict = osi_model.model_dump(mode="json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(osi_dict, f, indent=2)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(osi_dict, f, sort_keys=False)

        logger.info(f"Generated OSI JSON with inferred types: {json_path}")
        logger.info(f"Generated OSI YAML with inferred types: {yaml_path}")
    except Exception as ex:
        logger.warning(f"Failed to export inferred OSI artifacts (non-fatal): {ex}")

def _should_sync_measures(self, context: RunContext) -> bool:
    """
    Determine if measure sync should be performed.

    Checks configuration and model for syncable measures.
    """
    # Check if any measures need sync (those that couldn't be translated)
    if not context.sml_model:
        return False

    # Check for measures that are sync-enabled but lack SQL expression
    for metric in context.sml_model.metrics:
        if metric.sync_enabled and not metric.sql_expression:
            return True

    return False

def _sync_fabric_measures(self, context: RunContext, emitter) -> None:
    """
    Sync Fabric measures to Snowflake MEASURES_ tables.

    Evaluates DAX measures and writes results to Snowflake.
    """
    from semabridge.connectors.fabric_extractor import FabricExtractor

    config = context.config

    # Align workspace with extracted source context when available.
    ws_id = None
    if context.source_format:
        ws_id = getattr(context.source_format, "workspace_id", None)

    if ws_id and config.fabric.workspace_id != ws_id:
        config.fabric.workspace_id = ws_id

    interactive_token: Optional[str] = None
    try:
        from semabridge.repository.credential_manager import CredentialManager

        cm = CredentialManager()

        stored_fabric = cm.get_credentials("fabric", mask_secrets=False)
        stored_workspace_id = (stored_fabric.get("workspace_id") or "").strip()
        if stored_workspace_id and not ws_id:
            config.fabric.workspace_id = stored_workspace_id

        token_data = cm.get_msal_token()
        if cm.get_fabric_auth_method() == "interactive" and cm.has_valid_token() and token_data:
            interactive_token = token_data.get("access_token")
            logger.debug("sync_to_fabric: injecting interactive token from credential store")
    except Exception as exc:
        logger.warning("sync_to_fabric: credential lookup failed, falling back to env auth: %s", exc)

    # Initialize Fabric extractor
    fabric_extractor = FabricExtractor(config.fabric)
    if interactive_token:
        fabric_extractor._access_token = interactive_token
        fabric_extractor._token_expires_at = time.time() + 1800

    # Get dataset ID from source format metadata
    dataset_id = None
    if context.source_format:
        dataset_id = getattr(context.source_format, "dataset_id", None)

    if not dataset_id:
        dataset_name = None
        if context.source_format:
            dataset_name = getattr(context.source_format, "dataset_name", None)
        logger.warning(
            "Cannot sync measures: no dataset ID available "
            f"(dataset_name={dataset_name or 'unknown'})"
        )
        return

    # Sync all measures
    try:
        results = emitter.sync_all_measures(
            sml=context.sml_model,
            fabric_extractor=fabric_extractor,
            dataset_id=dataset_id,
        )

        success_count = sum(1 for r in results.values() if r.get("status") == "success")
        logger.info(f"Measure sync complete: {success_count}/{len(results)} measures synced")

    except Exception as e:
        logger.error(f"Measure sync failed: {e}")
        # Don't fail the deployment, just log warning
        logger.warning("Continuing despite measure sync failure")
