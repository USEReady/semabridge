"""
CLI Execution Engine v1.

Orchestrates the mandatory 10-step execution flow where each CLI invocation
corresponds to exactly one Project and one Run.

Execution Flow:
1. Load and Validate Configuration
2. Initialize Identifiers (project_id, run_id)
3. Resolve Authentication
4. Extract from Source
5. Validate and Parse Source Format
6. Convert to Canonical SML
7. Persist Artifacts
8. Convert to Target Format (Optional)
9. Deploy to Target (Optional)
10. Finalize Run
"""

from __future__ import annotations

import time
import uuid
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Literal, Optional
import yaml
from pydantic import Field
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.core.settings import FabricConfig, Settings, get_settings
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

import contextvars

logger = get_logger(__name__)

# Thread-safe context variables for observability.
# These are automatically propagated to child threads when using
# contextvars.copy_context().run(fn, ...) instead of bare fn().
_current_run_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_run_id", default=""
)
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_user_id", default=""
)


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


class AuthenticationError(Exception):
    """Raised when authentication resolution fails."""
    pass


class ExtractionError(Exception):
    """Raised when source extraction fails."""
    pass


class SourceFormatError(Exception):
    """Raised when source format validation fails."""
    pass


class ConversionError(Exception):
    """Raised when SML conversion fails."""
    pass


class PersistenceError(Exception):
    """Raised when artifact persistence fails."""
    pass


class DeploymentError(Exception):
    """Raised when target deployment fails."""
    pass


