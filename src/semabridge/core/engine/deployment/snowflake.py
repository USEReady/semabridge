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
from typing import Any, Dict, Literal, Optional, Tuple
import yaml
from pydantic import Field
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.connectors.sql_guard import validate_safe_sql
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

def target_snowflake_config(context: RunContext):
    """The Snowflake config this run's target uses: built from the target's
    linked account (identity_id) when it has one, else the run's config.

    Every Snowflake call of a run -- Step 8's live-schema DDL build, the dry
    run's binding preview and Step 9's deploy -- goes through this, so they
    all read the same account, database, schema, warehouse and role.
    """
    target_configs = getattr(context.config, "targets", None) or []
    identity_id = ""
    for tc in target_configs:
        if isinstance(tc, dict) and tc.get("type") == "snowflake":
            identity_id = str(tc.get("identity_id", "") or "").strip()
            break

    if not identity_id:
        # Also check the flat target config if present
        target_config = getattr(context.config, "target", None)
        if target_config and getattr(target_config, "type", "") == "snowflake":
            identity_id = str(getattr(target_config, "identity_id", "") or "").strip()

    if not identity_id:
        return context.config.snowflake

    from sqlalchemy import select
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.auth.credential_builder import build_snowflake_config, project_snowflake_target

    with db_manager.get_session() as session:
        account = session.execute(
            select(Account).where(
                Account.connector_type == "SNOWFLAKE",
                Account.id == identity_id,
            )
        ).scalars().first()

        if not account:
            logger.warning(
                "No linked Snowflake account found for identity_id %s; falling back to global Snowflake settings",
                identity_id,
            )
            return context.config.snowflake

        # Thread-safe: builds an isolated SnowflakeConfig object rather than
        # mutating os.environ (scoped_account_env), which is unsafe when
        # multiple syncs from the same batch run concurrently.
        sf_cfg = build_snowflake_config(
            account, session, context.config.snowflake,
            project_target=project_snowflake_target(context.config),
        )
        logger.info(
            "Snowflake target scoped to account %s (%s) — thread-safe config, no os.environ mutation",
            account.tag, identity_id,
        )
        return sf_cfg


def _deploy_to_snowflake(self, context: RunContext) -> None:
    """
    Deploy to Snowflake, dispatching based on ``deployment_method`` setting
    and utilizing single-tenant environment scoping if ``identity_id`` is supplied.
    """
    self._do_snowflake_deploy(context, target_snowflake_config(context))

_UNSET = object()


def _resolve_load_source_data(context: RunContext) -> "tuple[bool, Optional[str]]":
    """Returns (load_source_data, trigger) for this deploy.

    Data backfill into physical tables is strictly opt-in via project options
    (`options.load_source_data: true`). The core mission of SemaBridge is
    semantic layer synchronization (semantic view creation and metric translation).
    """
    options_ns = getattr(context.config, "options", None)
    raw_load_source_data = getattr(options_ns, "load_source_data", _UNSET)
    if raw_load_source_data is _UNSET or raw_load_source_data is None:
        return False, None

    load_source_data = bool(raw_load_source_data)
    return load_source_data, ("explicit" if load_source_data else None)


def table_binding_scope(context: RunContext) -> Optional[Tuple[str, str]]:
    """(project_id, model_key) under which this model's table bindings are saved."""
    from semabridge.core.upsert_delta import normalize_model_name

    model = getattr(context, "sml_model", None)
    model_key = normalize_model_name(getattr(model, "unique_name", None) or getattr(model, "label", None))
    # Dry runs execute under a preview project id; bindings belong to the real one.
    project_id = (context.config_payload or {}).get("binding_project_id") or context.project_id
    if not (project_id and model_key):
        return None
    return str(project_id), model_key


