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

    # The deployed Snowflake semantic view name must come from THIS file's own
    # filename — never from context.project_id. project_id is computed once
    # per sync request and shared, unchanged, across every job in a multi-PBIX
    # batch (see _build_sync_jobs/_run_single_job in sync_execution_service.py),
    # so using it here would collapse every file in the batch onto the same
    # unique_name and therefore the same "CREATE OR REPLACE SEMANTIC VIEW"
    # name — each job's DDL would silently overwrite the previous job's view
    # instead of producing N independent views.
    #
    # Sanitized with the same IdentifierSanitizer.sanitize_table_name() that
    # get_pbix_deployment_view_name() (name_translator.py) uses for the
    # pre-deploy collision check, so the collision check and the name actually
    # baked into the DDL agree on the same computation — not two disconnected
    # ones that can disagree about whether a collision exists.
    pbix_source_path = getattr(sf, "pbix_path", None)
    file_display_name = None
    if pbix_source_path:
        from semabridge.utils.identifiers import IdentifierSanitizer

        file_display_name = IdentifierSanitizer().sanitize_table_name(Path(pbix_source_path).stem)

    # Phase 1: TMSL → OSI
    source_data = {
        "tmsl": sf.tmsl_definition,
        "workspace_id": ws_id,
        "dataset_id": ds_id,
        "project_id": context.project_id,
        # Consumed by TMSLToOSIConverter.to_osi() as the top-priority source
        # for both OSIModel.unique_name (drives the deployed view name) and
        # OSIModel.label — see tmsl_to_osi.py's `resolved_unique_name`.
        "display_name": file_display_name,
        "field_aliases": getattr(sf, "field_aliases", []),
        # Stamped onto every OSIColumn/OSIMetric this conversion produces (see
        # TMSLToOSIConverter.to_osi) so a multi-PBIX project's per-file dry-run
        # results are never ambiguous about which .pbix file they came from.
        # getattr, not direct access: context.source_format is a real
        # SourceFormat in production (always has pbix_path, defaulting to
        # None), but some tests substitute a lighter-weight stand-in that
        # doesn't set every SourceFormat attribute.
        "source_file": getattr(sf, "pbix_path", None),
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
