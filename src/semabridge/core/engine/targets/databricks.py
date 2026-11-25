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
    from semabridge.core.engine.conversion.rules_choice import databricks_planner_enabled

    # With the planner path on, Step 6 wrote Databricks SQL (Phase 3.2); the
    # existing publisher expects today's SQL, so it must not run on it. The
    # planner path's own Step 8 lands in Phase 3.8.
    if databricks_planner_enabled(context):
        raise ConversionError(
            "Databricks planner path (behavior.databricks.use_planner_path) is on, but its "
            "deploy steps are not built yet (Phase 3.8). Dry runs work; to deploy with the "
            "existing Databricks publisher, turn use_planner_path off."
        )

    publisher = DatabricksPublisher(context.config.databricks, behavior=context.behavior)
    # Apply the same heuristic transforms that publish() applies so the
    # debug YAML artifact faithfully reflects what will be deployed.
    publisher._apply_model_config_overrides(context.sml_model)
    statements, created, skipped, skipped_details = publisher.generate_measure_view_statements(
        context.sml_model,
        view_type_override="metric_view",
    )

    output_dir = self._model_output_dir(
        "databricks",
        model_name=context.project_id,
        model_subdir=getattr(context, "physical_namespace", None),
    )
    yaml_path = output_dir / "databricks_metric_views.yaml"

    metric_view_defs: list[dict[str, Any]] = []
    for stmt in statements:
        view_match = re.search(r"CREATE OR REPLACE VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)", stmt)
        yaml_match = re.search(r"WITH METRICS LANGUAGE YAML AS \$\$\n(.*)\n\$\$", stmt, re.S)
        metric_view_defs.append({
            "view_name": view_match.group(1) if view_match else "unknown",
            "yaml_definition": yaml_match.group(1) if yaml_match else stmt,
        })

    deployed_names = [
        str(d["view_name"]).replace("`", "")
        for d in metric_view_defs
        if d.get("view_name") and d["view_name"] != "unknown"
    ]
    if deployed_names:
        context.deployed_view_name = ", ".join(deployed_names)

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


# ---------------------------------------------------------------------------
# Step 6b (Databricks planner path, Phase 3.4): Unity Catalog table matching
# ---------------------------------------------------------------------------


def _databricks_connection_for_run(context: RunContext):
    """Connection details for this run, or None when none is configured.

    Same credential order as the deploy step: the account linked at Step 3
    (account_id), else the target's identity_id account, else the global
    DATABRICKS_* settings. Catalog/schema come from the project's target."""
    from semabridge.target_backends.databricks.auth import DatabricksConnectionInfo

    target = getattr(context.config, "target", None)
    catalog = str(getattr(target, "catalog", "") or "").strip() or None
    schema = str(getattr(target, "schema", "") or getattr(target, "schema_name", "") or "").strip() or None

    cfg = getattr(context.config, "_databricks", None)
    identity_id = str(getattr(target, "identity_id", "") or "").strip()
    if cfg is None and identity_id:
        try:
            from sqlalchemy import select
            from semabridge.auth.credential_builder import build_databricks_config
            from semabridge.repository.orm.models import Account
            from semabridge.repository.orm.session_factory import db_manager

            with db_manager.get_session() as session:
                account = session.execute(
                    select(Account).where(Account.connector_type == "DATABRICKS", Account.id == identity_id)
                ).scalars().first()
                if account is not None:
                    cfg = build_databricks_config(account, session, context.config.databricks)
        except Exception as exc:  # fall back to the global settings below
            logger.info("Databricks account %s not usable for table matching (%s)", identity_id, type(exc).__name__)
            cfg = None
    if cfg is None:
        try:
            cfg = context.config.databricks
        except Exception:
            return None
    try:
        return DatabricksConnectionInfo.from_config(cfg, catalog=catalog, schema=schema)
    except Exception:
        return None


