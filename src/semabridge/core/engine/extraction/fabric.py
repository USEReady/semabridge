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

def _extract_fabric(
    self,
    context: RunContext,
    dataset_id: Optional[str],
    workspace_id: Optional[str],
) -> SourceFormat:
    """Extract from Fabric."""
    from semabridge.connectors.fabric_extractor import FabricExtractor

    config = context.config
    fabric_cfg = config.fabric
    ws_id = workspace_id or fabric_cfg.workspace_id
    stored_workspace_id: str = ""

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
            tmsl = json.load(f)

        # Accept both full TMSL and flattened model payloads.
        if isinstance(tmsl, dict) and "model" not in tmsl and "tables" in tmsl:
            tmsl = {"model": tmsl}

        resolved_dataset_id = dataset_id or context.project_id
        row_counts: dict[str, int] = {}

        source_format = from_fabric_tmsl(
            project_id=context.project_id,
            run_id=context.run_id,
            tmsl=tmsl,
            workspace_id=ws_id,
            dataset_id=resolved_dataset_id,
            row_counts=row_counts,
        )

        table_count = len(tmsl.get("model", {}).get("tables", []))
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
    except Exception as exc:
        logger.warning("_extract_fabric: stored Fabric workspace lookup failed: %s", exc)

    # One resolver for every Fabric stage (identity > run account token >
    # interactive store > extractor's own configured auth). Resolved here, at
    # Stage 4, so a long-queued scheduled run still gets a fresh identity token.
    from semabridge.sources.base import SourceAuthError
    from semabridge.sources.fabric.credentials import apply_to_extractor, credentials_for_context

    try:
        fabric_creds = credentials_for_context(context)
    except SourceAuthError as exc:
        raise ExtractionError(str(exc)) from exc
    logger.debug("_extract_fabric: Fabric credentials origin=%s", fabric_creds.origin)

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


    extractor = apply_to_extractor(FabricExtractor(fabric_cfg), fabric_creds)

    resolved_dataset_id = extractor.resolve_model_id(dataset_id)

    tmsl = extractor.get_model_definition(resolved_dataset_id)
    row_counts = extractor.get_table_row_counts(resolved_dataset_id)

    source_format = from_fabric_tmsl(
        project_id=context.project_id,
        run_id=context.run_id,
        tmsl=tmsl,
        workspace_id=ws_id,
        dataset_id=resolved_dataset_id,
        row_counts=row_counts,
    )
    
    # Ensure display name is explicitly resolved and stored for naming resolution
    source_format.dataset_name = extractor.get_model_display_name(resolved_dataset_id)

    self._record_step(4, StepStatus.SUCCESS, f"Extracted TMSL definition for '{source_format.dataset_name}'")

    return source_format
