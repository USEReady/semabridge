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

def _extract_pbix(
    self,
    context: RunContext,
    pbix_path: Optional[str],
) -> SourceFormat:
    """Extract from a local .pbix file."""
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector

    if not pbix_path:
        source_cfg = getattr(context.config, "source", None)
        pbix_path = (
            str(getattr(source_cfg, "pbix_path", "") or "").strip()
            or str(getattr(source_cfg, "source_path", "") or "").strip()
            or str(getattr(source_cfg, "file_path", "") or "").strip()
        )

    if not pbix_path:
        raise ExtractionError(
            "pbix_path is required for PBIX source. "
            "Provide it directly or via source.pbix_path/source.source_path/source.file_path."
        )

    pbix_path = str(pbix_path).strip()
    if len(pbix_path) >= 2 and (
        (pbix_path[0] == '"' and pbix_path[-1] == '"')
        or (pbix_path[0] == "'" and pbix_path[-1] == "'")
    ):
        pbix_path = pbix_path[1:-1].strip()
    pbix_path = os.path.expandvars(os.path.expanduser(pbix_path))

    p = Path(pbix_path)
    if not p.exists():
        raise ExtractionError(f"PBIX file not found: {pbix_path}")

    connector = LocalPBIXConnector({"pbix_path": pbix_path})
    discovered = connector.discover()
    tmsl = connector.extract() if discovered.get("raw_tmsl") is None else discovered.get("raw_tmsl")

    if not tmsl:
        raise ExtractionError(
            f"Could not extract DataModelSchema from {pbix_path}"
        )

    logger.debug(
        "PBIX -> TMSL transition complete for %s (%s tables)",
        pbix_path,
        len(tmsl.get("model", {}).get("tables", [])),
    )
    try:
        model_obj = tmsl.get("model", {})
        table_defs = model_obj.get("tables", []) or []
        table_names = [str(t.get("name", "")).strip() or "<unnamed>" for t in table_defs]
        measure_count = sum(len((t.get("measures", []) or [])) for t in table_defs if isinstance(t, dict))
        logger.info(
            "PBIX extraction debug: model=%s, tables=%s, measures=%s, table_names=%s",
            model_obj.get("name", "<unnamed-model>"),
            len(table_defs),
            measure_count,
            table_names,
        )
    except Exception as exc:
        logger.debug("PBIX extraction debug summary skipped: %s", exc)

    source_format = from_pbix_tmsl(
        project_id=context.project_id,
        run_id=context.run_id,
        tmsl=tmsl,
        pbix_path=pbix_path,
        field_aliases=discovered.get("field_aliases", []),
        unresolved_report_field_references=discovered.get("unresolved_report_field_references", []),
        ambiguous_report_aliases=discovered.get("ambiguous_report_aliases", []),
    )

    table_count = len(tmsl.get("model", {}).get("tables", []))
    self._record_step(
        4, StepStatus.SUCCESS,
        f"Extracted {table_count} tables from {p.name}"
    )

    return source_format
