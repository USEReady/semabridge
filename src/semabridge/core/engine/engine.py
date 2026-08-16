from __future__ import annotations

import time
import uuid
import json
import os
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, Literal
import yaml
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import Settings, get_settings
from semabridge.core.config_loader import get_project_file_path
from semabridge.core.run_summary import (
    STEP_NAMES,
    RunStatus,
    RunSummary,
    StepStatus,
    create_run_summary,
)
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger
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

class ExecutionEngine:
    """
    Orchestrates CLI execution following the mandatory 10-step flow.
    
    Each method corresponds to one step and must be called in order.
    The engine enforces this order and handles failures appropriately.
    """
    
    SUPPORTED_SOURCES = {"snowflake", "fabric", "pbix"}
    SUPPORTED_TARGETS = {"snowflake", "fabric", "databricks", None}
    
    def __init__(self, db_manager: Optional[ModelRepository] = None):
        self.db_manager = db_manager or ModelRepository()
        self._current_step = 0
        self._context: Optional[RunContext] = None
        self._summary: Optional[RunSummary] = None

    @staticmethod
    def _safe_output_name(name: Optional[str]) -> str:
        raw = str(name or "model")
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)
        safe = re.sub(r"_+", "_", safe).strip("._")
        return safe or "model"

    def _model_output_dir(self, *parts: str, model_name: Optional[str] = None) -> Path:
        safe_name = self._safe_output_name(model_name or (self._context.project_id if self._context else None))
        path = Path("output")
        for part in parts:
            path /= part
        path /= safe_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def from_yaml(cls, config_path: "Path") -> "ExecutionEngine":
        """
        Factory that constructs an ExecutionEngine from a YAML config file.

        The YAML is expected to follow the SemaBridge project file schema
        (semabridge.yaml / config.yaml).  Settings are applied to the current
        process environment so that ``get_settings()`` honours them throughout
        the pipeline.

        Example::

            engine = ExecutionEngine.from_yaml(Path("semabridge.yaml"))
            summary = engine.execute(source="fabric", target="snowflake")

        Args:
            config_path: Path to the YAML configuration file.

        Returns:
            A ready-to-use ``ExecutionEngine`` instance.
        """
        import os
        import yaml  # pyyaml — already a project dependency
        from pathlib import Path as _Path

        cfg_path = _Path(config_path).resolve()
        if not cfg_path.exists():
            raise FileNotFoundError(f"Config file not found: {cfg_path}")

        with cfg_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

        # — Inject YAML values into os.environ so pydantic-settings picks them up.
        # Only set keys that aren't already present (env-var takes precedence).
        def _setenv(key: str, val: object) -> None:
            if val is not None and key not in os.environ:
                os.environ[key] = str(val)

        sf = raw.get("snowflake") or {}
        _setenv("SNOWFLAKE_ACCOUNT",   sf.get("account"))
        _setenv("SNOWFLAKE_USER",      sf.get("user"))
        _setenv("SNOWFLAKE_PASSWORD",  sf.get("password"))
        _setenv("SNOWFLAKE_WAREHOUSE", sf.get("warehouse"))
        _setenv("SNOWFLAKE_DATABASE",  sf.get("database"))
        _setenv("SNOWFLAKE_SCHEMA",    sf.get("schema") or sf.get("schema_name"))
        _setenv("SNOWFLAKE_ROLE",      sf.get("role"))
        _setenv("SNOWFLAKE_DEPLOYMENT_METHOD", sf.get("deployment_method"))

        fabric = raw.get("fabric") or {}
        _setenv("FABRIC_TENANT_ID",    fabric.get("tenant_id"))
        _setenv("FABRIC_CLIENT_ID",    fabric.get("client_id"))
        _setenv("FABRIC_CLIENT_SECRET",fabric.get("client_secret"))
        _setenv("FABRIC_WORKSPACE_ID", fabric.get("workspace_id"))

        databricks = raw.get("databricks") or {}
        _setenv("DATABRICKS_HOST", databricks.get("host"))
        _setenv("DATABRICKS_TOKEN", databricks.get("token"))
        _setenv("DATABRICKS_WAREHOUSE_ID", databricks.get("warehouse_id"))
        _setenv("DATABRICKS_CATALOG", databricks.get("catalog"))
        _setenv("DATABRICKS_SCHEMA", databricks.get("schema") or databricks.get("schema_name"))

        tel = raw.get("telemetry") or {}
        _setenv("TELEMETRY_ENABLED",       tel.get("enabled"))
        _setenv("TELEMETRY_OTLP_ENDPOINT", tel.get("otlp_endpoint"))
        _setenv("TELEMETRY_SERVICE_NAME",  tel.get("service_name"))

        # Clear cached settings so the injected env vars are picked up
        get_settings.cache_clear()

        logger.info(f"ExecutionEngine configured from YAML: {cfg_path}")
        return cls()

    def execute(
        self,
        source: Literal["snowflake", "fabric", "pbix"],
        target: Optional[Literal["snowflake", "fabric", "databricks"]] = None,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        config_path: Optional[Path] = None,
        deploy: bool = True,
        tag: Optional[str] = None,
        dry_run: bool = False,
        # Source-specific options
        dataset_id: Optional[str] = None,  # For Fabric source
        workspace_id: Optional[str] = None,  # For Fabric source
        pbix_path: Optional[str] = None,  # For PBIX source
        # Multi-user: account-scoped credential injection
        account_id: Optional[str] = None,  # Linked Account ID for per-user credentials
        config_dict: Optional[Dict[str, Any]] = None, # In-memory configuration override
        sync_mode: str = "copy",
        force: bool = False,
    ) -> RunSummary:
        """
        Execute the full 10-step pipeline.
        
        Args:
            source: Source connector type ("snowflake" or "fabric")
            target: Target connector type (optional)
            project_id: Stable project identifier override
            project_name: Override project name
            config_path: Path to config file (uses .env by default)
            deploy: Whether to deploy to target
            tag: Version tag for this run
            dry_run: Generate artifacts but don't deploy
            dataset_id: Fabric dataset ID (for Fabric source)
            workspace_id: Fabric workspace ID override
            
        Returns:
            RunSummary with execution results
        """
        # ── Reset per-run state ───────────────────────────────────────────────
        # This engine is a singleton; mutable instance fields MUST be cleared
        # before every new run so that a second model's run never reads stale
        # context/summary/step from the previous model's run.
        self._current_step = 0
        self._context = None
        self._summary = None
        # ─────────────────────────────────────────────────────────────────────

        # ── Initialise telemetry (idempotent — no-op if already configured) ─
        try:
            from semabridge.utils.telemetry import configure_telemetry
            tel_cfg = get_settings().telemetry
            configure_telemetry(
                service_name=tel_cfg.service_name,
                otlp_endpoint=tel_cfg.otlp_endpoint,
                enabled=tel_cfg.enabled,
                insecure=tel_cfg.insecure,
            )
        except Exception as _tel_exc:
            logger.debug(f"Telemetry init skipped: {_tel_exc}")

        try:
            # Step 1: Load and Validate Configuration
            config = self._step1_load_config(config_path, source, target, config_dict=config_dict)
            
            # Step 2: Initialize Identifiers
            context = self._step2_init_identifiers(
                config,
                source,
                target,
                project_id,
                project_name,
                dataset_id,
                config_path,
                sync_mode=sync_mode,
                config_dict=config_dict,
            )
            context.config_path = config_path
            context.config_payload = config_dict
            self._context = context
            self._summary = create_run_summary(
                project_id=context.project_id,
                run_id=context.run_id,
                source_type=source,
                target_type=target,
            )
            # Back-fill step 1 (config load) which ran before the summary existed.
            self._summary.add_step(
                step_number=1,
                step_name=STEP_NAMES.get(1, "Load Configuration"),
                status=StepStatus.SUCCESS,
                message="Configuration validated",
            )
            logger.info(
                "Stage 1: %s - %s",
                STEP_NAMES.get(1, "Load Configuration"),
                "Configuration validated",
            )
            
            # Step 3: Resolve Authentication
            # If an account_id is provided, inject its credentials into os.environ
            # before validation. This enables per-user credential isolation.
            self._account_env_ctx = None
            if account_id:
                try:
                    from semabridge.auth.account_credential_resolver import scoped_account_env
                    from semabridge.repository.account_repository import AccountRepository
                    from semabridge.repository.orm.session_factory import db_manager

                    session = db_manager.get_session_factory()()
                    repo = AccountRepository(session)
                    account = repo.get_account_by_id(account_id)
                    if account:
                        self._account_env_ctx = scoped_account_env(account, session)
                        self._account_env_ctx.__enter__()
                        context.account_id = account_id
                        logger.info(
                            "Account-scoped credentials injected for %s/%s",
                            account.connector_type,
                            account.identity_email or account.tag,
                        )
                        # Preserve project-scoped source/target namespaces (set from YAML
                        # in Step 1) before clearing the settings cache — the fresh
                        # get_settings() call would otherwise lose them.
                        _old_source = getattr(context.config, "source", None)
                        _old_target = getattr(context.config, "target", None)
                        # Force settings cache clear so pydantic re-reads env vars
                        get_settings.cache_clear()
                        context.config = get_settings()
                        # Re-attach project-scoped namespaces to the refreshed config.
                        if _old_source is not None:
                            object.__setattr__(context.config, "source", _old_source)
                        if _old_target is not None:
                            object.__setattr__(context.config, "target", _old_target)
                except Exception as acct_exc:
                    logger.warning(
                        "Account credential injection failed for %s: %s",
                        account_id,
                        acct_exc,
                    )

            self._step3_resolve_auth(context)
            
            # Step 4: Extract from Source
            source_format = self._step4_extract(
                context, dataset_id, workspace_id, pbix_path
            )
            context.source_format = source_format
            
            # Step 5: Validate and Parse Source Format
            self._step5_validate_source(context)
            
            # Step 6a: Extract Target Model for UPSERT (if applicable) - BEFORE Step 6
            if hasattr(context, 'sync_mode') and context.sync_mode == "upsert" and target:
                self._step6a_extract_target_for_upsert(context, target)
            
            # Step 6: Convert to Canonical SML (with target context if UPSERT)
            sml_model = self._step6_convert_to_sml(context, workspace_id, dataset_id, force=force)
            
            if config_path is not None and not context.mapping_overrides_applied:
                self._apply_mapping_overrides_from_config(sml_model, Path(config_path), config_payload=config_dict)
            context.sml_model = sml_model

            # Step 6b: Predict time-intelligence flag columns (Snowflake
            # target only) and upgrade anchor-dependent metrics'
            # sql_expression from Stage 1's safe CURRENT_DATE() fallback to
            # a flag-column reference wherever prediction says one will
            # exist -- BEFORE Step 7 persists this model, so a future
            # rollback to this snapshot doesn't resurrect the plain
            # fallback rendering. See targets/snowflake.py's
            # _step6b_predict_anchor_flag_columns for why this must run
            # here rather than only inside Step 9's real deploy.
            if target == "snowflake":
                self._step6b_predict_anchor_flag_columns(context)

            # Step 7: Persist Artifacts
            self._step7_persist_artifacts(context, tag)
            
            # Step 8: Convert to Target Format (Optional)
            if target and not dry_run:
                self._step8_convert_to_target(context)
            else:
                self._record_step(8, StepStatus.SKIPPED, "No target or dry run")
            
            # Step 9: Deploy to Target (Optional)
            if deploy and target and not dry_run:
                self._step9_deploy(context)
            else:
                self._record_step(9, StepStatus.SKIPPED, "Deployment skipped")
            
            # Step 10: Finalize Run
            return self._step10_finalize(context, RunStatus.SUCCESS)
            
        except Exception as e:
            logger.error(f"Execution failed at step {self._current_step}: {e}")
            
            # Determine status based on progress
            if self._current_step >= 9 and self._summary and any(
                s.status == StepStatus.SUCCESS for s in self._summary.steps_completed
                if s.step_number == 9
            ):
                status = RunStatus.PARTIAL
            else:
                status = RunStatus.FAILED
            
            if self._summary:
                self._summary.add_error(
                    self._current_step,
                    STEP_NAMES.get(self._current_step, "Unknown"),
                    e,
                    include_traceback=True,
                )
            
            return self._step10_finalize(
                self._context or self._create_fallback_context(),
                status
            )
        finally:
            # Always clean up the account-scoped env context
            if hasattr(self, '_account_env_ctx') and self._account_env_ctx is not None:
                try:
                    self._account_env_ctx.__exit__(None, None, None)
                except Exception as exc:
                    logger.debug("Error cleaning up account env context: %s", exc)
                self._account_env_ctx = None

    def _record_step(
        self,
        step_number: int,
        status: StepStatus,
        message: Optional[str] = None,
        artifact_ids: Optional[list] = None,
    ) -> None:
        """Record step result in summary."""
        self._current_step = step_number
        step_name = STEP_NAMES.get(step_number, f"Step {step_number}")
        status_text = str(getattr(status, "value", status)).upper()
        log_msg = f"Stage {step_number}: {step_name}"
        if message:
            log_msg += f" - {message}"

        if status_text == "FAILED":
            logger.error(log_msg)
        elif status_text == "SKIPPED":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        if self._summary:
            self._summary.add_step(
                step_number=step_number,
                step_name=step_name,
                status=status,
                message=message,
                artifact_ids=artifact_ids,
            )

    def _create_fallback_context(self) -> RunContext:
        """Create a fallback context for error handling."""
        return RunContext(
            project_id="unknown",
            run_id=str(uuid.uuid4()),
            config=get_settings(),
            start_time=time.time(),
            source_type="snowflake",
            behavior=ConnectorBehavior(),
        )