@dataclass
class RunContext:
    """
    Execution context propagated through all steps.
    
    Generated at Step 2 and used throughout execution.

    Thread-safety: The ``scoped_*_config`` fields hold per-run Pydantic config
    objects built by ``auth.credential_builder``.  They live only in this
    RunContext instance (on the calling thread's stack) and are never shared
    with other threads or stored in global state.
    """
    project_id: str
    run_id: str
    config: Settings
    start_time: float
    source_type: Literal["snowflake", "fabric", "pbix"]
    target_type: Optional[Literal["snowflake", "fabric", "databricks"]] = None
    behavior: ConnectorBehavior = field(default_factory=ConnectorBehavior)
    account_id: Optional[str] = None  # Linked Account for multi-user credential scoping

    # Per-run scoped config objects — built by credential_builder, thread-safe.
    # When set, extractors/publishers use these instead of global settings.
    scoped_snowflake_config: Optional["SnowflakeConfig"] = None
    scoped_fabric_config: Optional["FabricConfig"] = None
    scoped_fabric_token: Optional[str] = None
    scoped_databricks_config: Optional["DatabricksConfig"] = None
    connection_tag: Optional[str] = None  # Resolved identity tag (e.g. 'Production', 'Dev')
    
    # Artifacts accumulated during execution
    source_format: Optional[SourceFormat] = None
    osi_model: Optional[OSIModel] = None  # OSI intermediate — populated after Step 6
    sml_model: Optional[SMLModel] = None
    source_artifact_id: Optional[str] = None
    sml_snapshot_id: Optional[str] = None
    target_artifact_path: Optional[str] = None
    routing_summary: Optional[dict[str, Any]] = None


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

    @staticmethod
    def _apply_mapping_overrides_from_config(sml_model: SMLModel, config_path: Path) -> None:
        """Apply user-edited mapping overrides from config to SML names before deploy."""
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
                metric_name = source_path[len("metrics."):]
                for metric in sml_model.metrics:
                    if str(metric.unique_name) == metric_name:
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

    # -------------------------------------------------------------------------
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
        # In-memory config to avoid disk I/O race conditions during concurrent API syncs
        config_dict: Optional[Dict[str, Any]] = None,
    ) -> RunSummary:
        """
        Execute the full 10-step pipeline.
        
        Args:
            source: Source connector type ("snowflake" or "fabric")
            target: Target connector type (optional)
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
                config, source, target, project_name, dataset_id, config_path
            )
            self._context = context

            # Set thread-safe context variables for observability.
            _current_run_id.set(context.run_id)
            if account_id:
                _current_user_id.set(str(account_id))
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
            # If an account_id is provided, build scoped config objects
            # using the credential_builder (thread-safe, no os.environ mutation).
            if account_id:
                try:
                    from semabridge.auth.credential_builder import (
                        build_databricks_config,
                        build_fabric_config,
                        build_snowflake_config,
                    )
                    from semabridge.repository.account_repository import AccountRepository
                    from semabridge.repository.orm.session_factory import db_manager

                    with db_manager.get_session() as _cred_session:
                        repo = AccountRepository(_cred_session)
                        account = repo.get_account_by_id(account_id)
                        if account:
                            context.account_id = account_id
                            connector = account.connector_type.upper()

                            # Build source config
                            if connector == "SNOWFLAKE" or source == "snowflake":
                                context.scoped_snowflake_config = build_snowflake_config(
                                    account, _cred_session, context.config.snowflake
                                )
                            elif connector == "FABRIC" or source in ("fabric", "pbix"):
                                cfg, token = build_fabric_config(
                                    account, _cred_session, context.config.fabric
                                )
                                context.scoped_fabric_config = cfg
                                context.scoped_fabric_token = token

                            # Build target config if it differs from source
                            if target == "databricks" and connector != "DATABRICKS":
                                # Target is Databricks but account is for source —
                                # Databricks config will be resolved separately if needed.
                                pass
                            elif connector == "DATABRICKS" or target == "databricks":
                                context.scoped_databricks_config = build_databricks_config(
                                    account, _cred_session, context.config.databricks
                                )

                            logger.info(
                                "[RunID: %s] Credential objects built for account %s/%s — "
                                "no os.environ mutation.",
                                context.run_id,
                                connector,
                                account.identity_email or account.tag,
                            )
                except Exception as acct_exc:
                    logger.warning(
                        "Credential build failed for account %s: %s",
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
            
            # Step 6: Convert to Canonical SML
            sml_model = self._step6_convert_to_sml(context, workspace_id, dataset_id)
            self._apply_mapping_overrides_from_config(sml_model, Path(config_path))
            context.sml_model = sml_model
            
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
    
    # =========================================================================
    # Step 1: Load and Validate Configuration
    # =========================================================================
    
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

            if config_dict is not None:
                raw_config = config_dict
            elif config_path and Path(config_path).exists():
                try:
                    raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
                except Exception as raw_exc:
                    logger.warning("Could not parse config at %s for project-scoped settings: %s", config_path, raw_exc)
                    raw_config = {}
            else:
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
    
    # =========================================================================
    # Step 2: Initialize Identifiers
    # =========================================================================
    
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

    def _resolve_identity(
        self,
        connector_type: str,
        identity_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Optional[Any]:
        """
        Resolve an account identity with smart fallback logic.
        
        Resolution order:
        1. Exact match by identity_id (UUID)
        2. Match by connection_tag recorded for this project in the DB
        3. Match by owner's "Default" account for this connector type
        4. Match by "Single Active Account" of this type
        """
        from sqlalchemy import select
        from semabridge.repository.orm.models import Account, Project
        from semabridge.repository.orm.session_factory import db_manager

        connector_type = connector_type.upper()
        with db_manager.get_session() as session:
            # 1. Exact UUID match (if provided)
            if identity_id:
                account = session.get(Account, identity_id)
                if account and account.connector_type.upper() == connector_type:
                    return account

            # 2. Match by recording connection_tag (Tag Memory)
            recorded_tag = None
            if project_id:
                project = session.get(Project, project_id)
                if project and project.connection_tag:
                    recorded_tag = project.connection_tag
            
            if recorded_tag:
                account = session.execute(
                    select(Account).where(
                        Account.connector_type == connector_type,
                        Account.tag == recorded_tag
                    )
                ).scalars().first()
                if account:
                    logger.info(
                        "Resumed %s identity via Tag Memory: using '%s' (ID: %s)",
                        connector_type, recorded_tag, account.id[:8]
                    )
                    return account

            # 3. Match by "Default" or "Unique" account for current user
            accounts = session.execute(
                select(Account).where(Account.connector_type == connector_type)
            ).scalars().all()
            
            if not accounts:
                return None

            # Look for explicit default
            for acc in accounts:
                if getattr(acc, "is_default", False):
                    logger.info(
                        "Stale %s ID in config. Falling back to default account '%s'.",
                        connector_type, acc.tag
                    )
                    return acc
            
            # If only one account exists, use it (least ambiguous fallback)
            if len(accounts) == 1:
                acc = accounts[0]
                logger.info(
                    "Stale %s ID in config. Using the only available account '%s'.",
                    connector_type, acc.tag
                )
                return acc

        return None

    # =========================================================================
    # Step 3: Resolve Authentication
    # =========================================================================
    
    def _step3_resolve_auth(self, context: RunContext) -> None:
        """
        Step 3: Resolve authentication.
        
        - Resolve credentials from DB identities (with Fallback) or Environment
        - Injects scoped config objects into RunContext for strict isolation
        - Updates Project connection_tag for resilient recovery
        """
        self._current_step = 3
        logger.info("Step 3: Resolving authentication")
        
        from semabridge.auth.credential_builder import (
            build_databricks_config,
            build_fabric_config,
            build_snowflake_config,
        )
        from semabridge.repository.orm.session_factory import db_manager

        config = context.config
        missing = []
        auth_sources: list[str] = []
        
        # --- Source Authentication ---
        if context.source_type == "fabric":
            if context.behavior.features.offline_mode:
                auth_sources.append("OFFLINE")
            else:
                identity_id = str(getattr(getattr(config, "source", None), "identity_id", "") or "").strip()
                account = self._resolve_identity("FABRIC", identity_id, context.project_id)
                
                if account:
                    context.connection_tag = account.tag
                    with db_manager.get_session() as session:
                        cfg, token = build_fabric_config(account, session, config.fabric)
                        context.scoped_fabric_config = cfg
                        context.scoped_fabric_token = token
                        # Record the tag for future fallback resiliency
                        self.db_manager.ensure_project(
                            project_id=context.project_id,
                            name=context.project_id, # Keep existing name
                            workspace_id=cfg.workspace_id,
                            adapter="fabric",
                            connection_tag=account.tag
                        )
                    auth_sources.append(f"Identity:{account.tag}")
                elif config.validate_fabric():
                    auth_sources.append("ENV")
                elif self._has_fabric_interactive_auth():
                    auth_sources.append("UI token")
                else:
                    missing.append("Fabric credentials (FABRIC_*)")
        
        elif context.source_type == "snowflake":
            identity_id = str(getattr(getattr(config, "source", None), "identity_id", "") or "").strip()
            account = self._resolve_identity("SNOWFLAKE", identity_id, context.project_id)
            if account:
                context.connection_tag = account.tag
                with db_manager.get_session() as session:
                    context.scoped_snowflake_config = build_snowflake_config(account, session, config.snowflake)
                    self.db_manager.ensure_project(
                        project_id=context.project_id,
                        name=context.project_id,
                        workspace_id="", # Snowflake doesn't use workspace_id in the same way
                        adapter="snowflake",
                        connection_tag=account.tag
                    )
                auth_sources.append(f"Identity:{account.tag}")
            elif config.validate_snowflake():
                auth_sources.append("ENV")
            else:
                missing.append("Snowflake credentials (SNOWFLAKE_*)")

        # --- Target Authentication ---
        if context.target_type == "fabric" and not context.scoped_fabric_config:
            identity_id = str(getattr(getattr(config, "target", None), "identity_id", "") or "").strip()
            account = self._resolve_identity("FABRIC", identity_id, context.project_id)
            if account:
                context.connection_tag = account.tag
                with db_manager.get_session() as session:
                    cfg, token = build_fabric_config(account, session, config.fabric)
                    context.scoped_fabric_config = cfg
                    context.scoped_fabric_token = token
                    self.db_manager.ensure_project(
                        project_id=context.project_id,
                        name=context.project_id,
                        workspace_id=cfg.workspace_id,
                        adapter=context.source_type, # Preserve source adapter
                        connection_tag=account.tag
                    )
                auth_sources.append(f"Identity:{account.tag}")
            elif config.validate_fabric():
                auth_sources.append("ENV")
            else:
                missing.append("Fabric credentials (FABRIC_*)")
                
        elif context.target_type == "databricks":
            identity_id = str(getattr(getattr(config, "target", None), "identity_id", "") or "").strip()
            account = self._resolve_identity("DATABRICKS", identity_id, context.project_id)
            if account:
                context.connection_tag = account.tag
                with db_manager.get_session() as session:
                    context.scoped_databricks_config = build_databricks_config(account, session, config.databricks)
                    self.db_manager.ensure_project(
                        project_id=context.project_id,
                        name=context.project_id,
                        workspace_id="",
                        adapter=context.source_type,
                        connection_tag=account.tag
                    )
                auth_sources.append(f"Identity:{account.tag}")
            elif config.validate_databricks():
                auth_sources.append("ENV")
            else:
                missing.append("Databricks credentials (DATABRICKS_*)")

        elif context.target_type == "snowflake" and not context.scoped_snowflake_config:
            identity_id = str(getattr(getattr(config, "target", None), "identity_id", "") or "").strip()
            account = self._resolve_identity("SNOWFLAKE", identity_id, context.project_id)
            if account:
                context.connection_tag = account.tag
                with db_manager.get_session() as session:
                    context.scoped_snowflake_config = build_snowflake_config(account, session, config.snowflake)
                    self.db_manager.ensure_project(
                        project_id=context.project_id,
                        name=context.project_id,
                        workspace_id="",
                        adapter=context.source_type,
                        connection_tag=account.tag
                    )
                auth_sources.append(f"Identity:{account.tag}")
            elif config.validate_snowflake():
                auth_sources.append("ENV")
            else:
                missing.append("Snowflake credentials (SNOWFLAKE_*)")

        if missing:
            msg = f"Missing authentication: {', '.join(missing)}"
            self._record_step(3, StepStatus.FAILED, msg)
            raise AuthenticationError(msg)

        source_label = "/".join(sorted(set(auth_sources))) or "Resolved"
        self._record_step(3, StepStatus.SUCCESS, f"Authentication resolved from {source_label}")
        
        if missing:
            msg = f"Missing authentication: {', '.join(missing)}"
            self._record_step(3, StepStatus.FAILED, msg)
            raise AuthenticationError(msg)

        if auth_sources:
            source_label = "/".join(sorted(set(auth_sources)))
            self._record_step(3, StepStatus.SUCCESS, f"Authentication resolved from {source_label}")
        else:
            self._record_step(3, StepStatus.SUCCESS, "Authentication resolved")

    def _has_fabric_interactive_auth(self) -> bool:
        """Return True when a valid Fabric interactive token exists in credential store."""
        try:
            from semabridge.repository.credential_manager import CredentialManager

            cm = CredentialManager()
            return cm.get_fabric_auth_method() == "interactive" and cm.has_valid_token()
        except Exception as exc:
            logger.warning("_has_fabric_interactive_auth: credential lookup failed: %s", exc)
            return False

    def _has_fabric_identity_auth(self, identity_id: str) -> bool:
        """Return True when a selected Fabric identity is resolvable from the DB."""
        if not identity_id:
            return False
        try:
            from semabridge.api.services.connection_domain_service import _resolve_fabric_access_token

            token = _resolve_fabric_access_token(None, identity_id)
            return bool(token)
        except Exception as exc:
            logger.warning("_has_fabric_identity_auth: identity lookup failed for %s: %s", identity_id, exc)
            return False

    def _has_snowflake_identity_auth(self, identity_id: str) -> bool:
        """Return True when a linked Snowflake Account row has valid credentials."""
        if not identity_id:
            return False
        try:
            from sqlalchemy import select
            from semabridge.repository.orm.models import Account
            from semabridge.repository.orm.session_factory import db_manager

            with db_manager.get_session() as session:
                account = session.execute(
                    select(Account).where(
                        Account.connector_type == "SNOWFLAKE",
                        Account.id == identity_id,
                    )
                ).scalars().first()
                return bool(account and account.encrypted_token)
        except Exception as exc:
            logger.warning("_has_snowflake_identity_auth: lookup failed for %s: %s", identity_id, exc)
            return False

    def _has_databricks_identity_auth(self, identity_id: str) -> bool:
        """Return True when a linked Databricks Account row has valid credentials."""
        if not identity_id:
            return False
        try:
            from sqlalchemy import select
            from semabridge.repository.orm.models import Account
            from semabridge.repository.orm.session_factory import db_manager

            with db_manager.get_session() as session:
                account = session.execute(
                    select(Account).where(
                        Account.connector_type == "DATABRICKS",
                        Account.id == identity_id,
                    )
                ).scalars().first()
                return bool(account and account.encrypted_token)
        except Exception as exc:
            logger.warning("_has_databricks_identity_auth: lookup failed for %s: %s", identity_id, exc)
            return False

    def _extract_snowflake_scoped(
        self,
        context: RunContext,
        dataset_id: Optional[str],
        identity_id: str,
    ) -> SourceFormat:
        """Run Snowflake extraction under scoped account credentials.

        Looks up the Account row by ``identity_id``, builds a scoped
        SnowflakeConfig via ``credential_builder`` (thread-safe), and
        delegates to the standard extraction pipeline.

        Args:
            context: Current run context.
            dataset_id: Optional dataset scope.
            identity_id: Account row UUID.

        Returns:
            Extracted SourceFormat.

        Raises:
            ExtractionError: When identity cannot be resolved.
        """
        from sqlalchemy import select
        from semabridge.repository.orm.models import Account
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.auth.credential_builder import build_snowflake_config

        try:
            with db_manager.get_session() as session:
                account = session.execute(
                    select(Account).where(
                        Account.connector_type == "SNOWFLAKE",
                        Account.id == identity_id,
                    )
                ).scalars().first()

                if not account:
                    raise ExtractionError(
                        f"No Snowflake account found for identity_id '{identity_id}'. "
                        "Please link this account in the Connections panel."
                    )

                # Build scoped config (thread-safe — no os.environ mutation)
                scoped_sf_config = build_snowflake_config(
                    account, session, context.config.snowflake
                )
                logger.info(
                    "Snowflake extraction scoped to account %s (%s) via credential_builder",
                    account.tag, identity_id,
                )
                # Inject scoped config into context and delegate
                context.scoped_snowflake_config = scoped_sf_config
                return self._extract_snowflake_unscoped(context, dataset_id)
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(
                f"Failed to resolve Snowflake credentials for identity '{identity_id}': {exc}"
            ) from exc
    # =========================================================================
    # Step 4: Extract from Source
    # =========================================================================
    
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
            elif context.source_type == "fabric":
                return self._extract_fabric(context, dataset_id, workspace_id)
            elif context.source_type == "pbix":
                return self._extract_pbix(context, pbix_path)
            else:
                raise ExtractionError(f"Unknown source type: {context.source_type}")
                
        except Exception as e:
            self._record_step(4, StepStatus.FAILED, str(e))
            raise ExtractionError(f"Extraction failed: {e}") from e
    
    def _extract_snowflake(
        self,
        context: RunContext,
        dataset_id: Optional[str] = None,
    ) -> SourceFormat:
        """Extract from Snowflake.

        Credential resolution order:
        1. ``context.scoped_snowflake_config`` — built by credential_builder
           (thread-safe, set when account_id was provided in execute()).
        2. ``identity_id`` in source config — legacy per-account scoping via
           ``_extract_snowflake_scoped`` (uses credential_builder internally).
        3. ``context.config.snowflake`` — global settings from .env (fallback).
        """
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.utils.cache import MetadataCache
        
        config = context.config
        # Prefer scoped config (thread-safe) over global settings
        sf_config = context.scoped_snowflake_config or config.snowflake

        source_config = getattr(config, "source", None)
        identity_id: str = str(getattr(source_config, "identity_id", "") or "").strip()

        # Resolve per-account credentials via identity_id (legacy path)
        # Only if no scoped config was already built by execute()
        if identity_id and not context.scoped_snowflake_config:
            return self._extract_snowflake_scoped(context, dataset_id, identity_id)

        cache = MetadataCache(config.model.cache_dir) if config.model.cache_enabled else None

        # BACKUP (old behavior): serial extraction + only env-based include list
        # extractor = SnowflakeExtractor(
        #     config=config.snowflake,
        #     cache=cache,
        #     exclude_tables=config.model.excluded_table_list,
        #     include_tables=config.model.included_table_list,
        # )
        # metadata = extractor.extract_all()

        include_tables, include_source = self._resolve_snowflake_include_tables(context)
        semantic_view_name: Optional[str] = None
        semantic_view_ddl: Optional[str] = None

        # If a specific model was requested for this run, scope extraction to that model.
        # For Snowflake this may be a semantic view name or a table name.
        if dataset_id:
            scope_probe = SnowflakeExtractor(
                config=sf_config,
                cache=cache,
                exclude_tables=config.model.excluded_table_list,
                include_tables=None,
            )
            scoped_tables, semantic_view_name, semantic_view_ddl = self._resolve_snowflake_dataset_scope(
                dataset_id,
                scope_probe,
            )
            if scoped_tables:
                include_tables = scoped_tables
                include_source = "dataset_id.semantic_view" if semantic_view_name else "dataset_id.table"

        parallel_enabled, max_workers = self._resolve_snowflake_parallelism(context)

        logger.info(
            "Snowflake extraction plan: include_tables=%s source=%s parallel=%s workers=%s",
            len(include_tables) if include_tables else 0,
            include_source or "none",
            parallel_enabled,
            max_workers,
        )
        
        extractor = SnowflakeExtractor(
            config=sf_config,
            cache=cache,
            exclude_tables=config.model.excluded_table_list,
            include_tables=include_tables,
        )
        
        metadata = extractor.extract_all(
            parallel=parallel_enabled,
            max_workers=max_workers,
        )

        # BACKUP (old behavior): no compatibility retry when include filter matched zero tables
        # semantic_data = extractor.read_semantic_tables()
        # metadata["semantic_tables"] = semantic_data

        # Backward compatibility guard:
        # if include list came from project YAML/UI and resolves to zero tables,
        # retry without include filter to preserve legacy "full-schema" behavior.
        strict_sources = {
            "source.models",
            "selection.model_ids",
            "dataset_id.semantic_view",
            "dataset_id.table",
        }
        if include_tables and not metadata.get("tables"):
            if include_source in strict_sources:
                raise ExtractionError(
                    f"Include filter from {include_source} matched 0 tables for '{dataset_id}'. "
                    "Refine the selected model or verify semantic view/table names."
                )
            if include_source != "model.include_tables":
                logger.warning(
                    "Include filter from %s matched 0 tables; retrying full schema extraction for compatibility",
                    include_source,
                )
                extractor = SnowflakeExtractor(
                    config=sf_config,
                    cache=cache,
                    exclude_tables=config.model.excluded_table_list,
                    include_tables=None,
                )
                metadata = extractor.extract_all(
                    parallel=parallel_enabled,
                    max_workers=max_workers,
                )

        semantic_data = extractor.read_semantic_tables()
        metadata["semantic_tables"] = semantic_data
        
        source_format = from_snowflake_metadata(
            project_id=context.project_id,
            run_id=context.run_id,
            metadata=metadata,
            semantic_view_name=semantic_view_name,
            semantic_view_ddl=semantic_view_ddl,
        )
        
        table_count = len(source_format.tables)
        self._record_step(4, StepStatus.SUCCESS, f"Extracted {table_count} tables")
        
        return source_format

    def _extract_snowflake_unscoped(
        self,
        context: RunContext,
        dataset_id: Optional[str] = None,
    ) -> SourceFormat:
        """Run Snowflake extraction without identity_id resolution.

        Called by ``_extract_snowflake_scoped`` after a scoped SnowflakeConfig
        has been set on context.  Delegates to the main extraction body but
        skips the identity_id check to prevent infinite recursion.
        """
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.utils.cache import MetadataCache

        config = context.config
        sf_config = context.scoped_snowflake_config or config.snowflake
        cache = MetadataCache(config.model.cache_dir) if config.model.cache_enabled else None

        include_tables, include_source = self._resolve_snowflake_include_tables(context)
        semantic_view_name: Optional[str] = None
        semantic_view_ddl: Optional[str] = None

        if dataset_id:
            scope_probe = SnowflakeExtractor(
                config=sf_config,
                cache=cache,
                exclude_tables=config.model.excluded_table_list,
                include_tables=None,
            )
            scoped_tables, semantic_view_name, semantic_view_ddl = self._resolve_snowflake_dataset_scope(
                dataset_id,
                scope_probe,
            )
            if scoped_tables:
                include_tables = scoped_tables
                include_source = "dataset_id.semantic_view" if semantic_view_name else "dataset_id.table"

        parallel_enabled, max_workers = self._resolve_snowflake_parallelism(context)

        extractor = SnowflakeExtractor(
            config=sf_config,
            cache=cache,
            exclude_tables=config.model.excluded_table_list,
            include_tables=include_tables,
        )
        metadata = extractor.extract_all(
            parallel=parallel_enabled,
            max_workers=max_workers,
        )

        strict_sources = {
            "source.models",
            "selection.model_ids",
            "dataset_id.semantic_view",
            "dataset_id.table",
        }
        if include_tables and not metadata.get("tables"):
            if include_source in strict_sources:
                raise ExtractionError(
                    f"Include filter from {include_source} matched 0 tables for '{dataset_id}'. "
                    "Refine the selected model or verify semantic view/table names."
                )
            if include_source != "model.include_tables":
                extractor = SnowflakeExtractor(
                    config=sf_config,
                    cache=cache,
                    exclude_tables=config.model.excluded_table_list,
                    include_tables=None,
                )
                metadata = extractor.extract_all(
                    parallel=parallel_enabled,
                    max_workers=max_workers,
                )

        semantic_data = extractor.read_semantic_tables()
        metadata["semantic_tables"] = semantic_data

        source_format = from_snowflake_metadata(
            project_id=context.project_id,
            run_id=context.run_id,
            metadata=metadata,
            semantic_view_name=semantic_view_name,
            semantic_view_ddl=semantic_view_ddl,
        )

        table_count = len(source_format.tables)
        self._record_step(4, StepStatus.SUCCESS, f"Extracted {table_count} tables (scoped)")

        return source_format
    def _resolve_snowflake_dataset_scope(
        self,
        dataset_id: str,
        extractor: Any,
    ) -> tuple[Optional[list[str]], Optional[str], Optional[str]]:
        """Resolve a run-scoped Snowflake dataset selector to include tables.

        Tries semantic-view DDL parsing first, then falls back to table-name scope.
        """
        selector = str(dataset_id or "").strip()
        if not selector:
            return None, None, None

        try:
            from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter

            ddl = extractor.extract_semantic_view_ddl(selector)
            converter = SemanticViewToOSIConverter()
            table_map = converter._parse_tables_clause(ddl)
            include_tables = sorted(
                {
                    v.get("table_name", "").upper()
                    for v in table_map.values()
                    if v.get("table_name")
                }
            )
            logger.info(
                "Resolved Snowflake model scope from semantic view '%s': %s base table(s)",
                selector,
                len(include_tables),
            )
            return include_tables or None, selector, ddl
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Dataset selector '%s' not resolved as semantic view (%s); using table-name scope fallback",
                selector,
                exc,
            )

        return [selector.upper()], None, None

    def _resolve_snowflake_include_tables(self, context: RunContext) -> tuple[Optional[list[str]], Optional[str]]:
        """Resolve include-tables from env/settings first, then project YAML/UI config.

        Priority order:
        1) MODEL_INCLUDE_TABLES / settings.model.include_tables
        2) source.include_tables
        3) source.tables
        4) source.models (UI-selected tables/models)
        5) selection.model_ids
        """
        include_from_model = context.config.model.included_table_list
        if include_from_model:
            return include_from_model, "model.include_tables"

        config_path = get_project_file_path("semabridge.yaml")
        if not config_path.exists():
            return None, None

        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not parse semabridge.yaml for include tables: %s", exc)
            return None, None

        source_cfg = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
        if str(source_cfg.get("type") or "").strip().lower() != "snowflake":
            return None, None

        def _normalize_list(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, str):
                raw = [v.strip() for v in value.split(",") if v and v.strip()]
            elif isinstance(value, list):
                raw = [str(v).strip() for v in value if str(v).strip()]
            else:
                raw = []
            seen: set[str] = set()
            normalized: list[str] = []
            for item in raw:
                key = item.upper()
                if key not in seen:
                    seen.add(key)
                    normalized.append(key)
            return normalized

        candidates: list[tuple[str, Any]] = [
            ("source.include_tables", source_cfg.get("include_tables")),
            ("source.tables", source_cfg.get("tables")),
            ("source.models", source_cfg.get("models")),
            ("selection.model_ids", (cfg.get("selection") or {}).get("model_ids") if isinstance(cfg.get("selection"), dict) else None),
        ]

        for source_name, source_value in candidates:
            include_tables = _normalize_list(source_value)
            if include_tables:
                return include_tables, source_name

        return None, None

    def _resolve_snowflake_parallelism(self, context: RunContext) -> tuple[bool, int]:
        """Resolve Snowflake extraction parallel settings with env overrides.

        Environment overrides (optional):
        - SNOWFLAKE_EXTRACT_PARALLEL=true|false
        - SNOWFLAKE_EXTRACT_MAX_WORKERS=<int>
        """
        cpu_count = os.cpu_count() or 4
        default_workers = max(2, min(16, cpu_count))

        configured_workers = context.config.concurrency.max_workers
        max_workers = configured_workers if configured_workers > 0 else default_workers

        env_workers = os.getenv("SNOWFLAKE_EXTRACT_MAX_WORKERS", "").strip()
        if env_workers.isdigit():
            max_workers = int(env_workers)

        max_workers = max(1, min(max_workers, 32))

        env_parallel = os.getenv("SNOWFLAKE_EXTRACT_PARALLEL", "").strip().lower()
        if env_parallel in {"0", "false", "no", "off"}:
            parallel_enabled = False
        elif env_parallel in {"1", "true", "yes", "on"}:
            parallel_enabled = True
        else:
            # Default to parallel extraction for Snowflake to reduce long-running sync times.
            parallel_enabled = True

        return parallel_enabled, max_workers
    
    def _extract_fabric(
        self,
        context: RunContext,
        dataset_id: Optional[str],
        workspace_id: Optional[str],
    ) -> SourceFormat:
        """Extract from Fabric.

        Credential resolution order:
        1. context.scoped_fabric_config — built by credential_builder (thread-safe).
        2. identity_id in source config — resolved via _resolve_fabric_access_token.
        3. Global config.fabric (fallback for CLI usage).
        """
        from semabridge.connectors.fabric_extractor import FabricExtractor
        
        config = context.config
        fabric_cfg = context.scoped_fabric_config or config.fabric
        interactive_token: Optional[str] = context.scoped_fabric_token
        source_config = getattr(config, "source", None)
        ws_id = workspace_id or fabric_cfg.workspace_id
        stored_workspace_id: str = ""
        identity_id: str = str(getattr(source_config, "identity_id", "") or "").strip()

        if context.behavior.features.offline_mode:
            offline_path = Path(context.behavior.features.offline_fabric_model_path)
            if not offline_path.exists():
                raise ExtractionError(
                    f"offline_mode enabled but file not found: {offline_path}"
                )

            logger.info(
                "Step 4: Running in OFFLINE mode (skipping Fabric API) using %s",
                offline_path,
            )

            with open(offline_path, "r", encoding="utf-8") as f:
                tmsl = json.load(f)

            # Accept both full TMSL and flattened model payloads.
            if isinstance(tmsl, dict) and "model" not in tmsl and "tables" in tmsl:
                tmsl = {"model": tmsl}

            resolved_dataset_id = dataset_id or context.project_id
            row_counts: dict[str, int] = {}

            source_format = from_fabric_tmsl(
                project_id=context.project_id,
                run_id=context.run_id,
                tmsl=tmsl,
                workspace_id=workspace_id or "",
                dataset_id=resolved_dataset_id,
                row_counts=row_counts,
            )

            table_count = len(tmsl.get("model", {}).get("tables", []))
            self._record_step(4, StepStatus.SUCCESS, f"OFFLINE extract loaded {table_count} tables")
            return source_format

        interactive_token = context.scoped_fabric_token
        fabric_cfg = context.scoped_fabric_config or config.fabric
        identity_id = str(getattr(getattr(config, "source", None), "identity_id", "") or "").strip()
        ws_id = workspace_id or (fabric_cfg.workspace_id if fabric_cfg else None) or ""

        # Only proceed with credential resolution if NOT already scoped in context
        if not fabric_cfg or not interactive_token:
            try:
                from semabridge.repository.credential_manager import CredentialManager
                cm = CredentialManager()
                
                # If identity_id is present but not scoped, try resolving it (legacy/CLI fallback)
                if identity_id and not context.scoped_fabric_config:
                    from semabridge.api.services.connection_domain_service import _resolve_fabric_access_token
                    interactive_token = _resolve_fabric_access_token(None, identity_id)
            except Exception as exc:
                logger.warning("_extract_fabric: credential lookup failed, falling back to env auth: %s", exc)
        
        if not dataset_id:
            raise ExtractionError("dataset_id is required for Fabric source")

        guid_pattern = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

        def _is_guid(value: str) -> bool:
            return bool(value and re.match(guid_pattern, value))

        requested_ws = str(ws_id or "").strip()
        configured_ws = str(fabric_cfg.workspace_id or "").strip()
        stored_ws = str(stored_workspace_id or "").strip()

        # UI aliases (for example, semabridge-local) are not accepted by Fabric API.
        # Prefer known GUIDs from credentials or environment-backed config.
        if requested_ws and not _is_guid(requested_ws):
            if _is_guid(stored_ws):
                logger.info(
                    "Fabric workspace '%s' appears to be an alias; using credential workspace GUID '%s'",
                    requested_ws,
                    stored_ws,
                )
                requested_ws = stored_ws
            elif _is_guid(configured_ws):
                logger.info(
                    "Fabric workspace '%s' appears to be an alias; using configured workspace GUID '%s'",
                    requested_ws,
                    configured_ws,
                )
                requested_ws = configured_ws

        ws_id = requested_ws or configured_ws or stored_ws

        # Build a local config with the resolved workspace_id (avoid mutating shared config)
        if ws_id and fabric_cfg.workspace_id != ws_id:
            fabric_cfg = FabricConfig(
                tenant_id=fabric_cfg.tenant_id,
                client_id=fabric_cfg.client_id,
                client_secret=fabric_cfg.client_secret.get_secret_value() if fabric_cfg.client_secret else None,
                workspace_id=ws_id,
                api_base_url=fabric_cfg.api_base_url,
                power_bi_api_url=fabric_cfg.power_bi_api_url,
            )
        
        # Refresh token just-in-time at Stage 4 for the source identity.
        # context.scoped_fabric_token was resolved at Stage 3. In long-queued
        # batch runs (e.g. scheduled jobs) that token may have expired by the
        # time Stage 4 executes. Re-resolving here mirrors the Stage 9 pattern
        # and guarantees freshness for the Fabric extraction API call.
        if identity_id:
            try:
                from semabridge.api.services.connection_domain_service import _resolve_fabric_access_token
                jit_token = _resolve_fabric_access_token(None, identity_id)
                if jit_token:
                    interactive_token = jit_token
                    logger.info(
                        "_extract_fabric: resolved JIT fresh token for source identity %s",
                        identity_id,
                    )
            except Exception as exc:
                logger.warning(
                    "_extract_fabric: JIT token resolution failed for %s, "
                    "using scoped token from Stage 3: %s",
                    identity_id, exc,
                )

        extractor = FabricExtractor(fabric_cfg)
        if interactive_token:
            extractor._access_token = interactive_token
            extractor._token_expires_at = time.time() + 1800

        resolved_dataset_id = extractor.resolve_model_id(dataset_id)

        tmsl = extractor.get_model_definition(resolved_dataset_id)
        row_counts = extractor.get_table_row_counts(resolved_dataset_id)
        
        source_format = from_fabric_tmsl(
            project_id=context.project_id,
            run_id=context.run_id,
            tmsl=tmsl,
            workspace_id=ws_id,
            dataset_id=resolved_dataset_id,
            row_counts=row_counts,
        )
        
        self._record_step(4, StepStatus.SUCCESS, f"Extracted TMSL definition")
        
        return source_format
    
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
        )

        table_count = len(tmsl.get("model", {}).get("tables", []))
        self._record_step(
            4, StepStatus.SUCCESS,
            f"Extracted {table_count} tables from {p.name}"
        )

        return source_format

    # =========================================================================
    # Step 5: Validate and Parse Source Format
    # =========================================================================
    
    def _step5_validate_source(self, context: RunContext) -> None:
        """
        Step 5: Validate and parse source format.
        
        - Apply format definition validation
        - Parse using explicit parsing instructions
        - Fail with actionable diagnostics if invalid
        """
        self._current_step = 5
        logger.info("Step 5: Validating source format")
        
        if not context.source_format:
            self._record_step(5, StepStatus.FAILED, "No source format to validate")
            raise SourceFormatError("No source format artifact available")
        
        issues = context.source_format.validate_format()
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        
        if errors:
            msg = context.source_format.get_diagnostic_message()
            self._record_step(5, StepStatus.FAILED, f"{len(errors)} validation errors")
            raise SourceFormatError(msg)
        
        if warnings:
            self._record_step(5, StepStatus.SUCCESS, f"{len(warnings)} warnings")
        else:
            self._record_step(5, StepStatus.SUCCESS, "Source format valid")
    
    # =========================================================================
    # Step 6: Convert to Canonical SML
    # =========================================================================
    
    def _step6_convert_to_sml(
        self,
        context: RunContext,
        workspace_id: Optional[str],
        dataset_id: Optional[str],
    ) -> SMLModel:
        """
        Step 6: Convert to canonical SML.
        
        - Map validated Source Format into canonical SML
        - Ensure schema correctness and semantic consistency
        """
        self._current_step = 6
        logger.info("Step 6: Converting to canonical SML")
        
        try:
            if context.source_type == "snowflake":
                sml_model = self._convert_snowflake_to_sml(context)
            elif context.source_type == "fabric":
                sml_model = self._convert_fabric_to_sml(context, workspace_id, dataset_id)
            elif context.source_type == "pbix":
                sml_model = self._convert_pbix_to_sml(context)
            else:
                raise ConversionError(f"Unknown source type: {context.source_type}")

            # Normalize relationship names/deduplication here so all downstream
            # target conversions and deployments operate on the same final model.
            self._normalize_relationships_for_target(sml_model)
            return sml_model
                
        except Exception as e:
            self._record_step(6, StepStatus.FAILED, str(e))
            raise ConversionError(f"SML conversion failed: {e}") from e

    def _normalize_relationships_for_target(self, model: SMLModel) -> None:
        """Canonicalize and deduplicate relationships on the final SML model.

        Stage placement is intentional: after canonical SML creation and before
        target-format conversion/deployment.
        
        Deduplication strategy:
        1. Remove exact endpoint duplicates (same columns)
        2. Remove multiple relationships between same table pair (keep strongest)
        3. Remove direct relationships that create cycles (redundant paths)
           - If A->B exists and A->C->B exists, remove A->B (shorter path preferred)
           - Fabric's ambiguous path error indicates we need to break cycles
        """
        if not getattr(model, "relationships", None):
            return

        normalized: list[SMLRelationship] = []
        seen_endpoints: set[tuple[str, str, str, str]] = set()
        table_pair_candidates: dict[tuple[str, str], list[SMLRelationship]] = {}
        renamed_count = 0
        deduped_count = 0

        # First pass: collect by exact endpoint and table pair
        for rel in model.relationships:
            from_col = rel.from_columns[0] if rel.from_columns else ""
            to_col = rel.to_columns[0] if rel.to_columns else ""
            endpoint_key = (
                rel.from_dataset.upper(),
                from_col.upper(),
                rel.to_dataset.upper(),
                to_col.upper(),
            )

            # Skip exact endpoint duplicates
            if endpoint_key in seen_endpoints:
                deduped_count += 1
                continue
            seen_endpoints.add(endpoint_key)

            # Group by table pair for ambiguity detection
            table_pair = (rel.from_dataset.upper(), rel.to_dataset.upper())
            if table_pair not in table_pair_candidates:
                table_pair_candidates[table_pair] = []
            table_pair_candidates[table_pair].append(rel)

        # Second pass: resolve ambiguous table pairs (keep strongest relationship)
        candidate_rels: list[SMLRelationship] = []
        for table_pair, rel_group in table_pair_candidates.items():
            if len(rel_group) > 1:
                # Multiple relationships between same tables - keep strongest
                best_rel = self._select_strongest_relationship(rel_group)
                logger.info(
                    "Ambiguous relationships detected for %s -> %s: "
                    "keeping %s (others removed)",
                    table_pair[0],
                    table_pair[1],
                    best_rel.unique_name,
                )
                deduped_count += len(rel_group) - 1
                candidate_rels.append(best_rel)
            else:
                candidate_rels.append(rel_group[0])

        # Third pass: detect and break cycles/transitive ambiguities
        # Build a graph to detect if keeping A->B creates redundant paths
        graph: dict[str, set[str]] = {}
        for rel in candidate_rels:
            from_table = rel.from_dataset.upper()
            to_table = rel.to_dataset.upper()
            if from_table not in graph:
                graph[from_table] = set()
            graph[from_table].add(to_table)

        # Check each relationship to see if removing it eliminates cycles
        final_rels: list[SMLRelationship] = []
        for rel in candidate_rels:
            from_table = rel.from_dataset.upper()
            to_table = rel.to_dataset.upper()
            
            # Check if there's an alternate path (excluding this direct edge)
            has_alternate_path = self._has_path(from_table, to_table, graph, exclude_edge=(from_table, to_table))
            
            if has_alternate_path:
                logger.info(
                    "Cycle detected for %s -> %s: removing direct edge (alternate path exists via other tables)",
                    from_table,
                    to_table,
                )
                deduped_count += 1
                # Skip this relationship - the alternate path will handle connectivity
            else:
                final_rels.append(rel)

        # Fourth pass: canonicalize names
        for rel in final_rels:
            from_col = rel.from_columns[0] if rel.from_columns else ""
            to_col = rel.to_columns[0] if rel.to_columns else ""
            canonical_name = generate_relationship_name(
                rel.from_dataset,
                from_col,
                rel.to_dataset,
                to_col,
            )
            if rel.unique_name != canonical_name:
                renamed_count += 1
                rel.unique_name = canonical_name

        model.relationships = final_rels

        if renamed_count or deduped_count:
            logger.info(
                "Normalized final relationships: kept=%s renamed=%s removed_duplicates=%s",
                len(model.relationships),
                renamed_count,
                deduped_count,
            )

        for rel in model.relationships:
            logger.info(
                "FINAL REL: %s (%s.%s -> %s.%s)",
                rel.unique_name,
                rel.from_dataset,
                rel.from_column,
                rel.to_dataset,
                rel.to_column,
            )

    def _has_path(self, from_table: str, to_table: str, graph: dict[str, set[str]], exclude_edge: tuple[str, str] = None, max_depth: int = 5) -> bool:
        """Check if there's a path from from_table to to_table, optionally excluding a specific edge.
        
        Uses BFS with max depth to detect if alternate paths exist through intermediary tables.
        Limited to 5 hops to avoid exploring distant relationships.
        """
        from collections import deque
        
        if from_table == to_table:
            return True
        
        queue: deque = deque([(from_table, 0)])
        visited: set[str] = {from_table}
        
        while queue:
            current, depth = queue.popleft()
            
            if depth >= max_depth:
                continue
            
            for next_table in graph.get(current, set()):
                # Skip excluded edge
                if exclude_edge and (current, next_table) == exclude_edge:
                    continue
                
                if next_table == to_table:
                    return True
                
                if next_table not in visited:
                    visited.add(next_table)
                    queue.append((next_table, depth + 1))
        
        return False

    def _select_strongest_relationship(self, candidates: list[SMLRelationship]) -> SMLRelationship:
        """Select the strongest relationship from ambiguous candidates.
        
        Strength hierarchy:
        1. Explicit foreign keys (from metadata)
        2. FK column naming pattern (ends with _id, _key, etc.)
        3. Column name matches target table name
        4. First encountered (fallback)
        """
        # Score each candidate
        scored = []
        for rel in candidates:
            score = 0
            from_col = rel.from_columns[0] if rel.from_columns else ""
            to_dataset = rel.to_dataset.upper()

            # Preference 1: Explicit foreign key (marked in metadata)
            if getattr(rel, "is_explicit_fk", False):
                score += 100

            # Preference 2: Standard FK naming conventions
            fk_suffixes = ["_ID", "_KEY", "_FK", "_CODE"]
            if any(from_col.upper().endswith(suffix) for suffix in fk_suffixes):
                score += 50

            # Preference 3: Column matches target table name
            # e.g., scenario_id -> scenario table
            col_base = from_col.upper().rstrip("_ID_KEYFK")
            if col_base == to_dataset or col_base.rstrip("S") == to_dataset:
                score += 25

            scored.append((score, rel))

        # Return highest-scored relationship, or first if tied
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
    
    def _convert_snowflake_to_sml(self, context: RunContext) -> SMLModel:
        """Convert Snowflake source to SML."""
        from semabridge.connectors.inference_engine import SmlInferenceEngine
        from semabridge.connectors.measure_detector import MeasureDetector
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.sml.assembler import SMLAssembler
        
        config = context.config
        sf = context.source_format

        # Prefer semantic-view driven conversion when Step 4 captured DDL.
        if sf.semantic_view_ddl:
            try:
                from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
                from semabridge.converter.osi_to_sml import OSIToSMLConverter

                column_metadata = {
                    table_name: [col.model_dump() for col in cols]
                    for table_name, cols in sf.columns.items()
                }
                osi_model = SemanticViewToOSIConverter().to_osi(
                    {
                        "ddl": sf.semantic_view_ddl,
                        "view_name": sf.semantic_view_name or context.project_id,
                        "column_metadata": column_metadata,
                    }
                )
                context.osi_model = osi_model
                sml_model = OSIToSMLConverter().from_osi(osi_model)

                self._record_step(
                    6,
                    StepStatus.SUCCESS,
                    f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics",
                )
                return sml_model
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Semantic-view conversion failed for '%s'; falling back to metadata inference: %s",
                    sf.semantic_view_name or context.project_id,
                    exc,
                )
        
        # Convert source format back to metadata dict for existing assembler
        metadata = {
            "database": sf.database,
            "schema": sf.schema_name,
            "tables": {name: {"description": t.description, "row_count": t.row_count}
                      for name, t in sf.tables.items()},
            "columns": {name: [c.model_dump() for c in cols]
                       for name, cols in sf.columns.items()},
            "foreign_keys": [fk.model_dump() for fk in sf.foreign_keys],
            "primary_keys": sf.primary_keys,
        }
        
        assembler = SMLAssembler(
            model_name=context.project_id,
            description=config.model.description,
            source_database=sf.database,
            source_schema=sf.schema_name,
        )
        
        # Add tables (deduplicate columns by name to avoid downstream conflicts)
        for table_name, table_info in metadata["tables"].items():
            columns = metadata["columns"].get(table_name, [])
            seen_cols: set[str] = set()
            deduped_cols: list[dict[str, Any]] = []
            for col in columns:
                col_name = str(col.get("name") or col.get("COLUMN_NAME") or "").strip()
                if not col_name:
                    continue
                key = col_name.upper()
                if key in seen_cols:
                    logger.debug("Skipping duplicate column %s in table %s", col_name, table_name)
                    continue
                seen_cols.add(key)
                deduped_cols.append(col)
            assembler.add_table(
                table_name=table_name,
                columns=deduped_cols,
                description=table_info.get("description", ""),
                row_count=table_info.get("row_count"),
            )
        
        # Detect relationships
        rel_detector = RelationshipDetector(
            tables=metadata["tables"],
            columns=metadata["columns"],
            primary_keys=metadata["primary_keys"],
            explicit_fks=metadata["foreign_keys"],
        )
        relationships = rel_detector.detect_all()
        
        for rel in relationships:
            from_table = str(
                rel.get("from_table")
                or rel.get("source_table")
                or rel.get("left_table")
                or ""
            ).strip()
            from_column = str(
                rel.get("from_column")
                or rel.get("source_column")
                or rel.get("left_column")
                or ""
            ).strip()
            to_table = str(
                rel.get("to_table")
                or rel.get("target_table")
                or rel.get("right_table")
                or ""
            ).strip()
            to_column = str(
                rel.get("to_column")
                or rel.get("target_column")
                or rel.get("right_column")
                or ""
            ).strip()
            rel_name = str(rel.get("name") or "").strip() or generate_relationship_name(
                from_table,
                from_column,
                to_table,
                to_column,
            )

            if not (from_table and from_column and to_table and to_column):
                logger.warning("Skipping malformed relationship during SML conversion: %s", rel)
                continue

            assembler.add_relationship(
                name=rel_name,
                from_table=from_table,
                from_column=from_column,
                to_table=to_table,
                to_column=to_column,
            )
        
        # Classify tables
        engine = SmlInferenceEngine(
            tables=metadata["tables"],
            columns=metadata["columns"],
            relationships=relationships,
            primary_keys=metadata["primary_keys"],
        )
        scores = engine.classify()
        
        classification_map = {}
        for ds in assembler._datasets:
            score = scores.get(ds.unique_name)
            if score:
                classification_map[ds.unique_name] = score.classification
                ds.is_fact = score.classification == "FACT"
        
        # Detect measures
        measure_detector = MeasureDetector(
            tables=metadata["tables"],
            columns=metadata["columns"],
            relationships=relationships,
        )
        all_measures = measure_detector.detect_all_measures(classification=classification_map)
        
        for table_name, measures in all_measures.items():
            for measure in measures[:5]:
                assembler.add_metric(
                    name=measure["name"],
                    dataset=table_name,
                    source_column=measure["column"],
                    aggregation=measure["aggregation"],
                )
        
        sml_model = assembler.build()

        # Populate context.osi_model via SML→OSI roundtrip for auditing (mandatory OSI pass)
        try:
            from semabridge.converter.sml_to_osi import SMLToOSIConverter
            context.osi_model = SMLToOSIConverter().to_osi(sml_model)
        except Exception as e:
            logger.warning(f"OSI roundtrip for Snowflake source failed (non-fatal): {e}")

        self._record_step(
            6, StepStatus.SUCCESS,
            f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics"
        )

        return sml_model

    def _convert_fabric_to_sml(
        self,
        context: RunContext,
        workspace_id: Optional[str],
        dataset_id: Optional[str],
    ) -> SMLModel:
        """Convert Fabric TMSL to SML via the mandatory OSI intermediate layer.
        
        Flow: TMSL → OSIModel (TMSLToOSIConverter) → SMLModel (OSIToSMLConverter)
        The OSIModel is stored on context.osi_model for auditing / step-7 persistence.
        """
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.converter.osi_to_sml import OSIToSMLConverter

        sf = context.source_format
        ws_id = workspace_id or sf.workspace_id
        ds_id = dataset_id or sf.dataset_id

        # Phase 1: TMSL → OSI
        source_data = {
            "tmsl": sf.tmsl_definition,
            "workspace_id": ws_id,
            "dataset_id": ds_id,
        }
        osi_model = TMSLToOSIConverter().to_osi(source_data)
        context.osi_model = osi_model  # Store on context for step-7 persistence
        logger.debug(
            f"OSI intermediate: {len(osi_model.datasets)} datasets, "
            f"{len(osi_model.metrics)} metrics, {len(osi_model.relationships)} relationships"
        )

        # Phase 2: OSI → SML
        sml_model = OSIToSMLConverter().from_osi(
            osi_model,
            row_counts=sf.row_counts,
        )

        self._record_step(
            6, StepStatus.SUCCESS,
            f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics (via OSI)"
        )

        return sml_model

    def _convert_pbix_to_sml(self, context: RunContext) -> SMLModel:
        """Convert PBIX DataModelSchema (TMSL) to SML via the mandatory OSI intermediate layer.

        The DataModelSchema inside a .pbix archive is structurally identical to the
        Fabric TMSL model definition, so we reuse TMSLToOSIConverter for Phase 1.
        Flow: TMSL → OSIModel → SMLModel
        """
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.converter.osi_to_sml import OSIToSMLConverter

        sf = context.source_format
        # PBIX has no workspace/dataset IDs — use sentinel values
        ws_id = "local"
        ds_id = context.project_id

        # Phase 1: TMSL → OSI
        source_data = {
            "tmsl": sf.tmsl_definition,
            "workspace_id": ws_id,
            "dataset_id": ds_id,
        }
        osi_model = TMSLToOSIConverter().to_osi(source_data)
        context.osi_model = osi_model
        logger.debug(
            f"PBIX OSI intermediate: {len(osi_model.datasets)} datasets, "
            f"{len(osi_model.metrics)} metrics"
        )
        logger.info(
            "PBIX OSI datasets: %s",
            [ds.unique_name for ds in osi_model.datasets],
        )
        logger.info(
            "PBIX OSI metrics: %s",
            [m.unique_name for m in osi_model.metrics],
        )

        # Phase 2: OSI → SML
        sml_model = OSIToSMLConverter().from_osi(
            osi_model,
            row_counts=sf.row_counts,
        )
        logger.info(
            "PBIX SML datasets: %s",
            [ds.unique_name for ds in sml_model.datasets],
        )
        logger.info(
            "PBIX SML metrics: %s",
            [m.unique_name for m in sml_model.metrics],
        )

        self._record_step(
            6, StepStatus.SUCCESS,
            f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics (via OSI)",
        )

        return sml_model

    # =========================================================================
    # Step 7: Persist Artifacts
    # =========================================================================
    
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
                connection_tag=context.connection_tag,
            )
            
            # Commit SML to DuckDB
            sml_dict = context.sml_model.model_dump(mode='json') if context.sml_model else {}
            
            committed, snapshot_id = self.db_manager.commit_model(
                project_id=context.project_id,
                sml_json=sml_dict,
                tag=tag,
                status="success",
                duration_ms=int((time.time() - context.start_time) * 1000),
                run_id=context.run_id,
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
    
    # =========================================================================
    # Step 8: Convert to Target Format (Optional)
    # =========================================================================
    
    def _step8_convert_to_target(self, context: RunContext) -> None:
        """
        Step 8: Convert to target format.
        
        - Convert SML into Target Format using target rule pack
        - Validate generated output before deployment
        """
        self._current_step = 8
        logger.info(f"Step 8: Converting to {context.target_type} target format")
        
        try:
            if context.target_type == "fabric":
                self._convert_to_fabric_target(context)
            elif context.target_type == "snowflake":
                self._convert_to_snowflake_target(context)
            elif context.target_type == "databricks":
                self._convert_to_databricks_target(context)
            
            self._record_step(8, StepStatus.SUCCESS, f"Target format generated")
            
        except Exception as e:
            self._record_step(8, StepStatus.FAILED, str(e))
            raise ConversionError(f"Target conversion failed: {e}") from e
    
    def _convert_to_fabric_target(self, context: RunContext) -> None:
        """Generate Fabric TMSL."""
        from semabridge.connectors.tmsl_generator import TMSLGenerator
        
        config = context.config
        sf_config = context.scoped_snowflake_config or config.snowflake
        
        generator = TMSLGenerator(
            context.sml_model,
            snowflake_server=sf_config.account,
            snowflake_warehouse=sf_config.warehouse,
            snowflake_database=sf_config.database,
            snowflake_schema=sf_config.schema_name,
        )
        
        output_dir = self._model_output_dir("fabric", model_name=context.project_id)
        
        bim_path = output_dir / "model.bim"
        generator.save(bim_path)
        context.target_artifact_path = str(bim_path)
    
    def _convert_to_snowflake_target(self, context: RunContext) -> None:
        """Generate Snowflake DDL."""
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        
        config = context.config
        sf_config = context.scoped_snowflake_config or config.snowflake
        emitter = SnowflakeEmitter(sf_config, behavior=context.behavior)
        
        output_dir = self._model_output_dir("reverse", model_name=context.project_id)
        
        ddls = emitter.generate_ddls(context.sml_model)
        full_ddl = "\n\n".join(ddls)
        yaml_out = emitter.generate_cortex_yaml(context.sml_model)
        
        ddl_path = output_dir / "semantic_view.sql"
        yaml_path = output_dir / "cortex_analyst.yaml"
        
        with open(ddl_path, "w") as f:
            f.write(full_ddl)
        with open(yaml_path, "w") as f:
            f.write(yaml_out)
        
        context.target_artifact_path = str(ddl_path)

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
    
    # =========================================================================
    # Step 9: Deploy to Target (Optional)
    # =========================================================================
    
    def _step9_deploy(self, context: RunContext) -> None:
        """   
        Step 9: Deploy to target.
        - Emit/deploy Target Format to target system
        - Handle partial deployment failures explicitly
        """
        self._current_step = 9
        logger.info(f"Step 9: Deploying to {context.target_type}")

        try:
            if context.target_type == "fabric":
                self._deploy_to_fabric(context)
            elif context.target_type == "snowflake":
                self._deploy_to_snowflake(context)
            elif context.target_type == "databricks":
                self._deploy_to_databricks(context)
        
            self._record_step(9, StepStatus.SUCCESS, "Deployment complete")

        # ✅ HANDLE MISSING TABLE AS WARNING (NOT FAILURE)
        except MissingSourceTableWarning as w:
            logger.warning(f"Missing underlying table during deployment: {w}")

            # Record step as success but with warning message
            self._record_step(
                9,
                StepStatus.SUCCESS,
                f"Deployment completed with warning: {str(w)}"
            )
        # 🔥 DO NOT re-raise

        except Exception as e:
            self._record_step(9, StepStatus.FAILED, str(e))
            raise DeploymentError(f"Deployment failed: {e}") from e
    
    def _deploy_to_fabric(self, context: RunContext) -> None:
        """Deploy to Fabric with multi-account isolation.
        
        Prioritizes scoped credentials resolved during Stage 3.
        """
        from semabridge.connectors.fabric_publisher import FabricPublisher
        from semabridge.core.settings import FabricConfig

        config = context.config
        fabric_cfg = context.scoped_fabric_config or config.fabric
        fabric_token = context.scoped_fabric_token

        # Ensure the workspace_id from the project config (YAML) takes precedence
        target_workspace = ""
        target_identity_id = ""
        target_configs = getattr(config, "targets", None) or []
        for tc in target_configs:
            if isinstance(tc, dict) and tc.get("type") == "fabric":
                target_workspace = str(tc.get("workspace_id", "") or "").strip()
                target_identity_id = str(tc.get("identity_id", "") or "").strip()
                break
        
        if not target_workspace or not target_identity_id:
            target_cfg = getattr(config, "target", None)
            if target_cfg and getattr(target_cfg, "type", "") == "fabric":
                target_workspace = target_workspace or str(getattr(target_cfg, "workspace_id", "") or "").strip()
                target_identity_id = target_identity_id or str(getattr(target_cfg, "identity_id", "") or "").strip()

        # Always resolve a fresh token for the target identity before deploying.
        # context.scoped_fabric_token was set at Stage 3 — MSAL tokens expire in
        # 60 minutes, so by Stage 9 the token may already be stale. Re-resolving
        # here guarantees the Publisher always receives a valid token.
        if target_identity_id:
            try:
                from semabridge.api.services.connection_domain_service import _resolve_fabric_access_token
                fresh_token = _resolve_fabric_access_token(None, target_identity_id)
                if fresh_token:
                    logger.info(
                        "_deploy_to_fabric: resolved fresh token for target identity %s",
                        target_identity_id,
                    )
                    fabric_token = fresh_token
            except Exception as exc:
                logger.warning(
                    "_deploy_to_fabric: fresh token resolution failed for %s, "
                    "falling back to scoped token: %s",
                    target_identity_id, exc,
                )

        if target_workspace and fabric_cfg.workspace_id != target_workspace:
            fabric_cfg = FabricConfig(
                tenant_id=fabric_cfg.tenant_id,
                client_id=fabric_cfg.client_id,
                client_secret=fabric_cfg.client_secret.get_secret_value() if fabric_cfg.client_secret else None,
                workspace_id=target_workspace,
                api_base_url=fabric_cfg.api_base_url,
                power_bi_api_url=fabric_cfg.power_bi_api_url,
            )

        sf_cfg = context.scoped_snowflake_config or config.snowflake
        publisher = FabricPublisher(config=fabric_cfg)
        if fabric_token:
            publisher._access_token = fabric_token
            publisher._token_expiry = time.time() + 1800  # matches FabricPublisher._token_expiry attr

        publisher.publish(
            sml_model=context.sml_model,
            model_name=context.project_id,
            snowflake_server=sf_cfg.account,
            snowflake_warehouse=sf_cfg.warehouse,
            snowflake_database=sf_cfg.database,
            snowflake_schema=sf_cfg.schema_name,
            overwrite=True,
        )
    
    def _deploy_to_snowflake(self, context: RunContext) -> None:
        """Deploy to Snowflake with multi-account isolation.
        
        Prioritizes scoped credentials resolved during Stage 3.
        """
        config = context.config
        sf_cfg = context.scoped_snowflake_config or config.snowflake
        # Ensure scoped credentials were resolved in Stage 3.
        # Fallback to global config if needed.
        if not context.scoped_snowflake_config:
            # Re-read global config cleanly 
            sf_cfg = context.config.snowflake

        self._do_snowflake_deploy(context, sf_cfg)

    def _do_snowflake_deploy(self, context: RunContext, sf_cfg) -> None:
        """Inner helper to perform Snowflake deployment with resolved config."""
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        
        emitter = SnowflakeEmitter(sf_cfg, behavior=context.behavior)
        deployment_method = getattr(sf_cfg, "deployment_method", "ddl") or "ddl"

        # ── DDL path ─────────────────────────────────────────────────────────
        if deployment_method in ("ddl", "both"):
            emitter.deploy(context.sml_model)
            self._export_inferred_osi_artifacts(context)

        # ── Stored-procedure / Cortex YAML path ──────────────────────────────
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

        # ── Optional: Sync materialized DAX measures to MEASURES_* tables ────
        if context.source_type == "fabric" and self._should_sync_measures(context):
            self._sync_fabric_measures(context, emitter)

    def _deploy_to_databricks(self, context: RunContext) -> None:
        """Deploy SML metadata projection and measure views to Databricks.
        
        Prioritizes scoped credentials resolved during Stage 3.
        """
        from semabridge.connectors.databricks_publisher import DatabricksPublisher

        config = context.config
        db_config = context.scoped_databricks_config or config.databricks

        publisher = DatabricksPublisher(db_config, behavior=context.behavior)
        context.target_artifact_path = publisher.publish(context.sml_model)
        publish_summary = publisher.get_last_publish_summary()
        context.routing_summary = publish_summary.get("routing_summary") if isinstance(publish_summary, dict) else None
        self._raise_if_databricks_fallback_failed(context, publish_summary)

    def _raise_if_databricks_fallback_failed(
        self,
        context: RunContext,
        publish_summary: Any,
    ) -> None:
        """Fail deployment when Databricks SQL fallback enters terminal FAILED state."""
        if not isinstance(publish_summary, dict):
            return

        fallback_state = str(publish_summary.get("sql_fallback_state") or "").strip().upper()
        if fallback_state != "FALLBACK_FAILED":
            return

        fallback_reason = str(publish_summary.get("sql_fallback_reason") or "").strip()
        if fallback_reason:
            raise DeploymentError(
                f"Databricks SQL fallback failed for model '{context.project_id}': {fallback_reason}"
            )
        raise DeploymentError(f"Databricks SQL fallback failed for model '{context.project_id}'")

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

            out_dir = self._model_output_dir("inferred", model_name=context.project_id)
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
    
    # =========================================================================
    # Step 10: Finalize Run
    # =========================================================================
    
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
            sf_cfg = context.scoped_snowflake_config or context.config.snowflake
            if getattr(sf_cfg, "push_run_summary_to_snowflake", False):
                from semabridge.repository.observability_table import ObservabilityTable
                obs = ObservabilityTable(sf_cfg)
                obs.insert_run_summary(finalized)
        except Exception as _obs_exc:
            logger.warning(
                f"Snowflake observability push failed (non-fatal): {_obs_exc}"
            )

        return finalized
    
    # =========================================================================
    # Measure Sync (Complex DAX Support)
    # =========================================================================
    
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
            # Do not mutate config.fabric.workspace_id — the immutable FabricConfig
            # copy below (measures_fabric_cfg) will carry the correct workspace_id.
            pass

        # Resolve the token for the source identity that performed the extraction.
        # Using the global CredentialManager here would contaminate multi-account
        # syncs by picking up whatever the last logged-in user's token was.
        # Instead, we resolve via the same per-account path as Stage 3 and Stage 4.
        interactive_token: Optional[str] = context.scoped_fabric_token
        source_identity_id = str(
            getattr(getattr(config, "source", None), "identity_id", "") or ""
        ).strip()

        if source_identity_id:
            try:
                from semabridge.api.services.connection_domain_service import _resolve_fabric_access_token
                fresh_token = _resolve_fabric_access_token(None, source_identity_id)
                if fresh_token:
                    interactive_token = fresh_token
                    logger.info(
                        "_sync_fabric_measures: resolved fresh token for source identity %s",
                        source_identity_id,
                    )
            except Exception as exc:
                logger.warning(
                    "_sync_fabric_measures: fresh token resolution failed for %s, "
                    "falling back to scoped token: %s",
                    source_identity_id, exc,
                )

        # Build an immutable FabricConfig copy with the resolved workspace_id
        # instead of mutating config.fabric.workspace_id in-place. Mutating
        # the shared config object is unsafe under parallel sync runs because
        # two concurrent _sync_fabric_measures calls could overwrite each
        # other's workspace_id mid-flight.
        measures_fabric_cfg = config.fabric
        if ws_id and config.fabric.workspace_id != ws_id:
            from semabridge.core.settings import FabricConfig
            measures_fabric_cfg = FabricConfig(
                tenant_id=config.fabric.tenant_id,
                client_id=config.fabric.client_id,
                client_secret=config.fabric.client_secret.get_secret_value() if config.fabric.client_secret else None,
                workspace_id=ws_id,
                api_base_url=config.fabric.api_base_url,
                power_bi_api_url=config.fabric.power_bi_api_url,
            )

        # Initialize Fabric extractor with the per-account token
        fabric_extractor = FabricExtractor(measures_fabric_cfg)
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
