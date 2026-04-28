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

def _convert_to_databricks_target(self, context: RunContext) -> None:
    """Generate a Databricks metric-view YAML artifact."""
    import yaml
    from semabridge.connectors.databricks_publisher import DatabricksPublisher

    publisher = DatabricksPublisher(context.config.databricks, behavior=context.behavior)
    # Apply the same heuristic transforms that publish() applies so the
    # debug YAML artifact faithfully reflects what will be deployed.
    publisher._apply_model_config_overrides(context.sml_model)
    statements, created, skipped, skipped_details = publisher.generate_measure_view_statements(
        context.sml_model,
        view_type_override="metric_view",
    )

    output_dir = self._model_output_dir("databricks", model_name=context.project_id)
    yaml_path = output_dir / "databricks_metric_views.yaml"

    metric_view_defs: list[dict[str, Any]] = []
    for stmt in statements:
        view_match = re.search(r"CREATE OR REPLACE VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)", stmt)
        yaml_match = re.search(r"WITH METRICS LANGUAGE YAML AS \$\$\n(.*)\n\$\$", stmt, re.S)
        metric_view_defs.append({
            "view_name": view_match.group(1) if view_match else "unknown",
            "yaml_definition": yaml_match.group(1) if yaml_match else stmt,
        })

    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "object_type": "metric_view",
                "created": created,
                "skipped": skipped,
                "skipped_details": skipped_details,
                "views": metric_view_defs,
            },
            f,
            sort_keys=False,
            allow_unicode=False,
        )

    context.target_artifact_path = str(yaml_path)
