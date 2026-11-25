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

    # The project's workspace only: a GUID as-is, or an exact workspace NAME
    # resolved with the project's credentials. Never the stored credential's
    # or the .env workspace (no alias fallback).
    from semabridge.sources.base import SourceError
    from semabridge.sources.fabric.workspaces import resolve_workspace_strict

    requested_ws = str(ws_id or "").strip()
    if not requested_ws:
        raise ExtractionError("No Fabric workspace for this model: set source.workspace_id (no default workspace is used).")
    fabric_cfg.workspace_id = requested_ws
    extractor = apply_to_extractor(FabricExtractor(fabric_cfg), fabric_creds)
    try:
        ws_id = resolve_workspace_strict(extractor, requested_ws)
    except SourceError as exc:
        raise ExtractionError(str(exc)) from exc
    fabric_cfg.workspace_id = ws_id
    extractor.config.workspace_id = ws_id

    # Exact match only: a GUID as-is, or exactly one model with that display
    # name. Two models sharing a name is an error naming their ids, never
    # "the first one".
    from semabridge.sources.base import SourceError

    try:
        resolved_dataset_id = extractor._resolve_model_id_exact(dataset_id)
    except SourceError as exc:
        raise ExtractionError(str(exc)) from exc

    from semabridge.sources.base import ModelIdentity, get_source_adapter
    adapter = get_source_adapter("fabric")
    identity = ModelIdentity(
        source_type="fabric",
        model_id=resolved_dataset_id,
        display_name=extractor.get_model_display_name(resolved_dataset_id) or resolved_dataset_id,
        workspace_id=ws_id,
    )
    tmsl = adapter.extract_model(identity, extractor=extractor)
    if context.behavior.features.fabric_real_row_counts:
        # COUNTROWS counts are kept for row-count parity only and never reach
        # fact/dimension classification (facts come from relationships), so
        # the classifier gets no row counts at all on this path.
        from semabridge.sources.fabric.stats import fabric_row_counts

        stats = fabric_row_counts(extractor, resolved_dataset_id, tmsl)
        context.source_row_counts = stats.counts
        context.source_row_count_errors = stats.errors
        row_counts = {}
    else:
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

    if context.behavior.features.fabric_report_layer:
        # Report-layer parity with .pbix: captions of the reports bound to this
        # model become field aliases (synonyms); unresolved / ambiguous report
        # fields are reported like PBIX. A report this identity cannot read is a
        # run warning, not a failure.
        try:
            layer = adapter.extract_report_layer(identity, extractor=extractor, tmsl=tmsl)
        except SourceError as exc:
            raise ExtractionError(f"Fabric report layer failed: {exc}") from exc
        source_format.field_aliases = layer.field_aliases
        source_format.unresolved_report_field_references = layer.unresolved
        source_format.ambiguous_report_aliases = layer.ambiguous
        context.source_warnings.extend(layer.warnings)

    self._record_step(4, StepStatus.SUCCESS, f"Extracted TMSL definition for '{source_format.dataset_name}'")

    return source_format
