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
    from semabridge.utils.identifiers import clean_pbix_model_name

    sf = context.source_format
    ws_id = "local"
    display_name = (
        clean_pbix_model_name(sf.pbix_path)
        if sf and getattr(sf, "pbix_path", None)
        else getattr(sf, "dataset_name", None)
    )
    ds_id = display_name or context.project_id

    # Phase 1: TMSL → OSI
    source_data = {
        "tmsl": sf.tmsl_definition if sf else None,
        "workspace_id": ws_id,
        "dataset_id": ds_id,
        "display_name": display_name or ds_id,
        "project_id": context.project_id,
        "field_aliases": getattr(sf, "field_aliases", []),
    }
    osi_model = TMSLToOSIConverter(drop_ledger=context.drop_ledger).to_osi(source_data)
    context.osi_model = osi_model

    # Apply metric-name mapping overrides (e.g. "Total Units" -> "TOTAL_UNITS")
    # to the OSI model now, before Phase 2 translates any DAX. A sibling
    # metric's raw DAX referencing the renamed measure by its old bracket
    # name (e.g. TOTALYTD([Total Units], ...)) only gets rewritten to the
    # new name by this same call — running it after Phase 2 would mean
    # translation already gave up on the stale bracket text, permanently
    # (there's no later retry). See _apply_mapping_overrides_from_config's
    # own comment for why the rewrite matters.
    if context.config_path is not None:
        self._apply_mapping_overrides_from_config(
            osi_model, Path(context.config_path), config_payload=context.config_payload
        )
        context.mapping_overrides_applied = True

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