# ?? Method bindings ?? do not edit manually ??????????????????????????????????
from semabridge.core.engine import config as _cfg
ExecutionEngine._apply_mapping_overrides_from_config = staticmethod(_cfg._apply_mapping_overrides_from_config)
ExecutionEngine._step1_load_config                   = _cfg._step1_load_config
ExecutionEngine._step2_init_identifiers              = _cfg._step2_init_identifiers

from semabridge.core.engine import auth as _auth
ExecutionEngine._step3_resolve_auth          = _auth._step3_resolve_auth
ExecutionEngine._has_fabric_interactive_auth = _auth._has_fabric_interactive_auth
ExecutionEngine._has_fabric_identity_auth    = _auth._has_fabric_identity_auth

from semabridge.core.engine.extraction import base as _ext_base
from semabridge.core.engine.extraction import snowflake as _ext_sf
from semabridge.core.engine.extraction import fabric as _ext_fab
from semabridge.core.engine.extraction import pbix as _ext_pbix
ExecutionEngine._step4_extract                    = _ext_base._step4_extract
ExecutionEngine._step6a_extract_target_for_upsert = _ext_base._step6a_extract_target_for_upsert
ExecutionEngine._extract_snowflake                = _ext_sf._extract_snowflake
ExecutionEngine._extract_snowflake_scoped         = _ext_sf._extract_snowflake_scoped
ExecutionEngine._extract_snowflake_unscoped       = _ext_sf._extract_snowflake_unscoped
ExecutionEngine._resolve_snowflake_dataset_scope  = _ext_sf._resolve_snowflake_dataset_scope
ExecutionEngine._resolve_snowflake_include_tables = _ext_sf._resolve_snowflake_include_tables
ExecutionEngine._resolve_snowflake_parallelism    = _ext_sf._resolve_snowflake_parallelism
ExecutionEngine._extract_fabric                   = _ext_fab._extract_fabric
ExecutionEngine._extract_pbix                     = _ext_pbix._extract_pbix

