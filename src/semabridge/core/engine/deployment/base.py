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
        from semabridge.core.targets import get_target_adapter

        get_target_adapter(context.target_type).deploy(self, context)

        self._record_step(
            9,
            StepStatus.SUCCESS,
            getattr(context, "deploy_step_message", None) or "Deployment complete",
        )

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
        # A failed deploy no longer touches the live view at all (the former
        # silent "bump LAST_ALTERED" ALTER on failure is gone: it changed a
        # live object with no audit trail). The attempt is recorded instead,
        # in the run's deploy journal (run_deploy_events).
        if context.target_type == "snowflake":
            try:
                from semabridge.connectors.staged_deploy import persist_deploy_events

                persist_deploy_events(
                    [{
                        "action": "deploy_failed",
                        "object_type": "SEMANTIC VIEW",
                        "object_name": str(getattr(context, "deployed_view_name", None) or "") or None,
                        "status": "failed",
                        "detail": str(e)[:2000],
                    }],
                    run_id=getattr(context, "run_id", None),
                    project_id=getattr(context, "project_id", None),
                    model=getattr(context, "project_name", None),
                )
            except Exception as audit_error:
                logger.warning(f"Could not record the failed deploy attempt (non-fatal): {audit_error}")

        self._record_step(9, StepStatus.FAILED, str(e))
        raise DeploymentError(f"Deployment failed: {e}") from e
