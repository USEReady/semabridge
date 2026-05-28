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

def _convert_fabric_to_sml(
    self,
    context: RunContext,
    workspace_id: Optional[str],
    dataset_id: Optional[str],
) -> SMLModel:
    """Convert Fabric TMSL to SML via the mandatory OSI intermediate layer.

    Flow: TMSL → OSIModel (TMSLToOSIConverter) → SMLModel (OSIToSMLConverter)
    The OSIModel is stored on context.osi_model for auditing / step-7 persistence.
    """
    from semabridge.converter.tmdl_to_osi import TMDLToOSIConverter
    from semabridge.converter.osi_to_sml import OSIToSMLConverter

    sf = context.source_format
    ws_id = workspace_id or sf.workspace_id
    ds_id = dataset_id or sf.dataset_id

    # Phase 1: TMDL → OSI
    source_data = {
        "tmdl_files": sf.tmdl_files,
        "workspace_id": ws_id,
        "dataset_id": ds_id,
        "display_name": sf.dataset_name or None,
        "project_id": context.project_id,
    }
    osi_model = TMDLToOSIConverter().to_osi(source_data)
    context.osi_model = osi_model  # Store on context for step-7 persistence
    logger.debug(
        f"OSI intermediate: {len(osi_model.datasets)} datasets, "
        f"{len(osi_model.metrics)} metrics, {len(osi_model.relationships)} relationships"
    )

    # Detailed runtime diagnostics before conversion
    logger.debug(
        "OSI model state - "
        f"metrics={type(osi_model.metrics)} "
        f"tables={type(osi_model.tables)} "
        f"relationships={type(osi_model.relationships)}"
    )
    
    if osi_model.metrics is None:
        logger.error("OSI model metrics is None (expected empty list at minimum)")
    if osi_model.datasets is None:
        logger.error("OSI model datasets is None (expected empty list at minimum)")
    if osi_model.relationships is None:
        logger.error("OSI model relationships is None (expected empty list at minimum)")
    if osi_model.dimensions is None:
        logger.error("OSI model dimensions is None (expected empty list at minimum)")

    # Phase 2: OSI → SML
    sml_model = OSIToSMLConverter().from_osi(
        osi_model,
        row_counts=sf.row_counts,
    )

    # Logging Improvements (accurate measure & fact counts)
    fact_tables = [ds.unique_name for ds in sml_model.datasets if ds.is_fact]
    fact_tables_str = f" ({', '.join(fact_tables)})" if fact_tables else ""
    logger.info(f"Detected {len(fact_tables)} fact tables{fact_tables_str}")
    
    tables_with_measures = {m.dataset for m in sml_model.metrics if m.dataset}
    logger.info(f"Detected {len(sml_model.metrics)} measures across {len(tables_with_measures)} tables")
    
    logger.info(f"Stage 6: Convert to Canonical SML - {sml_model.dataset_count} datasets, {sml_model.metric_count} metrics (via OSI)")

    # Allow the project config's model_name / project_name to override the
    # SML model's unique_name.  This lets users control the Snowflake view
    # name without renaming the Fabric dataset.
    # Read from context.semantic_view_name_override — never from
    # context.config.model.name, which is a shared singleton.
    override_name = context.semantic_view_name_override
    if override_name and str(override_name).strip():
        sml_model.unique_name = str(override_name).strip()
        sml_model.label = sml_model.label or sml_model.unique_name
        logger.info(
            "Model unique_name overridden by project config model_name: '%s'",
            sml_model.unique_name,
        )

    self._record_step(
        6, StepStatus.SUCCESS,
        f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics (via OSI)"
    )

    return sml_model
