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

def _step9_deploy(self, context: RunContext) -> None:
    """   
    Step 9: Deploy to target.
    - Emit/deploy Target Format to target system
    - Handle partial deployment failures explicitly
    - On Snowflake failure: attempt metadata-only update to reflect attempt time
    """
    self._current_step = 9
    logger.info(f"Step 9: Deploying to {context.target_type}")

    try:
        if context.target_type == "fabric":
            self._deploy_to_fabric(context)
        elif context.target_type == "snowflake":
            self._deploy_to_snowflake(context)
        elif context.target_type == "databricks":
            self._deploy_to_databricks(context)

        self._record_step(9, StepStatus.SUCCESS, "Deployment complete")

    # ✅ HANDLE MISSING TABLE AS WARNING (NOT FAILURE)
    except MissingSourceTableWarning as w:
        logger.warning(f"Missing underlying table during deployment: {w}")

        # Record step as success but with warning message
        self._record_step(
            9,
            StepStatus.SUCCESS,
            f"Deployment completed with warning: {str(w)}"
        )
    # 🔥 DO NOT re-raise

    except Exception as e:
        # NEW: On Snowflake deployment failure, attempt to update view metadata
        # This is a non-destructive operation that updates only the timestamp
        # to reflect when the last sync attempt occurred, keeping the old view intact
        if context.target_type == "snowflake":
            try:
                sf_cfg = context.config.snowflake
                error_msg = str(e)
                self._update_snowflake_view_metadata_on_failure(context, sf_cfg, error_msg)
            except Exception as metadata_update_error:
                logger.warning(
                    f"Could not update Snowflake view metadata on failure (non-fatal): {metadata_update_error}"
                )
                # Continue with the original error handling regardless
        
        self._record_step(9, StepStatus.FAILED, str(e))
        raise DeploymentError(f"Deployment failed: {e}") from e
