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
    from semabridge.core.engine.deployment.databricks import resolve_databricks_config_for_context
    from semabridge.core.engine.conversion.rules_choice import databricks_planner_enabled

    # With the planner path on, Step 6 wrote Databricks SQL and Step 6b made the
    # plan and the view definitions; the existing publisher (which expects
    # today's SQL) must not run on it. Step 9 deploys the plan.
    if databricks_planner_enabled(context):
        plan = getattr(context, "databricks_view_plan", None) or {}
        logger.info("Databricks planner path: %d metric view(s) planned; Step 9 deploys them",
                    len(plan.get("views") or []))
        return

    databricks_cfg = resolve_databricks_config_for_context(context, allow_offline_stub=True)
    publisher = DatabricksPublisher(databricks_cfg, behavior=context.behavior)
    publisher._offline_plan_mode = True
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
    if cfg is None and getattr(context, "project_id", None) is not None:
        # The resolver the existing deploy path uses (linked account, stored
        # credentials, then environment): one connection for both paths.
        try:
            from semabridge.core.engine.deployment.databricks import resolve_databricks_config_for_context

            cfg = resolve_databricks_config_for_context(context, allow_offline_stub=False)
        except Exception as exc:  # noqa: BLE001 -- fall back to the account / settings below
            logger.info("Databricks connection via the deploy resolver unavailable (%s)", type(exc).__name__)
            cfg = None
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
        read_existing_objects,
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
        # Which workspace + warehouse this run's plan rests on (probe evidence lookup, 3.5).
        context.databricks_target = {"host": info.host, "warehouse_id": client.warehouse_id}
        catalog_meta = read_unity_catalog(client, schemas)
        if info.catalog and info.schema:
            # Objects already in the output schema: proposed table names never take one (3.7).
            context.databricks_existing_objects = read_existing_objects(client, info.catalog, info.schema)
    except (DatabricksSqlError, DatabricksAuthError, DatabricksGuardError) as exc:
        raise ConversionError(f"{LIVE_SCHEMA_UNAVAILABLE} ({', '.join(f'{c}.{s}' for c, s in schemas)}): {exc}") from None
    try:
        plan = plan_tables(context.sml_model, catalog_meta, source_schemas=schemas,
                           on_missing_source=mode, table_mapping=mapping if isinstance(mapping, dict) else {})
    except TableMatchingError as exc:
        raise ConversionError(str(exc)) from None

    # 3.8: the Unity Catalog id of every bound table, recorded with the plan.
    bound = [b.table for b in plan.bindings if b.action == "bind" and b.table is not None]
    if bound:
        from semabridge.target_backends.databricks.tables import read_table_ids

        ids = read_table_ids(client, [t.full_name for t in bound])
        for t in bound:
            t.table_id = ids.get(t.full_name)

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

    defaults = _planner_defaults(context)
    values: Dict[str, Any] = {**defaults, "capabilities": frozenset(defaults["capabilities"])}
    values["evidence"] = dict(defaults.get("evidence") or {})
    project = _project_planner_block(context)
    for key in ("calculate_mode", "view_mode", "primary_fact"):
        if project.get(key):
            values[key] = str(project[key]).strip()
    for key in ("selected_tables", "excluded_measures"):
        if project.get(key):
            values[key] = tuple(str(x) for x in project[key])
    values["requested_names"] = tuple(
        (str(r.get("source_path")), str(r.get("target_name")), r.get("origin"))
        for r in (getattr(context, "databricks_name_requests", None) or [])
        if isinstance(r, dict) and r.get("source_path") and r.get("target_name")
    )
    values["security_roles"] = security_roles_for_run(context)
    catalog, schema = output_location(context)
    values["output_catalog"], values["output_schema"] = catalog, schema
    existing = getattr(context, "databricks_existing_objects", None)
    values["existing_objects"] = frozenset(existing) if existing is not None else None
    # 3.8 (A5): names already deployed for this model are kept on re-plan.
    from semabridge.target_backends.databricks.deploy.names import reserved_for

    reserved, status = reserved_for(owning_project_id(context), getattr(context, "physical_namespace", None))
    values.update(reserved)
    context.databricks_names_history = status
    return PlannerSettings(**values)


