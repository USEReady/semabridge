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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from pydantic import Field
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.core.settings import Settings, get_settings
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
from semabridge.sml.models import SMLModel
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name

logger = get_logger(__name__)


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
    """
    project_id: str
    run_id: str
    config: Settings
    start_time: float
    source_type: Literal["snowflake", "fabric", "pbix"]
    target_type: Optional[Literal["snowflake", "fabric"]] = None
    behavior: ConnectorBehavior = Field(default_factory=ConnectorBehavior)
    
    # Artifacts accumulated during execution
    source_format: Optional[SourceFormat] = None
    osi_model: Optional[OSIModel] = None  # OSI intermediate — populated after Step 6
    sml_model: Optional[SMLModel] = None
    source_artifact_id: Optional[str] = None
    sml_snapshot_id: Optional[str] = None
    target_artifact_path: Optional[str] = None


class ExecutionEngine:
    """
    Orchestrates CLI execution following the mandatory 10-step flow.
    
    Each method corresponds to one step and must be called in order.
    The engine enforces this order and handles failures appropriately.
    """
    
    SUPPORTED_SOURCES = {"snowflake", "fabric", "pbix"}
    SUPPORTED_TARGETS = {"snowflake", "fabric", None}
    
    def __init__(self, db_manager: Optional[ModelRepository] = None):
        self.db_manager = db_manager or ModelRepository()
        self._current_step = 0
        self._context: Optional[RunContext] = None
        self._summary: Optional[RunSummary] = None

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
        target: Optional[Literal["snowflake", "fabric"]] = None,
        project_name: Optional[str] = None,
        config_path: Optional[Path] = None,
        deploy: bool = True,
        tag: Optional[str] = None,
        dry_run: bool = False,
        # Source-specific options
        dataset_id: Optional[str] = None,  # For Fabric source
        workspace_id: Optional[str] = None,  # For Fabric source
        pbix_path: Optional[str] = None,  # For PBIX source
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
            config = self._step1_load_config(config_path, source, target)
            
            # Step 2: Initialize Identifiers
            context = self._step2_init_identifiers(
                config, source, target, project_name, dataset_id, config_path
            )
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
            
            # Step 3: Resolve Authentication
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
        if self._summary:
            self._summary.add_step(
                step_number=step_number,
                step_name=STEP_NAMES.get(step_number, f"Step {step_number}"),
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
            candidate_dirs = [config_path.parent, Path(".")]
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
            cwd_behavior = Path("behavior.yaml")
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
    
    # =========================================================================
    # Step 3: Resolve Authentication
    # =========================================================================
    
    def _step3_resolve_auth(self, context: RunContext) -> None:
        """
        Step 3: Resolve authentication.
        
        - Resolve credentials from environment variables
        - Validate all required variables present
        - Do not allow inline secrets in config files
        """
        self._current_step = 3
        logger.info("Step 3: Resolving authentication")
        
        config = context.config
        missing = []
        auth_sources: list[str] = []
        
        # Validate source auth
        if context.source_type == "snowflake":
            if not config.validate_snowflake():
                missing.append("Snowflake credentials (SNOWFLAKE_*)")
        elif context.source_type == "fabric":
            if context.behavior.features.offline_mode:
                logger.info("Step 3: OFFLINE mode enabled - skipping Fabric auth validation")
                auth_sources.append("OFFLINE")
            else:
                fabric_env_ok = config.validate_fabric()
                fabric_ui_ok = self._has_fabric_interactive_auth()
                if not (fabric_env_ok or fabric_ui_ok):
                    missing.append("Fabric credentials (FABRIC_*)")
                elif fabric_ui_ok:
                    auth_sources.append("UI token")
                else:
                    auth_sources.append("ENV")
        # PBIX source needs no external auth — local file
        elif context.source_type == "pbix":
            pass
        
        # Validate target auth
        if context.target_type == "snowflake":
            if not config.validate_snowflake():
                missing.append("Snowflake credentials (SNOWFLAKE_*)")
        elif context.target_type == "fabric":
            fabric_env_ok = config.validate_fabric()
            fabric_ui_ok = self._has_fabric_interactive_auth()
            if not (fabric_env_ok or fabric_ui_ok):
                missing.append("Fabric credentials (FABRIC_*)")
            elif fabric_ui_ok:
                auth_sources.append("UI token")
            else:
                auth_sources.append("ENV")
        
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
                return self._extract_snowflake(context)
            elif context.source_type == "fabric":
                return self._extract_fabric(context, dataset_id, workspace_id)
            elif context.source_type == "pbix":
                return self._extract_pbix(context, pbix_path)
            else:
                raise ExtractionError(f"Unknown source type: {context.source_type}")
                
        except Exception as e:
            self._record_step(4, StepStatus.FAILED, str(e))
            raise ExtractionError(f"Extraction failed: {e}") from e
    
    def _extract_snowflake(self, context: RunContext) -> SourceFormat:
        """Extract from Snowflake."""
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.utils.cache import MetadataCache
        
        config = context.config
        cache = MetadataCache(config.model.cache_dir) if config.model.cache_enabled else None
        
        extractor = SnowflakeExtractor(
            config=config.snowflake,
            cache=cache,
            exclude_tables=config.model.excluded_table_list,
            include_tables=config.model.included_table_list,
        )
        
        metadata = extractor.extract_all()
        semantic_data = extractor.read_semantic_tables()
        metadata["semantic_tables"] = semantic_data
        
        source_format = from_snowflake_metadata(
            project_id=context.project_id,
            run_id=context.run_id,
            metadata=metadata,
        )
        
        table_count = len(source_format.tables)
        self._record_step(4, StepStatus.SUCCESS, f"Extracted {table_count} tables")
        
        return source_format
    
    def _extract_fabric(
        self,
        context: RunContext,
        dataset_id: Optional[str],
        workspace_id: Optional[str],
    ) -> SourceFormat:
        """Extract from Fabric."""
        from semabridge.connectors.fabric_extractor import FabricExtractor
        
        config = context.config
        ws_id = workspace_id or config.fabric.workspace_id
        interactive_token: Optional[str] = None

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
                workspace_id=ws_id,
                dataset_id=resolved_dataset_id,
                row_counts=row_counts,
            )

            table_count = len(tmsl.get("model", {}).get("tables", []))
            self._record_step(4, StepStatus.SUCCESS, f"OFFLINE extract loaded {table_count} tables")
            return source_format

        try:
            from semabridge.repository.credential_manager import CredentialManager

            cm = CredentialManager()
            stored_fabric = cm.get_credentials("fabric", mask_secrets=False)
            stored_workspace_id = (stored_fabric.get("workspace_id") or "").strip()
            if not workspace_id and stored_workspace_id:
                ws_id = stored_workspace_id

            token_data = cm.get_msal_token()
            if cm.get_fabric_auth_method() == "interactive" and cm.has_valid_token() and token_data:
                interactive_token = token_data.get("access_token")
                logger.debug("_extract_fabric: injecting interactive token from credential store")
        except Exception as exc:
            logger.warning("_extract_fabric: credential lookup failed, falling back to env auth: %s", exc)
        
        if not dataset_id:
            raise ExtractionError("dataset_id is required for Fabric source")

        if ws_id and config.fabric.workspace_id != ws_id:
            config.fabric.workspace_id = ws_id
        
        extractor = FabricExtractor(config.fabric)
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
            raise ExtractionError(
                "pbix_path is required for PBIX source. "
                "Provide a path to a .pbix file."
            )

        p = Path(pbix_path)
        if not p.exists():
            raise ExtractionError(f"PBIX file not found: {pbix_path}")

        connector = LocalPBIXConnector({"pbix_path": pbix_path})
        connector.authenticate()
        discovered = connector.discover()  # may populate _data_model_schema or fallback metadata

        # The raw DataModelSchema is structurally identical to Fabric TMSL
        tmsl = connector._data_model_schema
        if tmsl and "model" not in tmsl and isinstance(tmsl.get("tables"), list):
            tmsl = {"model": tmsl}

        if not tmsl:
            # Fallback path (pbixray): build a minimal TMSL-compatible structure
            model_info = (discovered.get("models") or [{}])[0]
            model_name = model_info.get("name") or p.stem

            table_entries: list[dict[str, Any]] = []
            for table in discovered.get("tables", []):
                table_entries.append(
                    {
                        "name": table.get("name", ""),
                        "description": table.get("description", ""),
                        "isHidden": table.get("is_hidden", False),
                        "columns": [
                            {
                                "name": col.get("name", ""),
                                "dataType": col.get("data_type", "string"),
                                "isHidden": col.get("is_hidden", False),
                                "sourceColumn": col.get("source_column", ""),
                                "type": col.get("type", "data"),
                            }
                            for col in table.get("columns", [])
                        ],
                        "partitions": [],
                        "measures": [],
                    }
                )

            table_index = {t.get("name"): t for t in table_entries}
            for measure in discovered.get("measures", []):
                table_name = measure.get("table", "")
                if table_name not in table_index:
                    table_index[table_name] = {
                        "name": table_name,
                        "description": "",
                        "isHidden": False,
                        "columns": [],
                        "partitions": [],
                        "measures": [],
                    }
                    table_entries.append(table_index[table_name])

                table_index[table_name]["measures"].append(
                    {
                        "name": measure.get("name", ""),
                        "expression": measure.get("expression", ""),
                        "formatString": measure.get("format_string", ""),
                        "description": measure.get("description", ""),
                        "isHidden": measure.get("is_hidden", False),
                        "displayFolder": measure.get("display_folder", ""),
                    }
                )

            relationships = [
                {
                    "name": rel.get("name", ""),
                    "fromTable": rel.get("from_table", ""),
                    "fromColumn": rel.get("from_column", ""),
                    "toTable": rel.get("to_table", ""),
                    "toColumn": rel.get("to_column", ""),
                    "crossFilteringBehavior": rel.get("cross_filtering_behavior", "oneDirection"),
                    "isActive": rel.get("is_active", True),
                    "fromCardinality": "many" if str(rel.get("cardinality", "many-to-one")).startswith("many") else "one",
                    "toCardinality": "one" if str(rel.get("cardinality", "many-to-one")).endswith("one") else "many",
                }
                for rel in discovered.get("relationships", [])
            ]

            tmsl = {
                "model": {
                    "name": model_name,
                    "tables": table_entries,
                    "relationships": relationships,
                }
            }

        if not tmsl:
            raise ExtractionError(
                f"Could not extract DataModelSchema from {pbix_path}"
            )

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
        """
        if not getattr(model, "relationships", None):
            return

        normalized: list[SMLRelationship] = []
        seen_endpoints: set[tuple[str, str, str, str]] = set()
        renamed_count = 0
        deduped_count = 0

        for rel in model.relationships:
            from_col = rel.from_columns[0] if rel.from_columns else ""
            to_col = rel.to_columns[0] if rel.to_columns else ""
            endpoint_key = (
                rel.from_dataset.upper(),
                from_col.upper(),
                rel.to_dataset.upper(),
                to_col.upper(),
            )

            # Deduplicate only exact endpoint duplicates.
            if endpoint_key in seen_endpoints:
                deduped_count += 1
                continue
            seen_endpoints.add(endpoint_key)

            canonical_name = generate_relationship_name(
                rel.from_dataset,
                from_col,
                rel.to_dataset,
                to_col,
            )
            if rel.unique_name != canonical_name:
                renamed_count += 1
                rel.unique_name = canonical_name

            normalized.append(rel)

        model.relationships = normalized

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
    
    def _convert_snowflake_to_sml(self, context: RunContext) -> SMLModel:
        """Convert Snowflake source to SML."""
        from semabridge.connectors.inference_engine import SmlInferenceEngine
        from semabridge.connectors.measure_detector import MeasureDetector
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.sml.assembler import SMLAssembler
        
        config = context.config
        sf = context.source_format
        
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
        
        # Add tables
        for table_name, table_info in metadata["tables"].items():
            columns = metadata["columns"].get(table_name, [])
            assembler.add_table(
                table_name=table_name,
                columns=columns,
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
            assembler.add_relationship(
                name=rel["name"],
                from_table=rel["from_table"],
                from_column=rel["from_column"],
                to_table=rel["to_table"],
                to_column=rel["to_column"],
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

        # Load metric overrides and alias map from the behavior policy attached
        # to this run context (sourced from behavior.yaml / ExecutionConfig).
        metric_overrides: Dict[str, str] = context.behavior.semantic_model.metric_overrides
        override_alias_map: Dict[str, str] = context.behavior.semantic_model.override_alias_map

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

        # Phase 2: OSI → SML (pass override_alias_map so short prefixes like
        # FACT / SALESFACT are resolved before expressions reach the emitter)
        sml_model = OSIToSMLConverter().from_osi(
            osi_model,
            metric_overrides=metric_overrides,
            override_alias_map=override_alias_map,
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
        ds_id = sf.pbix_path or context.project_id

        # Load metric overrides and alias map from the behavior policy
        metric_overrides: Dict[str, str] = context.behavior.semantic_model.metric_overrides
        override_alias_map: Dict[str, str] = context.behavior.semantic_model.override_alias_map

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

        # Phase 2: OSI → SML
        sml_model = OSIToSMLConverter().from_osi(
            osi_model,
            metric_overrides=metric_overrides,
            override_alias_map=override_alias_map,
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
            
            self._record_step(8, StepStatus.SUCCESS, f"Target format generated")
            
        except Exception as e:
            self._record_step(8, StepStatus.FAILED, str(e))
            raise ConversionError(f"Target conversion failed: {e}") from e
    
    def _convert_to_fabric_target(self, context: RunContext) -> None:
        """Generate Fabric TMSL."""
        from semabridge.connectors.tmsl_generator import TMSLGenerator
        
        config = context.config
        
        generator = TMSLGenerator(
            context.sml_model,
            snowflake_server=config.snowflake.account,
            snowflake_warehouse=config.snowflake.warehouse,
            snowflake_database=config.snowflake.database,
            snowflake_schema=config.snowflake.schema_name,
        )
        
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        bim_path = output_dir / "model.bim"
        generator.save(bim_path)
        context.target_artifact_path = str(bim_path)
    
    def _convert_to_snowflake_target(self, context: RunContext) -> None:
        """Generate Snowflake DDL."""
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        
        config = context.config
        emitter = SnowflakeEmitter(config.snowflake, behavior=context.behavior)
        
        output_dir = Path("output/reverse")
        output_dir.mkdir(parents=True, exist_ok=True)
        
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
        """Deploy to Fabric."""
        from semabridge.connectors.fabric_publisher import FabricPublisher
        
        config = context.config
        publisher = FabricPublisher(config.fabric)
        
        publisher.publish(
            sml_model=context.sml_model,
            model_name=context.project_id,
            snowflake_server=config.snowflake.account,
            snowflake_warehouse=config.snowflake.warehouse,
            snowflake_database=config.snowflake.database,
            snowflake_schema=config.snowflake.schema_name,
            overwrite=True,
        )
    
    def _deploy_to_snowflake(self, context: RunContext) -> None:
        """
        Deploy to Snowflake, dispatching based on ``deployment_method`` setting.

        deployment_method values (set in SnowflakeConfig):
          - ``ddl``                   — DDL Semantic View only (default, existing behaviour)
          - ``yaml_stored_procedure`` — Cortex YAML via SYSTEM$CREATE_SEMANTIC_MODEL only
          - ``both``                  — DDL first, then YAML stored-procedure path
        """
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        config = context.config
        sf_cfg = config.snowflake
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

    def _export_inferred_osi_artifacts(self, context: RunContext) -> None:
        """Write OSI JSON/YAML with the latest inferred column datatypes."""
        try:
            import json
            import yaml
            from semabridge.converter.sml_to_osi import SMLToOSIConverter

            if not context.sml_model:
                return

            osi_model = SMLToOSIConverter().to_osi(context.sml_model)
            context.osi_model = osi_model

            out_dir = Path("output")
            out_dir.mkdir(parents=True, exist_ok=True)
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

