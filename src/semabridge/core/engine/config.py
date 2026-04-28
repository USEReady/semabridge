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

def _apply_mapping_overrides_from_config(
    sml_model: SMLModel,
    config_path: Path,
    config_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Apply user-edited mapping overrides from config to SML names before deploy."""
    parsed: Dict[str, Any] = {}
    if isinstance(config_payload, dict) and isinstance(config_payload.get("mappings_overrides"), list):
        parsed = config_payload
    else:
        try:
            if not config_path.exists():
                return
            parsed = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(parsed, dict):
                return
        except Exception as exc:
            logger.debug("Skipping mapping override load from %s: %s", config_path, exc)
            return

    raw_overrides = parsed.get("mappings_overrides")
    if not isinstance(raw_overrides, list):
        return

    overrides: Dict[str, str] = {}
    for row in raw_overrides:
        if not isinstance(row, dict):
            continue
        source_path = str(row.get("source_path") or "").strip()
        target_name = str(row.get("target_name") or "").strip()
        if source_path and target_name:
            overrides[source_path] = target_name

    if not overrides:
        return

    renamed_columns = 0
    renamed_metrics = 0

    dataset_by_name: Dict[str, Any] = {}
    for dataset in sml_model.datasets:
        dataset_by_name[str(dataset.unique_name)] = dataset

    for source_path, target_name in overrides.items():
        if source_path.startswith("metrics."):
            metric_name = source_path[len("metrics."):].strip()
            metric_lookup = metric_name.lower()
            for metric in sml_model.metrics:
                metric_unique_name = str(getattr(metric, "unique_name", "")).strip()
                metric_label = str(getattr(metric, "label", "")).strip()
                if metric_unique_name.lower() == metric_lookup or metric_label.lower() == metric_lookup:
                    if str(metric.unique_name) != target_name:
                        metric.unique_name = target_name
                        metric.label = target_name
                        renamed_metrics += 1
                    break
            continue

        if source_path.startswith("datasets.") and ".columns." in source_path:
            prefix = "datasets."
            col_sep = ".columns."
            dataset_name = source_path[len(prefix): source_path.index(col_sep)]
            column_name = source_path[source_path.index(col_sep) + len(col_sep):]
            dataset = dataset_by_name.get(dataset_name)
            if not dataset:
                continue
            for column in dataset.columns:
                if str(column.unique_name) == column_name:
                    if str(column.unique_name) != target_name:
                        column.unique_name = target_name
                        column.label = target_name
                        renamed_columns += 1
                    break

    if renamed_columns or renamed_metrics:
        logger.info(
            "Applied mapping overrides from config: columns=%s metrics=%s",
            renamed_columns,
            renamed_metrics,
        )

def _step1_load_config(
    self,
    config_path: Optional[Path],
    source: str,
    target: Optional[str],
    config_dict: Optional[Dict[str, Any]] = None,
) -> Settings:
    """
    Step 1: Load and validate configuration.

    - Load YAML/env configuration
    - Validate required keys
    - Validate supported connector types
    - Fail fast if validation fails
    """
    self._current_step = 1
    logger.info("Step 1: Loading and validating configuration")

    try:
        # Load settings (from .env by default)
        config = get_settings()

        # Merge in-memory config or file config
        raw_config = config_dict or {}
        if not raw_config and config_path and Path(config_path).exists():
            try:
                raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
            except Exception as raw_exc:
                logger.warning("Could not parse config at %s for project-scoped settings: %s", config_path, raw_exc)
                raw_config = {}

            source_cfg = raw_config.get("source") if isinstance(raw_config.get("source"), dict) else {}
            target_cfg: dict[str, Any] = {}
            raw_target = raw_config.get("target")
            if isinstance(raw_target, dict):
                target_cfg = raw_target
            elif not raw_target:
                targets_cfg = raw_config.get("targets")
                if isinstance(targets_cfg, list) and targets_cfg and isinstance(targets_cfg[0], dict):
                    target_cfg = targets_cfg[0]

            if source_cfg:
                object.__setattr__(config, "source", SimpleNamespace(**source_cfg))
                if source == "fabric":
                    source_workspace_id = str(source_cfg.get("workspace_id") or "").strip()
                    if source_workspace_id:
                        config.fabric.workspace_id = source_workspace_id
            if target_cfg:
                object.__setattr__(config, "target", SimpleNamespace(**target_cfg))
                if target == "fabric":
                    target_workspace_id = str(target_cfg.get("workspace_id") or "").strip()
                    if target_workspace_id:
                        config.fabric.workspace_id = target_workspace_id

        # Validate connector types
        if source not in self.SUPPORTED_SOURCES:
            raise ConfigValidationError(
                f"Unsupported source connector: '{source}'. "
                f"Supported: {self.SUPPORTED_SOURCES}"
            )

        if target not in self.SUPPORTED_TARGETS:
            raise ConfigValidationError(
                f"Unsupported target connector: '{target}'. "
                f"Supported: {self.SUPPORTED_TARGETS - {None}}"
            )

        self._record_step(1, StepStatus.SUCCESS, "Configuration validated")
        return config

    except Exception as e:
        self._record_step(1, StepStatus.FAILED, str(e))
        raise ConfigValidationError(f"Configuration validation failed: {e}") from e

def _step2_init_identifiers(
    self,
    config: Settings,
    source: str,
    target: Optional[str],
    project_name: Optional[str],
    dataset_id: Optional[str],
    config_path: Optional[Path] = None,
) -> RunContext:
    """
    Step 2: Initialize identifiers.

    - Generate unique project_id
    - Generate unique run_id
    - Create at the very start and propagate through all stages
    """
    self._current_step = 2
    logger.info("Step 2: Initializing identifiers")

    # Determine project_id
    if dataset_id:
        # For Fabric source, use dataset_id as project_id
        project_id = dataset_id
    elif project_name:
        project_id = project_name
    else:
        project_id = config.model.name

    # Behavior loading logic (matches ExecutionConfig)
    behavior = ConnectorBehavior()
    if hasattr(config, "behavior"):
        behavior = config.behavior
    elif config_path:
        # config_path provided — look for behavior.yaml alongside the config
        # file, or fall back to the CWD behavior.yaml.
        candidate_dirs = [config_path.parent, Path("config"), Path(".")]
        for d in candidate_dirs:
            behavior_candidate = d / "behavior.yaml"
            if behavior_candidate.exists():
                try:
                    behavior = ConnectorBehavior.from_yaml(behavior_candidate)
                except Exception as _be:
                    logger.warning(
                        f"behavior.yaml at '{behavior_candidate}' could not be "
                        f"parsed, using defaults: {_be}"
                    )
                break
    else:
        # Last resort: try behavior.yaml in the current working directory.
        # This covers the API codepath where config is a Settings instance
        # that predates the .behavior property.
        cwd_behavior = get_project_file_path("behavior.yaml")
        if cwd_behavior.exists():
            try:
                behavior = ConnectorBehavior.from_yaml(cwd_behavior)
            except Exception as _be:
                logger.warning(
                    f"behavior.yaml could not be parsed, using defaults: {_be}"
                )

    # Generate unique run_id
    run_id = str(uuid.uuid4())

    context = RunContext(
        project_id=project_id,
        run_id=run_id,
        config=config,
        start_time=time.time(),
        source_type=source,
        target_type=target,
        behavior=behavior,
    )

    # Register the project + run row in the DB immediately.
    # source_artifacts (step 7) has a FK -> runs.run_id -> projects.project_id,
    # so both parent rows must exist before any artifact INSERT happens.
    try:
        workspace_id = ""
        if source == "fabric":
            try:
                workspace_id = config.fabric.workspace_id
            except Exception:
                pass
        elif source in ("pbix", "local"):
            workspace_id = "local"

        self.db_manager.ensure_project(
            project_id=project_id,
            name=project_name or project_id,
            workspace_id=workspace_id,
            adapter=source,
        )
        self.db_manager.record_run_start(
            run_id=run_id,
            project_id=project_id,
            source_type=source,
            target_type=target,
        )
        logger.debug("Run %s registered in DB", run_id[:8])
    except Exception as _reg_err:
        # Non-fatal here — step 7 will also call ensure_project.
        # Log at warning so it is visible but does not abort the pipeline.
        logger.warning(
            "Could not pre-register run %s in DB: %s", run_id[:8], _reg_err
        )

    logger.info(f"Identifiers: project_id={project_id}, run_id={run_id}")
    self._record_step(2, StepStatus.SUCCESS, f"run_id={run_id[:8]}...")

    return context