from semabridge.core.engine.conversion import base as _conv_base
from semabridge.core.engine.conversion import snowflake as _conv_sf
from semabridge.core.engine.conversion import fabric as _conv_fab
from semabridge.core.engine.conversion import pbix as _conv_pbix
ExecutionEngine._step5_validate_source              = _conv_base._step5_validate_source
ExecutionEngine._step6_convert_to_sml               = _conv_base._step6_convert_to_sml
ExecutionEngine._normalize_relationships_for_target = _conv_base._normalize_relationships_for_target
ExecutionEngine._has_path                           = _conv_base._has_path
ExecutionEngine._select_strongest_relationship      = _conv_base._select_strongest_relationship
ExecutionEngine._convert_snowflake_to_sml           = _conv_sf._convert_snowflake_to_sml
ExecutionEngine._convert_fabric_to_sml              = _conv_fab._convert_fabric_to_sml
ExecutionEngine._convert_pbix_to_sml                = _conv_pbix._convert_pbix_to_sml

from semabridge.core.engine.targets import base as _tgt_base
from semabridge.core.engine.targets import fabric as _tgt_fab
from semabridge.core.engine.targets import snowflake as _tgt_sf
from semabridge.core.engine.targets import databricks as _tgt_db
ExecutionEngine._step8_convert_to_target      = _tgt_base._step8_convert_to_target
ExecutionEngine._convert_to_fabric_target     = _tgt_fab._convert_to_fabric_target
ExecutionEngine._convert_to_snowflake_target  = _tgt_sf._convert_to_snowflake_target
ExecutionEngine._convert_to_databricks_target = _tgt_db._convert_to_databricks_target
ExecutionEngine._step6b_predict_anchor_flag_columns = _tgt_sf._step6b_predict_anchor_flag_columns