def preview_table_bindings(context: RunContext) -> Dict[str, Any]:
    """Dry run: which existing table each dataset binds to (with any tie) and
    which tables a sync would create. Read-only against Snowflake.

    Raises LiveSchemaUnavailableError when the live schema can't be read: a
    dry run stops rather than previewing against a schema it never saw.
    """
    from semabridge.connectors.snowflake_emitter import (
        LIVE_SCHEMA_UNAVAILABLE,
        LiveSchemaUnavailableError,
        SnowflakeEmitter,
    )
    from semabridge.connectors.table_binding_resolver import TableBindingAmbiguityError
    from semabridge.core.engine.targets.snowflake import emitter_physical_namespace

    sf_cfg = target_snowflake_config(context)
    try:
        emitter = SnowflakeEmitter(
            sf_cfg,
            behavior=context.behavior,
            physical_namespace=emitter_physical_namespace(context),
        )
        emitter.binding_scope = table_binding_scope(context)
        return {"tables": emitter.preview_table_bindings(context.sml_model), "error": None}
    except TableBindingAmbiguityError:
        raise
    except Exception as exc:
        raise LiveSchemaUnavailableError(
            f"{LIVE_SCHEMA_UNAVAILABLE} for "
            f"{getattr(sf_cfg, 'database', None)}.{getattr(sf_cfg, 'schema_name', None)}: {exc}"
        ) from exc


def _save_table_bindings(emitter) -> None:
    """After a successful deploy, persist bindings this deploy resolved for the
    first time, so every later sync of the project/model reuses them."""
    if not getattr(emitter, "binding_scope", None):
        return
    try:
        new_bindings = emitter.new_table_bindings()
        if not new_bindings:
            return
        project_id, model_key = emitter.binding_scope
        saved = ModelRepository().save_table_bindings(project_id, model_key, new_bindings)
        logger.info("Saved %d table binding(s) for %s/%s", saved, project_id, model_key)
    except Exception as exc:
        logger.warning("Could not save table bindings: %s", exc)


