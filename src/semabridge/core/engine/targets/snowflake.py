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

def emitter_physical_namespace(context: RunContext) -> Optional[str]:
    """Physical source data tables in Snowflake (CATEGORY, DATE, SALESFACT, etc.)
    are pre-existing user-owned data tables and must never be namespaced per model,
    preserving parity with the demo_version sync engine. Model isolation in
    Snowflake is achieved via unique semantic view names, not physical table prefixing.
    """
    return None


def _convert_to_snowflake_target(self, context: RunContext) -> None:
    """Generate Snowflake DDL against the live Snowflake schema.

    The live schema (table bindings, real columns, enriched-view flag
    columns) is required: if it can't be loaded the step fails and the run
    stops -- it never falls back to a schema-less build, which would report
    NULL placeholders and dropped columns that a real deploy wouldn't have.
    """
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.engine.deployment.snowflake import (
        table_binding_scope,
        target_snowflake_config,
    )
    from semabridge.sml.serializer import SMLSerializer

    emitter = SnowflakeEmitter(
        target_snowflake_config(context),
        behavior=context.behavior,
        physical_namespace=emitter_physical_namespace(context),
    )
    emitter.binding_scope = table_binding_scope(context)

    if not context.sml_model:
        raise ConversionError("No SML model available for target conversion. Ensure Stage 6 completed successfully.")

    from semabridge.utils.name_translator import get_target_deployment_name

    context.deployed_view_name = get_target_deployment_name(
        getattr(context.sml_model, "unique_name", None)
        or getattr(context.sml_model, "label", None)
        or "model",
        "snowflake",
    )

    gen_fn = getattr(emitter, "generate_ddls_live", getattr(emitter, "generate_ddls", None))
    ddls = gen_fn(context.sml_model) if gen_fn else []
    context.generated_ddl = ddls[0] if ddls else ""
    # Surface DDL-emission-time drops (DAX translation failures, unresolved
    # references, dropped relationships) on the shared context ledger so
    # they reach RunSummary.dropped_entities even when Stage 9 (deploy) is
    # skipped, e.g. for dry-run. getattr guards test doubles / stub emitters
    # that don't carry a drop_ledger.
    _emitter_ledger = getattr(emitter, "drop_ledger", None)
    if _emitter_ledger is not None:
        context.drop_ledger.extend(_emitter_ledger)
    if getattr(context, "dry_run", False):
        # Preview only: the drops/placeholders above are what a sync would
        # report; no target artifact is written or promoted.
        return

    output_dir = self._model_output_dir(
        "reverse",
        model_name=context.project_id,
        model_subdir=getattr(context, "physical_namespace", None),
    )
    full_ddl = "\n\n".join(ddls)
    yaml_out = emitter.generate_cortex_yaml(context.sml_model)

    # Write to a STAGING location, not the canonical output/reverse/<project>/
    # path directly. If Step 9 (deploy) subsequently fails, the last known-good
    # cortex_analyst.yaml/semantic_view.sql at the canonical path must remain
    # untouched -- Step 10 (finalize.py) only promotes staging -> canonical on
    # a genuine SUCCESS. Writing here unconditionally would otherwise corrupt
    # the last-good artifact with content describing a model that was never
    # actually deployed.
    # Per-model staging subdirectory (same model_subdir as the canonical
    # output_dir above): models of one multi-model project run concurrently
    # and must never overwrite each other's staged DDL/YAML before promotion.
    # Single-model projects have no physical_namespace, so their staging path
    # is unchanged.
    staging_dir = self._model_output_dir(
        "reverse",
        ".staging",
        model_name=context.project_id,
        model_subdir=getattr(context, "physical_namespace", None),
    )
    staged_ddl_path = staging_dir / "semantic_view.sql"
    staged_yaml_path = staging_dir / "cortex_analyst.yaml"

    with open(staged_ddl_path, "w", encoding="utf-8") as f:
        f.write(full_ddl)
    with open(staged_yaml_path, "w", encoding="utf-8") as f:
        f.write(yaml_out)

    if isinstance(context.sml_model, SMLModel):
        sml_path = output_dir / "sml" / "model.yaml"
        SMLSerializer.save(context.sml_model, sml_path)

    final_ddl_path = output_dir / "semantic_view.sql"
    final_yaml_path = output_dir / "cortex_analyst.yaml"
    context.staged_target_artifact_paths = {
        "ddl": staged_ddl_path,
        "yaml": staged_yaml_path,
        "final_ddl": final_ddl_path,
        "final_yaml": final_yaml_path,
    }
    # Reported path is the eventual canonical destination -- if this run
    # fails before promotion, it still correctly points at whatever artifact
    # is actually live (the previous successful run's).
    context.target_artifact_path = str(final_ddl_path)


def _step6b_predict_anchor_flag_columns(self, context: RunContext) -> None:
    """Predict this model's time-intelligence anchor_flag_map WITHOUT a
    live Snowflake connection, and upgrade anchor-dependent metrics'
    `sql_expression` from Stage 1's safe CURRENT_DATE()-anchored fallback
    to a flag-column reference wherever prediction says a flag column will
    exist — BEFORE Step 7 persists this model, so a future rollback to
    this snapshot (see cli/commands/version_history_commands.py's
    `rollback --sync`, which deploys a persisted `sql_expression`
    verbatim, with no re-translation) doesn't resurrect the plain
    fallback rendering forever.

    Snowflake-only: `predict_anchor_flag_map`/`anchor_flag_map` is an
    inherently Snowflake-specific concept (enriched-view flag columns tied
    to Snowflake's semantic-view METRICS-clause constraints); every other
    target is completely unaffected — this is only ever called when
    `context.target_type == "snowflake"` (see engine.py's `execute()`).

    Uses a throwaway `SnowflakeEmitter` that is never used to `deploy()`
    — no connection is ever opened, so every eligibility/column check
    `predict_anchor_flag_map` performs transparently falls back to this
    model's own declared columns (see
    `SnowflakeEmitter._resolve_fact_enrichment_date_column`). Never
    raises: prediction is a best-effort improvement over Stage 1's
    already-safe fallback, never a correctness requirement — a failure
    here must not fail an otherwise-successful sync.
    """
    if not context.sml_model:
        return
    try:
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        from semabridge.connectors.anchor_flag_rerender import rerender_anchor_dependent_metrics
        from semabridge.converter.dax_translator import DAXTranslator

        emitter = SnowflakeEmitter(context.config.snowflake, behavior=context.behavior)
        predicted_map = emitter.predict_anchor_flag_map(context.sml_model)
        if not predicted_map:
            return

        dataset_col_lookup, dataset_aliases = DAXTranslator.build_schema_lookup(
            getattr(context.sml_model, "datasets", []) or []
        )
        rerendered = rerender_anchor_dependent_metrics(
            context.sml_model,
            predicted_map,
            dataset_col_lookup,
            dataset_aliases,
            label="predicted",
        )
        if rerendered:
            logger.info(
                "Step 6b: re-rendered %d anchor-dependent metric(s) using a predicted flag map",
                rerendered,
            )
    except Exception as exc:
        logger.warning("Step 6b anchor-flag-map prediction skipped: %s", exc)