def owning_project_id(context: RunContext) -> str:
    """The project a dry run belongs to. Dry runs execute as a transient preview
    project (``preview-...``); the real project travels as ``binding_project_id``.
    Deployed-name history and plan ownership are the real project's."""
    payload = getattr(context, "config_payload", None) or {}
    return str(payload.get("binding_project_id") or getattr(context, "project_id", "") or "")


def stub_form_for_run(context: RunContext) -> str:
    """How missing tables are stood in for (decided at planning, recorded in the
    plan and bundle; deploy never switches it): empty_table (default) | inline."""
    target = getattr(context.config, "target", None)
    form = str(getattr(target, "stub_form", "") or "empty_table").strip().lower()
    return form if form in ("empty_table", "inline") else "empty_table"


def output_location(context: RunContext):
    """(catalog, schema) the project's metric views (and any proposed tables) go to."""
    target = getattr(context.config, "target", None)
    catalog = str(getattr(target, "catalog", "") or "").strip() or None
    schema = str(getattr(target, "schema", "") or getattr(target, "schema_name", "") or "").strip() or None
    return catalog, schema


def security_roles_for_run(context: RunContext) -> Optional[int]:
    """Power BI security roles of this run's model: >0 found, 0 confirmed none,
    None unknown (the reader could not confirm). Never 0 by assumption."""
    osi = getattr(context, "osi_model", None)
    carried = ((getattr(osi, "metadata", None) or {}).get("security_roles")) if osi is not None else None
    if carried:
        return len(carried)
    source = str(getattr(context, "source_type", "") or "").strip().lower()
    if source == "snowflake":
        return 0  # a Snowflake semantic view source has no Power BI roles
    if source not in ("pbix", "fabric"):
        return None
    tmsl = getattr(getattr(context, "source_format", None), "tmsl_definition", None)
    if not isinstance(tmsl, dict):
        return None
    model = tmsl.get("model") if isinstance(tmsl.get("model"), dict) else tmsl
    if isinstance(model.get("roles"), list):
        return sum(1 for r in model["roles"] if isinstance(r, dict))
    if source == "fabric":
        return 0  # the service's model definition lists every role
    # PBIX: a complete DataModelSchema lists every role; other readers must
    # have read the Role table (an explicit empty list) to confirm none.
    return 0 if getattr(context, "pbix_parser", None) == "schema" else None


def _planner_defaults(context: RunContext) -> Dict[str, Any]:
    """Global planner defaults. ``capabilities``: the optional Databricks features this
    run may use = the features PROVEN by valid probe evidence (3.5) for this
    workspace + warehouse, intersected with the admin allow-list
    (behavior.databricks.planner_capabilities). No connection or no valid proof:
    none. Read from the evidence store only -- the warehouse is not queried."""
    db = getattr(getattr(context, "behavior", None), "databricks", None)
    caps, ref = _capability_proof(context, capability_allow_list(db))
    return {
        "calculate_mode": str(getattr(db, "planner_calculate_mode", "approximate") or "approximate").strip().lower(),
        "view_mode": str(getattr(db, "planner_view_mode", "separate_views") or "separate_views").strip().lower(),
        "capabilities": sorted(caps),
        "evidence": ref,
    }


CAPABILITY_ALLOW_ENV = "SEMABRIDGE_DATABRICKS_CAPABILITY_ALLOW_LIST"


def capability_allow_list(db_behavior) -> list:
    """The admin allow-list: behavior.databricks.planner_capabilities, unless the
    local environment sets SEMABRIDGE_DATABRICKS_CAPABILITY_ALLOW_LIST (comma
    separated; for one developer's machine, e.g. in the git-ignored .env, never
    committed). Either way a feature is used only when it is also PROVEN."""
    raw = os.environ.get(CAPABILITY_ALLOW_ENV)
    if raw is not None and raw.strip():
        return [x.strip() for x in raw.split(",") if x.strip()]
    return list(getattr(db_behavior, "planner_capabilities", None) or [])


def _capability_proof(context: RunContext, allow_list):
    from semabridge.target_backends.databricks.probes.evidence import effective_capabilities

    target = getattr(context, "databricks_target", None) or {}
    return effective_capabilities(target.get("host"), target.get("warehouse_id"), allow_list)


