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

def _convert_pbix_to_sml(self, context: RunContext) -> SMLModel:
    """Convert PBIX DataModelSchema (TMSL) to SML via the mandatory OSI intermediate layer.

    The DataModelSchema inside a .pbix archive is structurally identical to the
    Fabric TMSL model definition, so we reuse TMSLToOSIConverter for Phase 1.
    Flow: TMSL → OSIModel → SMLModel
    """
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.converter.osi_to_sml import OSIToSMLConverter

    sf = context.source_format
    # PBIX has no workspace/dataset IDs — use sentinel values
    ws_id = "local"
    ds_id = context.project_id

    # Phase 1: TMSL → OSI
    source_data = {
        "tmsl": sf.tmsl_definition,
        "workspace_id": ws_id,
        "dataset_id": ds_id,
        "project_id": context.project_id,
    }
    osi_model = TMSLToOSIConverter().to_osi(source_data)
    context.osi_model = osi_model
    logger.debug(
        f"PBIX OSI intermediate: {len(osi_model.datasets)} datasets, "
        f"{len(osi_model.metrics)} metrics"
    )
    logger.info(
        "PBIX OSI datasets: %s",
        [ds.unique_name for ds in osi_model.datasets],
    )
    logger.info(
        "PBIX OSI metrics: %s",
        [m.unique_name for m in osi_model.metrics],
    )

    # Phase 2: OSI → SML
    sml_model = OSIToSMLConverter().from_osi(
        osi_model,
        row_counts=sf.row_counts,
    )
    logger.info(
        "PBIX SML datasets: %s",
        [ds.unique_name for ds in sml_model.datasets],
    )
    logger.info(
        "PBIX SML metrics: %s",
        [m.unique_name for m in sml_model.metrics],
    )

    self._record_step(
        6, StepStatus.SUCCESS,
        f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics (via OSI)",
    )

    return sml_model
