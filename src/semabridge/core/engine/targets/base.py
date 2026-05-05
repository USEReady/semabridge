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

def _step8_convert_to_target(self, context: RunContext) -> None:
    """
    Step 8: Convert to target format.

    - Convert SML into Target Format using target rule pack
    - Validate generated output before deployment
    """
    self._current_step = 8
    logger.info(f"Step 8: Converting to {context.target_type} target format")

    try:
        if context.target_type == "fabric":
            self._convert_to_fabric_target(context)
        elif context.target_type == "snowflake":
            self._convert_to_snowflake_target(context)
        elif context.target_type == "databricks":
            self._convert_to_databricks_target(context)

        self._record_step(8, StepStatus.SUCCESS, f"Target format generated")

    except Exception as e:
        self._record_step(8, StepStatus.FAILED, str(e))
        raise ConversionError(f"Target conversion failed: {e}") from e
