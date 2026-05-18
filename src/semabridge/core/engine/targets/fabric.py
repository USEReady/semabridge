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

def _convert_to_fabric_target(self, context: RunContext) -> None:
    """Generate Fabric TMDL."""
    from semabridge.connectors.tmsl_generator import TMDLGenerator

    config = context.config

    generator = TMDLGenerator(
        context.sml_model,
        snowflake_server=config.snowflake.account,
        snowflake_warehouse=config.snowflake.warehouse,
        snowflake_database=config.snowflake.database,
        snowflake_schema=config.snowflake.schema_name,
    )

    output_dir = self._model_output_dir("fabric", model_name=context.project_id)

    bim_path = output_dir / "model.bim"
    generator.save(bim_path)
    context.target_artifact_path = str(bim_path)