def _do_snowflake_deploy(self, context: RunContext, sf_cfg) -> None:
    """Inner helper to perform Snowflake deployment with resolved config."""
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.engine.targets.snowflake import emitter_physical_namespace

    emitter = SnowflakeEmitter(
        sf_cfg,
        behavior=context.behavior,
        physical_namespace=emitter_physical_namespace(context),
    )
    emitter.binding_scope = table_binding_scope(context)
    deployment_method = getattr(sf_cfg, "deployment_method", "ddl") or "ddl"

    # DDL path
    if deployment_method in ("ddl", "both"):
        # Opt-in data-backfill (options.load_source_data, see
        # core/engine/config.py's _step1_load_config and
        # _resolve_load_source_data above) -- resolved here, not inside
        # the emitter, since only this call site has both
        # context.config.options and context.config.source. Same
        # pbix_path/source_path/file_path alias fallback already used by
        # core/config_loader.py and core/execution_config.py's own PBIX
        # path resolution, so a project using any of those three key names
        # still gets backfill if it opts in.
        source_cfg = getattr(context.config, "source", None)
        # In a multi-model batch each job extracted its OWN file; the
        # project-wide config.source.pbix_path (if any) belongs to at most
        # one of them, so the job's own path must win there. Single-model
        # runs keep the original resolution order.
        batch_pbix_path = (
            getattr(context, "pbix_path", None)
            if int(getattr(context, "batch_model_count", 1) or 1) > 1
            else None
        )
        source_pbix_path = (
            batch_pbix_path
            or getattr(source_cfg, "pbix_path", None)
            or getattr(source_cfg, "source_path", None)
            or getattr(source_cfg, "file_path", None)
        )
        load_source_data, backfill_trigger = _resolve_load_source_data(context)

        deployed = emitter.deploy(
            context.sml_model,
            sync_mode=getattr(context, "sync_mode", "copy"),
            source_pbix_path=source_pbix_path,
            load_source_data=load_source_data,
        )
        # Mirror the drop_ledger merge below: the emitter accumulates
        # backfill outcomes on its own instance during deploy(), copied
        # onto context here so Step 10 (finalize.py) can put them on
        # RunSummary. Runs even when load_source_data was false -- the
        # list is just empty in that case, same no-op cost as every other
        # project that never opts in. `trigger` records WHY this run's
        # backfill ran at all ("explicit" config vs. "first_sync_default"),
        # so the run report can say so plainly rather than looking like an
        # unexplained, silent behavior change.
        context.data_backfill_results = [
            {**entry, "trigger": backfill_trigger}
            for entry in (getattr(emitter, "data_backfill_results", None) or [])
        ]
        # Propagate the live deployed DDL (captured by deploy() itself via
        # GET_DDL, on the connection it already had open — see
        # snowflake_emitter.py's Step 6b) so Step 10 can reconcile
        # context.drop_ledger against what's actually deployed instead of
        # trusting whichever pass populated it first. None when deploy()
        # failed before reaching that capture point, or the capture itself
        # failed non-fatally — either way Step 10 just falls back to the
        # unreconciled ledger, no regression.
        context.deployed_ddl_text = getattr(emitter, "_last_deployed_ddl_text", None)
        # deploy() rebuilds DDL from scratch internally, so emitter.drop_ledger
        # also re-contains every DDL-emission-stage drop Step 8 already merged
        # into context.drop_ledger. Only merge the deployment-stage entries
        # here (genuinely new — Snowflake-rejected identifiers etc.) to avoid
        # duplicating the Step 8 entries. Runs even on failure — the ledger is
        # populated incrementally as generate_ddls()/execute run, before any raise.
        # getattr guards test doubles / stub emitters that don't carry a drop_ledger.
        from semabridge.core.drop_ledger import DropStage as _DropStage
        _emitter_ledger = getattr(emitter, "drop_ledger", None)
        for _rec in getattr(_emitter_ledger, "records", None) or []:
            if _rec.stage == _DropStage.DDL_DEPLOYMENT:
                context.drop_ledger.records.append(_rec)
        if not deployed:
            error_msg = emitter.last_deployment_error or "unknown deployment error"

            # Check if this is a database-not-found error
            if "object does not exist" in error_msg.lower() and "use database" in error_msg.lower():
                db_name, _ = sf_cfg._resolved_db_schema() if hasattr(sf_cfg, "_resolved_db_schema") else ("UNKNOWN", "")
                raise DeploymentError(
                    f"Snowflake deployment failed: Target database does not exist.\n"
                    f"Database: {db_name}\n"
                    f"Error: {error_msg}\n\n"
                    f"SOLUTIONS:\n"
                    f"1. Create the database manually in Snowflake: CREATE DATABASE {db_name}\n"
                    f"2. Or enable auto-create in config: add 'auto_create_database: true' to snowflake section in semabridge.yaml\n"
                    f"3. Or check if the database name in config is correct."
                )

            raise DeploymentError(
                f"Snowflake DDL deployment returned unsuccessful status: {error_msg}"
            )
        _save_table_bindings(emitter)
        self._export_inferred_osi_artifacts(context)

    # Stored-procedure / Cortex YAML path
    if deployment_method in ("yaml_stored_procedure", "both"):
        try:
            import snowflake.connector

            yaml_content = emitter.generate_cortex_yaml(context.sml_model)
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
            kwargs = get_snowflake_connect_kwargs(sf_cfg)
            kwargs["session_parameters"] = {
                "QUERY_TAG": "SemaBridge_CortexYamlDeploy"
            }
            conn = snowflake.connector.connect(**kwargs)
            try:
                cur = conn.cursor()
                emitter.deploy_cortex_yaml(cur, context.sml_model, yaml_content)
                logger.info("Cortex YAML stored-procedure deploy complete")
            finally:
                conn.close()
        except Exception as exc:
            raise DeploymentError(
                f"Cortex YAML stored-procedure deploy failed: {exc}"
            ) from exc

    # Optional: Sync materialized DAX measures to MEASURES_* tables
    if context.source_type == "fabric" and self._should_sync_measures(context):
        self._sync_fabric_measures(context, emitter)

