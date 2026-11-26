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
    NoSourceTablesError,
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
        from semabridge.sources.base import get_source_adapter
        adapter = get_source_adapter(context.source_type)
        if adapter.source_type == "fabric":
            return self._extract_fabric(context, dataset_id, workspace_id)
        elif adapter.source_type == "pbix":
            return self._extract_pbix(context, pbix_path)
        elif adapter.source_type == "pbi_desktop":
            return self._extract_pbi_desktop(context, dataset_id, pbix_path)
        else:
            raise ExtractionError(f"Unknown source type: {context.source_type}")

    except NoSourceTablesError as e:
        # Keep the type: callers skip (not fail) a model with no tables.
        self._record_step(4, StepStatus.FAILED, str(e))
        raise
    except Exception as e:
        self._record_step(4, StepStatus.FAILED, str(e))
        raise ExtractionError(f"Extraction failed: {e}") from e


def _extract_pbi_desktop(
    self,
    context: RunContext,
    dataset_id: Optional[str],
    pbix_path: Optional[str] = None,
) -> SourceFormat:
    """Extract live semantic model from running Power BI Desktop instance."""
    from semabridge.core.source_format import from_pbix_tmsl
    from semabridge.sources.base import ModelIdentity, get_source_adapter

    adapter = get_source_adapter("pbi_desktop")
    model_ref = dataset_id or pbix_path or getattr(getattr(context.config, "source", None), "model", None)
    if not model_ref:
        raise ExtractionError("No target engine or model specified for Power BI Desktop extraction.")

    saved_display_name = getattr(context, "resolved_pbix_display_name", None)
    if not saved_display_name and hasattr(context, "config"):
        cfg_source = getattr(context.config, "source", None)
        models = getattr(cfg_source, "models", None) if cfg_source else None
        if isinstance(models, list):
            for m in models:
                m_path = getattr(m, "file_path", None) if not isinstance(m, dict) else m.get("file_path")
                m_name = getattr(m, "display_name", None) if not isinstance(m, dict) else m.get("display_name")
                if m_path and (m_path == pbix_path or m_path == model_ref or not pbix_path):
                    if m_name:
                        saved_display_name = m_name
                        break
        if not saved_display_name:
            cfg_model = getattr(context.config, "model", None)
            if cfg_model and getattr(cfg_model, "name", None):
                saved_display_name = cfg_model.name

    file_name = None
    path_candidate = pbix_path or (model_ref if isinstance(model_ref, str) and (model_ref.lower().endswith(".pbix") or "\\" in model_ref or "/" in model_ref) else None)
    if path_candidate:
        from pathlib import Path
        file_name = Path(path_candidate).stem

    model_name = saved_display_name or file_name
    if not model_name:
        raise ExtractionError(
            f"Power BI Desktop extraction requires a display_name in the saved model configuration "
            f"or a valid file path to derive the model name from (got pbix_path={pbix_path!r}, model_ref={model_ref!r})."
        )
    ident = ModelIdentity(
        source_type="pbi_desktop",
        model_id=str(model_ref),
        display_name=str(model_name),
    )
    drop_ledger = getattr(context, "drop_ledger", None)
    tmsl = adapter.extract_model(ident, drop_ledger=drop_ledger)

    report_layer = None
    report_aliases = None
    unresolved_refs = None
    ambiguous_aliases = None
    if pbix_path:
        try:
            report_layer = adapter.extract_report_layer(ident, tmsl=tmsl)
            if report_layer:
                report_aliases = getattr(report_layer, "field_aliases", None)
                unresolved_refs = getattr(report_layer, "unresolved", None)
                ambiguous_aliases = getattr(report_layer, "ambiguous", None)
        except Exception as exc:
            logger.warning(f"Failed to extract report layer: {exc}")

    source_format = from_pbix_tmsl(
        project_id=context.project_id,
        run_id=context.run_id,
        tmsl=tmsl,
        pbix_path=str(pbix_path or ""),
        field_aliases=report_aliases,
        unresolved_report_field_references=unresolved_refs,
        ambiguous_report_aliases=ambiguous_aliases,
    )
    from semabridge.utils.identifiers import clean_pbix_model_name
    if hasattr(source_format, "dataset_name"):
        source_format.dataset_name = clean_pbix_model_name(str(model_name))
    context.resolved_pbix_display_name = str(model_name)

    if report_layer and hasattr(source_format, "report_layer"):
        source_format.report_layer = report_layer

    return source_format
