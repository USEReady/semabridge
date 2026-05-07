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

def _convert_to_snowflake_target(self, context: RunContext) -> None:
    """Generate Snowflake DDL."""
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    config = context.config
    emitter = SnowflakeEmitter(config.snowflake, behavior=context.behavior)

    if not context.sml_model:
        raise ConversionError("No SML model available for target conversion. Ensure Stage 6 completed successfully.")

    output_dir = self._model_output_dir("reverse", model_name=context.project_id)

    ddls = emitter.generate_ddls(context.sml_model)
    full_ddl = "\n\n".join(ddls)
    yaml_out = emitter.generate_cortex_yaml(context.sml_model)

    ddl_path = output_dir / "semantic_view.sql"
    yaml_path = output_dir / "cortex_analyst.yaml"

    with open(ddl_path, "w", encoding="utf-8") as f:
        f.write(full_ddl)
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(yaml_out)

    context.target_artifact_path = str(ddl_path)