def _export_inferred_osi_artifacts(self, context: RunContext) -> None:
    """Write OSI JSON/YAML with the latest inferred column datatypes."""
    try:
        import json
        import yaml
        from semabridge.converter.sml_to_osi import SMLToOSIConverter

        if not context.sml_model:
            return

        logger.info(
            "Pre-export SML summary: datasets=%s, metrics=%s",
            len(context.sml_model.datasets),
            len(context.sml_model.metrics),
        )
        logger.info(
            "Pre-export dataset names: %s",
            [ds.unique_name for ds in context.sml_model.datasets],
        )
        logger.info(
            "Pre-export metric names: %s",
            [m.unique_name for m in context.sml_model.metrics],
        )

        osi_model = SMLToOSIConverter().to_osi(context.sml_model)
        context.osi_model = osi_model

        out_dir = self._model_output_dir(
            "inferred",
            model_name=context.project_id,
            model_subdir=getattr(context, "physical_namespace", None),
        )
        json_path = out_dir / "osi_inferred.json"
        yaml_path = out_dir / "osi_inferred.yaml"

        osi_dict = osi_model.model_dump(mode="json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(osi_dict, f, indent=2)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(osi_dict, f, sort_keys=False)

        logger.info(f"Generated OSI JSON with inferred types: {json_path}")
        logger.info(f"Generated OSI YAML with inferred types: {yaml_path}")
    except Exception as ex:
        logger.warning(f"Failed to export inferred OSI artifacts (non-fatal): {ex}")

def _should_sync_measures(self, context: RunContext) -> bool:
    """
    Determine if measure sync should be performed.

    Checks configuration and model for syncable measures.
    """
    # Check if any measures need sync (those that couldn't be translated)
    if not context.sml_model:
        return False

    # Check for measures that are sync-enabled but lack SQL expression
    for metric in context.sml_model.metrics:
        if metric.sync_enabled and not metric.sql_expression:
            return True

    return False

def _sync_fabric_measures(self, context: RunContext, emitter) -> None:
    """
    Sync Fabric measures to Snowflake MEASURES_ tables.

    Evaluates DAX measures and writes results to Snowflake.
    """
    from semabridge.connectors.fabric_extractor import FabricExtractor

    config = context.config

    # Align workspace with extracted source context when available.
    ws_id = None
    if context.source_format:
        ws_id = getattr(context.source_format, "workspace_id", None)

    if ws_id and config.fabric.workspace_id != ws_id:
        config.fabric.workspace_id = ws_id

    interactive_token: Optional[str] = None
    try:
        from semabridge.repository.credential_manager import CredentialManager

        cm = CredentialManager()

        stored_fabric = cm.get_credentials("fabric", mask_secrets=False)
        stored_workspace_id = (stored_fabric.get("workspace_id") or "").strip()
        if stored_workspace_id and not ws_id:
            config.fabric.workspace_id = stored_workspace_id

        token_data = cm.get_msal_token()
        if cm.get_fabric_auth_method() == "interactive" and cm.has_valid_token() and token_data:
            interactive_token = token_data.get("access_token")
            logger.debug("sync_to_fabric: injecting interactive token from credential store")
    except Exception as exc:
        logger.warning("sync_to_fabric: credential lookup failed, falling back to env auth: %s", exc)

    # Initialize Fabric extractor
    fabric_extractor = FabricExtractor(config.fabric)
    if interactive_token:
        fabric_extractor._access_token = interactive_token
        fabric_extractor._token_expires_at = time.time() + 1800

    # Get dataset ID from source format metadata
    dataset_id = None
    if context.source_format:
        dataset_id = getattr(context.source_format, "dataset_id", None)

    if not dataset_id:
        dataset_name = None
        if context.source_format:
            dataset_name = getattr(context.source_format, "dataset_name", None)
        logger.warning(
            "Cannot sync measures: no dataset ID available "
            f"(dataset_name={dataset_name or 'unknown'})"
        )
        return

    # Sync all measures
    try:
        results = emitter.sync_all_measures(
            sml=context.sml_model,
            fabric_extractor=fabric_extractor,
            dataset_id=dataset_id,
        )

        success_count = sum(1 for r in results.values() if r.get("status") == "success")
        logger.info(f"Measure sync complete: {success_count}/{len(results)} measures synced")

    except Exception as e:
        logger.error(f"Measure sync failed: {e}")
        # Don't fail the deployment, just log warning
        logger.warning("Continuing despite measure sync failure")

def _update_snowflake_view_metadata_on_failure(self, context: RunContext, sf_cfg, error_msg: str) -> bool:
    """
    Update Snowflake view's LAST_ALTERED timestamp on deployment failure.
    
    This is a silent, non-destructive operation that:
    1. Keeps the existing view definition completely unchanged
    2. Silently updates only the view's LAST_ALTERED timestamp
    3. Leaves no audit trail or comments visible to users
    
    Args:
        context: Execution context containing project_id and config
        sf_cfg: Snowflake configuration
        error_msg: Error message from the failed deployment (unused, for future logging)
        
    Returns:
        True if timestamp update succeeded, False otherwise
    """
    # The deployed object is the SEMANTIC VIEW this model deploys to, in the
    # run's configured database/schema (SnowflakeConfig.schema_name -- not
    # getattr(sf_cfg, "schema"), which is pydantic's schema() method).
    db_name = str(getattr(sf_cfg, "database", None) or "").strip()
    schema_name = str(getattr(sf_cfg, "schema_name", None) or "").strip()
    view_name = str(getattr(context, "deployed_view_name", None) or "").strip()
    if not (db_name and schema_name and view_name):
        logger.debug(
            "Skipping view timestamp update: database/schema/view unknown (db=%r schema=%r view=%r)",
            db_name, schema_name, view_name,
        )
        return False
    fully_qualified_name = f'"{db_name}"."{schema_name}"."{view_name}"'

    try:
        import snowflake.connector
        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

        kwargs = get_snowflake_connect_kwargs(sf_cfg)
        kwargs["session_parameters"] = {
            "QUERY_TAG": "SemaBridge_SilentUpdate"
        }

        conn = snowflake.connector.connect(**kwargs)
        try:
            cur = conn.cursor()
            # Only touch a view that exists; read its current comment so it
            # can be set back to exactly the same value (no visible change,
            # LAST_ALTERED moves).
            escaped_name = view_name.replace("'", "''")
            show_sql = f"SHOW SEMANTIC VIEWS LIKE '{escaped_name}' IN SCHEMA \"{db_name}\".\"{schema_name}\""
            validate_safe_sql(show_sql)
            cur.execute(show_sql)
            row = cur.fetchone()
            if not row:
                logger.debug("Skipping view timestamp update: %s does not exist", fully_qualified_name)
                return False
            columns = [str(col[0]).lower() for col in (cur.description or [])]
            comment = None
            if "comment" in columns:
                comment = row[columns.index("comment")]
            if comment:
                escaped_comment = str(comment).replace("'", "''")
                set_sql = f"ALTER SEMANTIC VIEW IF EXISTS {fully_qualified_name} SET COMMENT = '{escaped_comment}'"
                validate_safe_sql(set_sql)
                cur.execute(set_sql)
            else:
                unset_sql = f"ALTER SEMANTIC VIEW IF EXISTS {fully_qualified_name} UNSET COMMENT"
                validate_safe_sql(unset_sql)
                cur.execute(unset_sql)

            logger.debug("Updated LAST_ALTERED (silent) for %s", fully_qualified_name)
            return True

        finally:
            conn.close()
            
    except Exception as e:
        # Log at debug level - this is a silent background operation
        logger.debug(
            f"Failed to update Snowflake view LAST_ALTERED timestamp (non-fatal): {e}"
        )
        return False
