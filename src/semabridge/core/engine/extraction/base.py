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