from semabridge.core.engine.deployment import base as _dep_base
from semabridge.core.engine.deployment import fabric as _dep_fab
from semabridge.core.engine.deployment import snowflake as _dep_sf
from semabridge.core.engine.deployment import databricks as _dep_db
ExecutionEngine._step9_deploy                        = _dep_base._step9_deploy
ExecutionEngine._deploy_to_fabric                    = _dep_fab._deploy_to_fabric
ExecutionEngine._deploy_to_snowflake                 = _dep_sf._deploy_to_snowflake
ExecutionEngine._do_snowflake_deploy                 = _dep_sf._do_snowflake_deploy
ExecutionEngine._export_inferred_osi_artifacts       = _dep_sf._export_inferred_osi_artifacts
ExecutionEngine._should_sync_measures                = _dep_sf._should_sync_measures
ExecutionEngine._sync_fabric_measures                = _dep_sf._sync_fabric_measures
ExecutionEngine._deploy_to_databricks                = _dep_db._deploy_to_databricks
ExecutionEngine._raise_if_databricks_fallback_failed = _dep_db._raise_if_databricks_fallback_failed

from semabridge.core.engine import finalize as _fin
ExecutionEngine._step7_persist_artifacts = _fin._step7_persist_artifacts
ExecutionEngine._step10_finalize         = _fin._step10_finalize
# ?? End method bindings ??????????????????????????????????????????????????????
