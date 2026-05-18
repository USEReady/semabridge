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

def _step4_extract(
    self,
    context: RunContext,
    dataset_id: Optional[str],
    workspace_id: Optional[str],
    pbix_path: Optional[str] = None,
) -> SourceFormat:
    """
    Step 4: Extract from source.

    - Connect to configured source system
    - Extract semantic model
    - Normalize into Source Format artifact
    """
    self._current_step = 4
    logger.info(f"Step 4: Extracting from {context.source_type}")

    try:
        if context.source_type == "snowflake":
            return self._extract_snowflake(context, dataset_id)
        elif context.source_type == "fabric":
            return self._extract_fabric(context, dataset_id, workspace_id)
        elif context.source_type == "pbix":
            return self._extract_pbix(context, pbix_path)
        else:
            raise ExtractionError(f"Unknown source type: {context.source_type}")

    except Exception as e:
        self._record_step(4, StepStatus.FAILED, str(e))
        raise ExtractionError(f"Extraction failed: {e}") from e

def _step6a_extract_target_for_upsert(self, context: RunContext, target: str) -> None:
    """
    Step 6a: Extract existing target model for UPSERT merge.
    
    For UPSERT sync_mode with Snowflake target:
    - Query existing Semantic View DDL from Snowflake
    - Parse it back to SMLModel for granular merge
    
    Stores result in context.target_sml_model
    """
    logger.info("Step 6a: Extracting target model for UPSERT")
    
    try:
        if target.lower() != "snowflake":
            logger.info(f"Target is {target}, skipping target extraction (UPSERT supported for Snowflake)")
            return
        
        # Extract existing Snowflake Semantic View
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
        from semabridge.utils.name_translator import get_target_deployment_name
        
        emitter = SnowflakeEmitter(context.config.snowflake, behavior=context.behavior)
        
        # Resolve the target semantic view name using the same deployment naming path.
        view_name_raw = (
            context.semantic_view_name_override
            or getattr(getattr(context, "source_format", None), "semantic_view_name", None)
            or getattr(getattr(context, "source_format", None), "dataset_name", None)
            or context.project_id
        )
        view_name = get_target_deployment_name(view_name_raw, "snowflake")
        if not view_name:
            logger.warning("No semantic_view_name in context, cannot extract target")
            context.target_sml_model = None
            return
        
        # Query Snowflake for existing view
        try:
            existing_view = emitter.get_semantic_view(view_name)
            if existing_view:
                logger.info(f"Found existing Snowflake view: {view_name}, converting to SML")
                # Convert the Snowflake view back to SMLModel for merge
                osi_model = SemanticViewToOSIConverter().to_osi(
                    {
                        "ddl": existing_view,
                        "view_name": view_name,
                    }
                )
                target_sml = OSIToSMLConverter().from_osi(osi_model)
                context.target_sml_model = target_sml
                logger.info(f"Extracted target SML: {len(target_sml.datasets)} datasets, {len(target_sml.metrics)} metrics")
            else:
                logger.info(f"No existing view found: {view_name}, treating as new deployment")
                context.target_sml_model = None
        except Exception as e:
            logger.warning(f"Could not extract existing view {view_name}: {e}, will use COPY behavior")
            context.target_sml_model = None
            
    except Exception as e:
        logger.warning(f"Step 6a target extraction failed: {e}, continuing with COPY behavior")
        context.target_sml_model = None