def _step6b_databricks_tables(self, context: RunContext) -> None:
    """Match the model's tables to Unity Catalog (bind / stub / skip) and
    rewrite the measures' SQL to the real column names, BEFORE Step 7
    persists the model -- so the snapshot (and the dry-run screen) shows the
    names that will deploy. Read-only against Databricks.

    No Databricks connection configured: the plan is reported as
    unavailable and the measures keep their model column names. A configured
    connection that fails, an ambiguous match, or missing tables with
    on_missing_source=fail stop the run (never guess without the live schema).
    """
    from semabridge.target_backends.databricks.client import DatabricksSqlClient, DatabricksSqlError
    from semabridge.target_backends.databricks.auth import DatabricksAuthError
    from semabridge.target_backends.databricks.guard import DatabricksGuardError
    from semabridge.target_backends.databricks.tables import (
        LIVE_SCHEMA_UNAVAILABLE,
        TableMatchingError,
        parse_source_schemas,
        plan_tables,
        read_unity_catalog,
        rewrite_measure_columns,
    )
    from semabridge.target_backends.databricks.warehouses import resolve_warehouse

    if context.sml_model is None:
        return
    target = getattr(context.config, "target", None)
    info = _databricks_connection_for_run(context)
    if info is None:
        context.table_binding_plan = {
            "platform": "databricks", "tables": [], "error": None,
            "warnings": ["No Databricks connection is configured: table plan unavailable; "
                         "measures keep their model column names."],
        }
        _run_databricks_planner(self, context, None)
        return

    schemas = parse_source_schemas(getattr(target, "source_schemas", None), info.catalog, info.schema)
    mode = str(getattr(target, "on_missing_source", "") or "stub").strip().lower()
    mapping = getattr(target, "table_mapping", None) or {}
    client = DatabricksSqlClient(
        info, deadline_seconds=180,
        query_tags={"project_id": str(context.project_id), "run_id": str(context.run_id), "step": "table_matching"},
    )
    try:
        if not client.warehouse_id:
            choice = resolve_warehouse(client)
            if choice["warehouse"] is None:
                raise DatabricksSqlError(choice["message"])
            client.warehouse_id = choice["warehouse"].id
        catalog_meta = read_unity_catalog(client, schemas)
    except (DatabricksSqlError, DatabricksAuthError, DatabricksGuardError) as exc:
        raise ConversionError(f"{LIVE_SCHEMA_UNAVAILABLE} ({', '.join(f'{c}.{s}' for c, s in schemas)}): {exc}") from None
    try:
        plan = plan_tables(context.sml_model, catalog_meta, source_schemas=schemas,
                           on_missing_source=mode, table_mapping=mapping if isinstance(mapping, dict) else {})
    except TableMatchingError as exc:
        raise ConversionError(str(exc)) from None

    rewrite = plan.rewrite_map()
    if rewrite:
        for metric in getattr(context.sml_model, "metrics", []) or []:
            metric.sql_expression = rewrite_measure_columns(metric.sql_expression, rewrite)
    context.databricks_table_plan = plan
    context.table_binding_plan = plan.as_dict()
    bound = sum(1 for b in plan.bindings if b.action == "bind")
    logger.info("Databricks table plan: %d bound, %d stub, %d skipped (sources %s)", bound,
                sum(1 for b in plan.bindings if b.action == "stub"),
                sum(1 for b in plan.bindings if b.action == "skip"),
                ", ".join(f"{c}.{s}" for c, s in schemas))
    _run_databricks_planner(self, context, plan)


# ---------------------------------------------------------------------------
# Step 6b (Databricks planner path, Phase 3.6): the metric-view plan
# ---------------------------------------------------------------------------


def databricks_planner_settings(context: RunContext):
    """Planner settings: global behavior (behavior.databricks.planner_*), overridden
    per project by the target's optional `planner` block (calculate_mode,
    view_mode, selected_tables, primary_fact, excluded_measures)."""
    from semabridge.target_backends.databricks.planner import PlannerSettings

    db = getattr(getattr(context, "behavior", None), "databricks", None)
    values: Dict[str, Any] = {
        "calculate_mode": str(getattr(db, "planner_calculate_mode", "approximate") or "approximate").strip().lower(),
        "view_mode": str(getattr(db, "planner_view_mode", "separate_views") or "separate_views").strip().lower(),
        "capabilities": frozenset(getattr(db, "planner_capabilities", None) or []),
    }
    target = getattr(context.config, "target", None)
    project = getattr(target, "planner", None) or {}
    if not isinstance(project, dict):
        project = getattr(project, "__dict__", {}) or {}
    for key in ("calculate_mode", "view_mode", "primary_fact"):
        if project.get(key):
            values[key] = str(project[key]).strip()
    for key in ("selected_tables", "excluded_measures"):
        if project.get(key):
            values[key] = tuple(str(x) for x in project[key])
    return PlannerSettings(**values)


def _run_databricks_planner(self, context: RunContext, table_plan) -> None:
    """Plan the metric views (offline, read-only) so Step 7 saves the plan and the
    dry run shows it. Settings errors and self-check failures stop the run.

    The plan is also written next to the run's other Databricks artifacts:
    output/databricks/<project>/databricks_view_plan.json and .md."""
    from semabridge.target_backends.databricks.planner import (
        PlannerError,
        PlannerInvariantError,
        plan_metric_views,
    )

    try:
        result = plan_metric_views(context.sml_model, table_plan, databricks_planner_settings(context))
    except (PlannerError, PlannerInvariantError) as exc:
        raise ConversionError(f"Databricks planner: {exc}") from None
    context.databricks_view_plan = result.as_dict()
    summary = context.databricks_view_plan["summary"]
    logger.info("Databricks view plan: %d view(s), %d measure(s) placed, %d left out (%d approximate)",
                summary["views"], summary["measures_placed"], summary["measures_left_out"], summary["approximate"])
    try:
        from semabridge.target_backends.databricks.planner.report import render

        out_dir = self._model_output_dir("databricks", model_name=context.project_id,
                                         model_subdir=getattr(context, "physical_namespace", None))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "databricks_view_plan.json").write_text(
            json.dumps(context.databricks_view_plan, indent=2, ensure_ascii=False), encoding="utf-8")
        (out_dir / "databricks_view_plan.md").write_text(
            render(context.databricks_view_plan, str(context.project_id)), encoding="utf-8")
        logger.info("Databricks view plan written to %s", out_dir / "databricks_view_plan.md")
    except Exception as exc:  # non-fatal: the plan is in the run summary either way
        logger.warning("Could not write the Databricks view plan file: %s", exc)
