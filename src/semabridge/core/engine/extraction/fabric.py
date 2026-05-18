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
from semabridge.adapters.tmsl_translator import translate_tmsl_to_internal_sml

logger = get_logger(__name__)

def _extract_fabric(
    self,
    context: RunContext,
    dataset_id: Optional[str],
    workspace_id: Optional[str],
) -> SourceFormat:
    """Extract from Fabric."""
    from semabridge.connectors.fabric_extractor import FabricExtractor

    config = context.config
    source_config = getattr(config, "source", None)
    fabric_cfg = config.fabric
    ws_id = workspace_id or fabric_cfg.workspace_id
    interactive_token: Optional[str] = None
    stored_workspace_id: str = ""
    identity_id: str = str(getattr(source_config, "identity_id", "") or "").strip()

    if context.behavior.features.offline_mode:
        offline_path = Path(context.behavior.features.offline_fabric_model_path)
        if not offline_path.exists():
            raise ExtractionError(
                f"offline_mode enabled but file not found: {offline_path}"
            )

        logger.info(
            "Step 4: Running in OFFLINE mode (skipping Fabric API) using %s",
            offline_path,
        )

        with open(offline_path, "r", encoding="utf-8") as f:
            tmdl = json.load(f)

        resolved_dataset_id = dataset_id or context.project_id
        row_counts: dict[str, int] = {}

        source_format = from_fabric_tmdl(
            project_id=context.project_id,
            run_id=context.run_id,
            tmdl=tmdl,
            workspace_id=ws_id,
            dataset_id=resolved_dataset_id,
            row_counts=row_counts,
        )

        table_count = len(tmdl.get("model", {}).get("tables", []))
        self._record_step(4, StepStatus.SUCCESS, f"OFFLINE extract loaded {table_count} tables")
        return source_format

    try:
        from semabridge.repository.credential_manager import CredentialManager

        cm = CredentialManager()
        stored_fabric = cm.get_credentials("fabric", mask_secrets=False)
        stored_workspace_id = (stored_fabric.get("workspace_id") or "").strip()
        configured_workspace_id = str(getattr(fabric_cfg, "workspace_id", "") or "").strip()
        if not workspace_id and not configured_workspace_id and stored_workspace_id:
            ws_id = stored_workspace_id

        if identity_id:
            try:
                from semabridge.api.services.connection_domain_service import _resolve_fabric_access_token

                interactive_token = _resolve_fabric_access_token(None, identity_id)
                logger.debug("_extract_fabric: injecting identity-scoped interactive token for %s", identity_id)
            except Exception as identity_exc:
                logger.warning(
                    "_extract_fabric: failed to resolve token for identity %s: %s",
                    identity_id,
                    identity_exc,
                )
                raise ExtractionError(
                    f"Could not resolve Fabric token for identity '{identity_id}'. "
                    f"Please re-authenticate this account in the Connections panel."
                ) from identity_exc

        if not interactive_token and not identity_id:
            # Only fall back to global credential store when NO identity_id is specified.
            # This prevents cross-account contamination in multi-account scenarios.
            token_data = cm.get_msal_token()
            if cm.get_fabric_auth_method() == "interactive" and cm.has_valid_token() and token_data:
                interactive_token = token_data.get("access_token")
                logger.debug("_extract_fabric: injecting interactive token from credential store (no identity_id)")
    except ExtractionError:
        raise
    except Exception as exc:
        logger.warning("_extract_fabric: credential lookup failed, falling back to env auth: %s", exc)

    if not dataset_id:
        raise ExtractionError("dataset_id is required for Fabric source")

    guid_pattern = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

    def _is_guid(value: str) -> bool:
        return bool(value and re.match(guid_pattern, value))

    requested_ws = str(ws_id or "").strip()
    configured_ws = str(fabric_cfg.workspace_id or "").strip()
    stored_ws = str(stored_workspace_id or "").strip()

    # UI aliases (for example, semabridge-local) are not accepted by Fabric API.
    # Prefer known GUIDs from credentials or environment-backed config.
    if requested_ws and not _is_guid(requested_ws):
        if _is_guid(stored_ws):
            logger.info(
                "Fabric workspace '%s' appears to be an alias; using credential workspace GUID '%s'",
                requested_ws,
                stored_ws,
            )
            requested_ws = stored_ws
        elif _is_guid(configured_ws):
            logger.info(
                "Fabric workspace '%s' appears to be an alias; using configured workspace GUID '%s'",
                requested_ws,
                configured_ws,
            )
            requested_ws = configured_ws

    ws_id = requested_ws or configured_ws or stored_ws

    if ws_id and fabric_cfg.workspace_id != ws_id:
        fabric_cfg.workspace_id = ws_id


    # Refresh token just-in-time at Stage 4 for the source identity.
    # context.scoped_fabric_token was resolved at Stage 3. In long-queued
    # batch runs (e.g. scheduled jobs) that token may have expired by the
    # time Stage 4 executes. Re-resolving here mirrors the Stage 9 pattern
    # and guarantees freshness for the Fabric extraction API call.
    if identity_id:
        try:
            from semabridge.api.services.connection_domain_service import _resolve_fabric_access_token
            jit_token = _resolve_fabric_access_token(None, identity_id)
            if jit_token:
                interactive_token = jit_token
                logger.info(
                    "_extract_fabric: resolved JIT fresh token for source identity %s",
                    identity_id,
                )
        except Exception as exc:
            if interactive_token:
                logger.info(
                    "_extract_fabric: JIT token resolution failed for %s; "
                    "continuing with Stage 3 scoped token (%s)",
                    identity_id, exc,
                )
            else:
                logger.warning(
                    "_extract_fabric: JIT token resolution failed for %s and no "
                    "Stage 3 scoped token is available: %s",
                    identity_id, exc,
                )

    extractor = FabricExtractor(fabric_cfg)

    if interactive_token:
        extractor._access_token = interactive_token
        extractor._token_expires_at = time.time() + 1800

    resolved_dataset_id = extractor.resolve_model_id(dataset_id)
    resolved_display_name = extractor.get_model_display_name(resolved_dataset_id)

    tmdl = extractor.get_model_definition(resolved_dataset_id)
    if isinstance(tmdl, dict):
        model_obj = tmdl.get("model")
        if isinstance(model_obj, dict):
            model_name = str(model_obj.get("name") or "").strip()
            if not model_name or model_name.lower() in {"model", "fabricmodel"}:
                if resolved_display_name:
                    model_obj["name"] = resolved_display_name

    row_counts = extractor.get_table_row_counts(resolved_dataset_id)
    internal_sml_model = translate_tmsl_to_internal_sml(
        tmdl,
        workspace_id=ws_id,
        dataset_id=resolved_dataset_id,
        row_counts=row_counts,
    ).model_dump()
    if isinstance(internal_sml_model, dict) and resolved_display_name:
        internal_sml_model["unique_name"] = resolved_display_name
        if not internal_sml_model.get("label"):
            internal_sml_model["label"] = resolved_display_name

    source_format = from_fabric_tmdl(
        project_id=context.project_id,
        run_id=context.run_id,
        tmdl=tmdl,
        workspace_id=ws_id,
        dataset_id=resolved_dataset_id,
        row_counts=row_counts,
        internal_sml_model=internal_sml_model,
    )
    
    # Ensure display name is explicitly resolved and stored for naming resolution
    source_format.dataset_name = resolved_display_name

    self._record_step(4, StepStatus.SUCCESS, f"Extracted TMDL definition for '{source_format.dataset_name}'")

    return source_format