def _project_planner_block(context: RunContext) -> Dict[str, Any]:
    target = getattr(context.config, "target", None)
    project = getattr(target, "planner", None) or {}
    if not isinstance(project, dict):
        project = dict(getattr(project, "__dict__", {}) or {})
    return dict(project)


def _run_databricks_planner(self, context: RunContext, table_plan) -> None:
    """Plan the metric views (offline, read-only) so Step 7 saves the plan and the
    dry run shows it. Settings errors and self-check failures stop the run.

    The plan is also written next to the run's other Databricks artifacts
    (output/databricks/<project>/databricks_view_plan.json and .md), with the
    planner's inputs (databricks_planner_input.json) so the screen can re-plan
    after a rename or an option change without another dry run (replan.py)."""
    from semabridge.target_backends.databricks import replan
    from semabridge.target_backends.databricks.planner import (
        PlannerError,
        PlannerInvariantError,
        plan_metric_views,
    )

    settings = databricks_planner_settings(context)
    try:
        result = plan_metric_views(context.sml_model, table_plan, settings)
    except (PlannerError, PlannerInvariantError) as exc:
        raise ConversionError(f"Databricks planner: {exc}") from None
    plan = result.as_dict()
    summary = plan["summary"]
    logger.info("Databricks view plan: %d view(s), %d measure(s) placed, %d left out (%d approximate)",
                summary["views"], summary["measures_placed"], summary["measures_left_out"], summary["approximate"])
    subdir = getattr(context, "physical_namespace", None)
    out_dir = replan.output_dir(context.project_id, subdir)
    revision = replan.now_revision()
    cache_ref = {"project_id": str(context.project_id), "model_subdir": subdir,
                 # the real project this (preview) plan belongs to: deploy accepts the plan
                 # only for this project, and re-plans read its deployed-name history
                 "binding_project_id": owning_project_id(context)}
    try:
        plan["meta"] = replan.write_cache(
            out_dir, sml_model=context.sml_model, table_plan=table_plan, security_roles=settings.security_roles,
            defaults=_planner_defaults(context), project_planner=_project_planner_block(context),
            cache_ref=cache_ref, revision=revision,
            output={"catalog": settings.output_catalog, "schema": settings.output_schema,
                    "existing_objects": sorted(settings.existing_objects) if settings.existing_objects is not None else None,
                    "reserved_table_names": dict(settings.reserved_table_names or {}),
                    "stub_form": stub_form_for_run(context),
                    "names_history": getattr(context, "databricks_names_history", "none")},
        )
    except Exception as exc:  # the plan is still valid; it just cannot be re-planned without a dry run
        logger.warning("Could not store the Databricks planner input for re-planning: %s", exc)
        plan["meta"] = {"revision": revision, "status": "valid", "source": "dry_run", "cache_ref": None,
                        "stub_form": stub_form_for_run(context),
                        "names_history": getattr(context, "databricks_names_history", "none"),
                        "basis": {"table_check_at": None, "uc_recheck_required_before_deploy": True}}
    # 3.7: the view definitions (offline text; nothing is sent to Databricks).
    from semabridge.target_backends.databricks import bundle

    model = context.sml_model
    model_label = str(getattr(model, "label", "") or getattr(model, "unique_name", "") or "model")
    try:
        defs = bundle.attach_definitions(plan, out_dir, catalog=settings.output_catalog,
                                         schema=settings.output_schema, model_label=model_label,
                                         run_id=str(getattr(context, "run_id", "") or "") or None)
        if defs["status"] != "written":
            logger.warning("Databricks view definitions not written: %s", defs["error"])
    except Exception as exc:  # file system trouble: the plan stays valid, definitions unavailable
        logger.warning("Could not write the Databricks view definitions: %s", exc)
        plan["definitions"] = []
        plan.setdefault("meta", {})["definitions"] = {"status": "unavailable", "error": str(exc),
                                                      "error_code": "bundle_error", "revision": revision, "views": []}
    context.databricks_view_plan = plan
    try:
        if replan.record_plan(out_dir, plan, str(context.project_id), reset=True):
            bundle.set_current(out_dir, revision, reset=True)
        logger.info("Databricks view plan written to %s", out_dir / replan.PLAN_MD)
    except Exception as exc:  # non-fatal: the plan is in the run summary either way
        logger.warning("Could not write the Databricks view plan file: %s", exc)
