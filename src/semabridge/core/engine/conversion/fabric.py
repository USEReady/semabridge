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

def _convert_fabric_to_sml(
    self,
    context: RunContext,
    workspace_id: Optional[str],
    dataset_id: Optional[str],
) -> SMLModel:
    """Convert Fabric TMDL to SML via the mandatory OSI intermediate layer.

    Flow: TMDL → OSIModel (TMDLToOSIConverter) → SMLModel (OSIToSMLConverter)
    The OSIModel is stored on context.osi_model for auditing / step-7 persistence.
    """
    from semabridge.connectors.metadata_connectors import (
        TmdlConnector,
    )

    sf = context.source_format
    ws_id = workspace_id or sf.workspace_id
    ds_id = dataset_id or sf.dataset_id
    conversion_path = "fabric_boundary_sml"

    if sf.internal_sml_model:
        sml_model = SMLModel.model_validate(sf.internal_sml_model)
        sf.metadata_format = "tmdl"
        logger.debug(
            "Using pre-translated internal SML model from Fabric extraction boundary"
        )
    else:
        source_data = {
            "tmdl": sf.tmdl_definition,
            "workspace_id": ws_id,
            "dataset_id": ds_id,
            "display_name": sf.dataset_name or None,
        }
        primary_connector = TmdlConnector()
        conversion_path = primary_connector.source_format
        osi_model, sml_model = primary_connector.parse_to_sml(source_data, row_counts=sf.row_counts)
        context.osi_model = osi_model  # Store on context for step-7 persistence

        sf.metadata_format = "tmdl"
        logger.debug(
            "OSI intermediate (%s): %d datasets, %d metrics, %d relationships",
            primary_connector.source_format,
            len(osi_model.datasets),
            len(osi_model.metrics),
            len(osi_model.relationships),
        )

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
        f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics (via {conversion_path})"
    )

    return sml_model
