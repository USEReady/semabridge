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

def _step7_persist_artifacts(
    self,
    context: RunContext,
    tag: Optional[str],
) -> None:
    """
    Step 7: Persist artifacts.

    - Persist Source Format artifact
    - Persist Canonical SML artifact
    - Persist validation/conversion reports
    - Persist execution metadata (project_id, run_id, timestamps)
    """
    self._current_step = 7
    logger.info("Step 7: Persisting artifacts")

    try:
        config = context.config

        # Ensure project exists
        self.db_manager.ensure_project(
            project_id=context.project_id,
            name=context.sml_model.label if context.sml_model else context.project_id,
            workspace_id=(
                config.fabric.workspace_id if context.source_type == "fabric"
                else "local" if context.source_type == "pbix"
                else ""
            ),
            adapter=context.source_type,
        )

        # Commit SML to DuckDB
        sml_dict = context.sml_model.model_dump(mode='json') if context.sml_model else {}
        sync_mode = getattr(context, 'sync_mode', 'copy')

        committed, snapshot_id = self.db_manager.commit_model(
            project_id=context.project_id,
            sml_json=sml_dict,
            tag=tag,
            status="success",
            duration_ms=int((time.time() - context.start_time) * 1000),
            run_id=context.run_id,
            sync_mode=sync_mode,
        )

        context.sml_snapshot_id = snapshot_id

        # Persist OSI intermediate snapshot (for round-trip auditing)
        if context.osi_model:
            try:
                import json as _json
                osi_dict = context.osi_model.model_dump(mode="json")
                self.db_manager.persist_source_artifact(
                    run_id=context.run_id,
                    source_format=None,  # type: ignore[arg-type]
                    raw_json=osi_dict,
                    artifact_type="osi_intermediate",
                )
            except Exception as osi_err:
                logger.warning(f"OSI snapshot persist failed (non-fatal): {osi_err}")

        # Persist source artifact
        source_artifact_id = self.db_manager.persist_source_artifact(
            run_id=context.run_id,
            source_format=context.source_format,
        )
        context.source_artifact_id = source_artifact_id

        if committed:
            msg = f"Snapshot {snapshot_id[:8]}... committed"
        else:
            msg = "No changes detected"

        self._record_step(
            7, StepStatus.SUCCESS, msg,
            artifact_ids=[snapshot_id, source_artifact_id] if source_artifact_id else [snapshot_id]
        )

    except Exception as e:
        self._record_step(7, StepStatus.FAILED, str(e))
        raise PersistenceError(f"Artifact persistence failed: {e}") from e

def _step10_finalize(
    self,
    context: RunContext,
    status: RunStatus,
) -> RunSummary:
    """
    Step 10: Finalize run.

    - Record final run status (SUCCESS, FAILED, PARTIAL)
    - Produce structured run summary
    - Output to CLI
    """
    self._current_step = 10
    logger.info(f"Step 10: Finalizing run with status {status.value}")

    if not self._summary:
        self._summary = create_run_summary(
            project_id=context.project_id,
            run_id=context.run_id,
            source_type=context.source_type,
            target_type=context.target_type,
        )

    self._summary.status = status
    self._summary.source_artifact_id = context.source_artifact_id
    self._summary.sml_snapshot_id = context.sml_snapshot_id
    self._summary.target_artifact_path = context.target_artifact_path
    self._summary.routing_summary = context.routing_summary

    if status == RunStatus.FAILED and self._summary.sml_snapshot_id:
        failure_message = self._summary.errors[-1].message if self._summary.errors else None
        try:
            self.db_manager.update_snapshot_status(
                self._summary.sml_snapshot_id,
                "failed",
                failure_message,
            )
        except Exception as snapshot_exc:
            logger.warning(
                "Failed to mark snapshot %s as failed after run failure: %s",
                self._summary.sml_snapshot_id,
                snapshot_exc,
            )

    self._record_step(10, StepStatus.SUCCESS, f"Status: {status.value}")

    finalized = self._summary.finalize()

    # ── Telemetry: record run counter + flush spans ───────────────────────
    try:
        from semabridge.utils.telemetry import record_run, flush
        record_run(
            source=context.source_type,
            target=context.target_type,
            status=status.value,
        )
        flush()
    except Exception as _tel_exc:
        logger.debug(f"Telemetry flush skipped: {_tel_exc}")

    # ── Snowflake observability push (if enabled) ────────────────────────
    try:
        sf_cfg = context.config.snowflake
        if getattr(sf_cfg, "push_run_summary_to_snowflake", False):
            from semabridge.repository.observability_table import ObservabilityTable
            obs = ObservabilityTable(sf_cfg)
            obs.insert_run_summary(finalized)
    except Exception as _obs_exc:
        logger.warning(
            f"Snowflake observability push failed (non-fatal): {_obs_exc}"
        )

    return finalized
