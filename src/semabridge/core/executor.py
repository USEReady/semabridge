"""
CLI Execution Engine.

Implements the mandatory 10-step execution flow for Semabridge CLI.
Each CLI invocation corresponds to exactly one Project and one Run.
"""

from __future__ import annotations

import os
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from semabridge.core.execution_config import ExecutionConfig
from semabridge.core.source_format import SourceFormat, from_snowflake_metadata, from_fabric_tmsl
from semabridge.core.run_summary import (
    RunSummary, RunStatus, StepStatus, STEP_NAMES, create_run_summary
)
from semabridge.core.settings import get_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class ExecutionError(Exception):
    """
    Execution error with step context.
    
    Provides actionable error messages with step number for debugging.
    """
    
    def __init__(self, step_number: int, message: str, cause: Optional[Exception] = None):
        self.step_number = step_number
        self.step_name = STEP_NAMES.get(step_number, f"Step {step_number}")
        self.message = message
        self.cause = cause
        super().__init__(f"[Step {step_number}] {self.step_name}: {message}")


class CLIExecutor:
    """
    Orchestrates the 10-step execution flow for CLI invocations.
    
    Each invocation = 1 Project + 1 Run.
    
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
    
    def __init__(self, config: ExecutionConfig):
        """
        Initialize the executor with validated configuration.

        .. deprecated::
            ``CLIExecutor`` is superseded by ``ExecutionEngine``, which provides
            the full OSI-compliant 10-step pipeline with telemetry and
            stored-procedure Cortex YAML deploy support.
            Migrate to::

                from semabridge.core.execution_engine import ExecutionEngine
                engine = ExecutionEngine()
                summary = engine.execute(source="fabric", target="snowflake", ...)

        Args:
            config: Validated ExecutionConfig from YAML
        """
        import warnings

        warnings.warn(
            "CLIExecutor is deprecated and will be removed in a future release. "
            "Use `semabridge.core.execution_engine.ExecutionEngine` instead, which "
            "provides the OSI-compliant pipeline, hybrid DAX translation, Cortex YAML "
            "stored-procedure deploy, and OpenTelemetry observability.",
            DeprecationWarning,
            stacklevel=2,
        )

        self.config = config
        self.project_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.summary: Optional[RunSummary] = None
        
        # Internal state
        self._source_format: Optional[SourceFormat] = None
        self._sml_model: Optional[Any] = None
        self._target_format: Optional[Any] = None
        self._artifacts: List[str] = []
        
        # Database manager for persistence
        self._db_manager: Optional[ModelRepository] = None
    
    @classmethod
    def from_yaml(cls, config_path: Path) -> "CLIExecutor":
        """
        Create executor from YAML configuration file.
        
        This performs Step 1: Load and Validate Configuration.
        
        Args:
            config_path: Path to YAML configuration file
            
        Returns:
            CLIExecutor instance ready to execute
            
        Raises:
            ExecutionError: If configuration is invalid
        """
        try:
            config = ExecutionConfig.from_yaml(config_path)
            return cls(config)
        except FileNotFoundError as e:
            raise ExecutionError(1, f"Configuration file not found: {config_path}", e)
        except ValueError as e:
            raise ExecutionError(1, f"Configuration validation failed: {e}", e)
    
    def execute(self) -> RunSummary:
        """
        Execute the full 10-step flow.
        
        Returns:
            RunSummary with final status and metadata
        """
        self.started_at = datetime.utcnow()
        
        try:
            # Step 1: Load and Validate Configuration (already done in from_yaml)
            self._step_1_validate_config()
            
            # Step 2: Initialize Identifiers
            self._step_2_init_identifiers()
            
            # Step 3: Resolve Authentication
            self._step_3_resolve_auth()
            
            # Step 4: Extract from Source
            self._step_4_extract()
            
            # Step 5: Validate and Parse Source Format
            self._step_5_validate_parse()
            
            # Step 6: Convert to Canonical SML
            self._step_6_convert_to_sml()
            
            # Step 7: Persist Artifacts
            self._step_7_persist_artifacts()
            
            # Step 8: Convert to Target Format (Optional)
            self._step_8_convert_to_target()
            
            # Step 9: Deploy to Target (Optional)
            self._step_9_deploy()
            
            # Step 10: Finalize Run
            return self._step_10_finalize(RunStatus.SUCCESS)
            
        except ExecutionError as e:
            logger.error(f"Execution failed: {e}")
            return self._step_10_finalize(RunStatus.FAILED, error=e)
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            exec_error = ExecutionError(
                step_number=self.summary.last_successful_step + 1 if self.summary else 1,
                message=str(e),
                cause=e
            )
            return self._step_10_finalize(RunStatus.FAILED, error=exec_error)
    
    def _step_1_validate_config(self) -> None:
        """
        Step 1: Load and Validate Configuration.
        
        Validates:
        - Required keys are present
        - Connector types are supported
        - No inline secrets
        """
        step_start = time.time()
        logger.info("Step 1: Validating configuration...")
        
        # Configuration already validated in from_yaml() or __init__
        # Perform additional runtime validation here
        
        if not self.config.model_name:
            raise ExecutionError(1, "model_name is required")
        
        if not self.config.source:
            raise ExecutionError(1, "source configuration is required")
        
        logger.info(f"  Source type: {self.config.source.type}")
        if self.config.target:
            logger.info(f"  Target type: {self.config.target.type}")
        
        duration_ms = int((time.time() - step_start) * 1000)
        if self.summary:
            self.summary.add_step(1, STEP_NAMES[1], StepStatus.SUCCESS, 
                                   message="Configuration valid", duration_ms=duration_ms)
    
    def _step_2_init_identifiers(self) -> None:
        """
        Step 2: Initialize Identifiers.
        
        Generates unique project_id and run_id at the very start.
        These identifiers are propagated through all stages.
        """
        step_start = time.time()
        logger.info("Step 2: Initializing identifiers...")
        
        # Generate unique identifiers
        self.run_id = str(uuid.uuid4())
        
        # Project ID: Use existing if reverse-syncing, generate new otherwise
        if self.config.source.type == "fabric" and self.config.source.dataset_id:
            # For Fabric source, use dataset_id as project_id
            self.project_id = self.config.source.dataset_id
        else:
            # For Snowflake source, use model_name as project_id
            self.project_id = self.config.model_name
        
        logger.info(f"  Project ID: {self.project_id}")
        logger.info(f"  Run ID: {self.run_id}")
        
        # Initialize run summary
        self.summary = create_run_summary(
            project_id=self.project_id,
            run_id=self.run_id,
            source_type=self.config.source.type,
            target_type=self.config.target.type if self.config.target else None,
        )
        
        # Initialize database manager
        self._db_manager = ModelRepository()
        
        # Record run start
        self._db_manager.record_run_start(
            run_id=self.run_id,
            project_id=self.project_id,
            source_type=self.config.source.type,
            target_type=self.config.target.type if self.config.target else None,
        )
        
        duration_ms = int((time.time() - step_start) * 1000)
        self.summary.add_step(2, STEP_NAMES[2], StepStatus.SUCCESS,
                               message=f"IDs: {self.project_id[:8]}..., {self.run_id[:8]}...",
                               duration_ms=duration_ms)
    
    def _step_3_resolve_auth(self) -> None:
        """
        Step 3: Resolve Authentication.
        
        Resolves credentials strictly from environment variables (via .env file).
        Validates all required variables for source and target connectors.
        Does NOT allow inline secrets in configuration files.
        """
        step_start = time.time()
        logger.info("Step 3: Resolving authentication...")
        
        # Force reload settings to ensure we have latest .env values
        from semabridge.core.settings import reload_settings
        settings = reload_settings()
        
        errors = []
        
        # Validate source authentication using Settings (which reads from .env)
        if self.config.source.type == "snowflake":
            try:
                # This will raise if any required Snowflake vars are missing
                sf = settings.snowflake
                # Access properties to trigger validation
                _ = sf.account
                _ = sf.user
                _ = sf.password
                _ = sf.warehouse
                _ = sf.database
            except Exception as e:
                errors.append(f"Snowflake: {e}")
                
        elif self.config.source.type == "fabric":
            try:
                # This will raise if any required Fabric vars are missing
                fb = settings.fabric
                _ = fb.tenant_id
                _ = fb.client_id
                _ = fb.client_secret
                _ = fb.workspace_id
            except Exception as e:
                errors.append(f"Fabric: {e}")
        
        # Validate target authentication (if configured)
        if self.config.target:
            if self.config.target.type == "snowflake":
                try:
                    sf = settings.snowflake
                    _ = sf.account
                    _ = sf.user
                    _ = sf.password
                    _ = sf.warehouse
                    _ = sf.database
                except Exception as e:
                    if f"Snowflake: {e}" not in errors:
                        errors.append(f"Snowflake: {e}")
                        
            elif self.config.target.type == "fabric":
                try:
                    fb = settings.fabric
                    _ = fb.tenant_id
                    _ = fb.client_id
                    _ = fb.client_secret
                    _ = fb.workspace_id
                except Exception as e:
                    if f"Fabric: {e}" not in errors:
                        errors.append(f"Fabric: {e}")
        
        if errors:
            raise ExecutionError(
                3, 
                f"Authentication failed - check .env file: {'; '.join(errors)}"
            )
        
        logger.info("  Authentication resolved from environment")
        
        duration_ms = int((time.time() - step_start) * 1000)
        self.summary.add_step(3, STEP_NAMES[3], StepStatus.SUCCESS,
                               message="Credentials verified", duration_ms=duration_ms)
    
    def _step_4_extract(self) -> None:
        """
        Step 4: Extract from Source.
        
        Connects to the configured source system and extracts the semantic model.
        Normalizes the extracted content into a Source Format artifact.
        """
        step_start = time.time()
        logger.info("Step 4: Extracting from source...")
        
        from semabridge.core.settings import reload_settings
        settings = reload_settings()
        
        # Pre-flight connectivity check for Snowflake
        if self.config.source.type == "snowflake":
            self._preflight_snowflake_check(settings)
        
        if self.config.source.type == "snowflake":
            self._source_format = self._extract_from_snowflake(settings)
        elif self.config.source.type == "fabric":
            self._source_format = self._extract_from_fabric(settings)
        else:
            raise ExecutionError(4, f"Unsupported source type: {self.config.source.type}")
        
        logger.info(f"  Extracted from {self.config.source.type}")
        
        duration_ms = int((time.time() - step_start) * 1000)
        self.summary.add_step(4, STEP_NAMES[4], StepStatus.SUCCESS,
                               message=f"Extracted from {self.config.source.type}",
                               duration_ms=duration_ms)
    
    def _preflight_snowflake_check(self, settings) -> None:
        """
        Pre-flight connectivity check for Snowflake.
        
        Validates:
        1. Connection can be established
        2. Database is visible to current role
        3. Schema exists and is accessible
        """
        import snowflake.connector
        
        sf = settings.snowflake
        logger.info("  Pre-flight: Checking Snowflake connectivity...")
        
        try:
            conn = snowflake.connector.connect(
                user=sf.user,
                password=sf.password.get_secret_value(),
                account=sf.account,
                warehouse=sf.warehouse,
                database=sf.database,
                schema=sf.schema_name,
                role=sf.role,
            )
            
            try:
                cur = conn.cursor()
                
                # Check 1: Current role and context
                cur.execute("SELECT CURRENT_ROLE(), CURRENT_DATABASE(), CURRENT_SCHEMA()")
                role, db, schema = cur.fetchone()
                logger.info(f"  Pre-flight: Role={role}, Database={db}, Schema={schema}")
                
                if not db:
                    raise ExecutionError(
                        4, 
                        f"Database '{sf.database}' not accessible with role '{role}'. "
                        f"Check that the role has USAGE privilege on the database."
                    )
                
                # Check 2: Schema visibility
                cur.execute(f"SHOW SCHEMAS IN DATABASE {sf.database}")
                schemas = {row[1].upper() for row in cur.fetchall()}
                
                if sf.schema_name.upper() not in schemas:
                    raise ExecutionError(
                        4,
                        f"Schema '{sf.schema_name}' not found in database '{sf.database}'. "
                        f"Available schemas: {', '.join(sorted(schemas)[:5])}..."
                    )
                
                logger.info("  Pre-flight: Connectivity verified")
                
            finally:
                conn.close()
                
        except snowflake.connector.errors.ProgrammingError as e:
            error_msg = str(e)
            if "Role" in error_msg and "not granted" in error_msg:
                raise ExecutionError(
                    4,
                    f"Role '{sf.role}' is not granted to user '{sf.user}'. "
                    f"Update SNOWFLAKE_ROLE in .env to a role granted to this user."
                )
            elif "Failed to connect" in error_msg:
                raise ExecutionError(
                    4,
                    f"Failed to connect to Snowflake: {error_msg}. "
                    f"Check account name, credentials, and network connectivity."
                )
            else:
                raise ExecutionError(4, f"Snowflake pre-flight check failed: {error_msg}")
        except Exception as e:
            raise ExecutionError(4, f"Pre-flight connectivity check failed: {e}")
    
    def _extract_from_snowflake(self, settings) -> SourceFormat:
        """Extract from Snowflake and create Source Format."""
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.utils.cache import MetadataCache
        
        cache = MetadataCache(settings.model.cache_dir) if settings.model.cache_enabled else None
        
        extractor = SnowflakeExtractor(
            config=settings.snowflake,
            cache=cache,
            exclude_tables=settings.model.excluded_table_list,
            include_tables=settings.model.included_table_list,
        )
        
        metadata = extractor.extract_all()
        
        return from_snowflake_metadata(
            project_id=self.project_id,
            run_id=self.run_id,
            metadata=metadata,
        )
    
    def _extract_from_fabric(self, settings) -> SourceFormat:
        """Extract from Fabric and create Source Format."""
        from semabridge.connectors.fabric_extractor import FabricExtractor
        
        extractor = FabricExtractor(settings.fabric)
        
        dataset_id = self.config.source.dataset_id
        if not dataset_id:
            raise ExecutionError(4, "dataset_id is required for Fabric source")
        
        tmsl = extractor.get_model_definition(dataset_id)
        row_counts = extractor.get_table_row_counts(dataset_id)
        
        workspace_id = self.config.source.workspace_id or settings.fabric.workspace_id
        
        return from_fabric_tmsl(
            project_id=self.project_id,
            run_id=self.run_id,
            tmsl=tmsl,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            row_counts=row_counts,
        )
    
    def _step_5_validate_parse(self) -> None:
        """
        Step 5: Validate and Parse Source Format.
        
        Applies format definition validation.
        Parses the Source Format using explicit parsing instructions.
        Fails with actionable diagnostics if validation or parsing fails.
        """
        step_start = time.time()
        logger.info("Step 5: Validating and parsing source format...")
        
        if not self._source_format:
            raise ExecutionError(5, "No source format to validate (Step 4 output missing)")
        
        # Validate format
        issues = self._source_format.validate_format()
        
        # Check for errors
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        
        if errors:
            diagnostic = self._source_format.get_diagnostic_message()
            raise ExecutionError(5, f"Source format validation failed:\n{diagnostic}")
        
        if warnings:
            for warning in warnings:
                logger.warning(f"  {warning.code}: {warning.message}")
        
        logger.info("  Source format valid")
        
        duration_ms = int((time.time() - step_start) * 1000)
        self.summary.add_step(5, STEP_NAMES[5], StepStatus.SUCCESS,
                               message=f"Valid ({len(warnings)} warnings)",
                               duration_ms=duration_ms)
    
    def _step_6_convert_to_sml(self) -> None:
        """
        Step 6: Convert to Canonical SML.
        
        Maps the validated Source Format into canonical SML.
        Ensures schema correctness and semantic consistency.
        """
        step_start = time.time()
        logger.info("Step 6: Converting to canonical SML...")
        
        if not self._source_format:
            raise ExecutionError(6, "No source format to convert (Step 5 output missing)")
        
        settings = get_settings()
        
        if self.config.source.type == "snowflake":
            self._sml_model = self._convert_snowflake_to_sml(settings)
        elif self.config.source.type == "fabric":
            self._sml_model = self._convert_fabric_to_sml(settings)
        else:
            raise ExecutionError(6, f"No SML converter for source type: {self.config.source.type}")
        
        logger.info(f"  Converted to SML ({self._sml_model.metric_count} metrics)")
        
        duration_ms = int((time.time() - step_start) * 1000)
        self.summary.add_step(6, STEP_NAMES[6], StepStatus.SUCCESS,
                               message=f"{self._sml_model.dataset_count} datasets, {self._sml_model.metric_count} metrics",
                               duration_ms=duration_ms)
    
    def _convert_snowflake_to_sml(self, settings):
        """Convert Snowflake source format to SML."""
        from semabridge.sml.assembler import SMLAssembler
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.connectors.measure_detector import MeasureDetector
        from semabridge.connectors.inference_engine import SmlInferenceEngine
        
        # Build metadata dict from source format
        metadata = {
            "database": self._source_format.database,
            "schema": self._source_format.schema_name,
            "tables": {name: {"description": info.description, "row_count": info.row_count} 
                       for name, info in self._source_format.tables.items()},
            "columns": {name: [col.model_dump() for col in cols] 
                        for name, cols in self._source_format.columns.items()},
            "foreign_keys": [fk.model_dump() for fk in self._source_format.foreign_keys],
            "primary_keys": self._source_format.primary_keys,
        }
        
        assembler = SMLAssembler(
            model_name=self.config.model_name,
            description=settings.model.description,
            source_database=metadata.get("database", ""),
            source_schema=metadata.get("schema", ""),
        )
        
        # Add tables
        for table_name, table_info in metadata.get("tables", {}).items():
            columns = metadata.get("columns", {}).get(table_name, [])
            assembler.add_table(
                table_name=table_name,
                columns=columns,
                description=table_info.get("description", ""),
                row_count=table_info.get("row_count"),
            )
        
        # Detect relationships
        rel_detector = RelationshipDetector(
            tables=metadata.get("tables", {}),
            columns=metadata.get("columns", {}),
            primary_keys=metadata.get("primary_keys", {}),
            explicit_fks=metadata.get("foreign_keys", []),
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
            tables=metadata.get("tables", {}),
            columns=metadata.get("columns", {}),
            relationships=relationships,
            primary_keys=metadata.get("primary_keys", {})
        )
        scores = engine.classify()
        
        classification_map = {}
        for ds in assembler._datasets:
            score = scores.get(ds.unique_name)
            if score:
                classification = score.classification
                classification_map[ds.unique_name] = classification
                ds.is_fact = classification == "FACT"
        
        # Detect measures
        measure_detector = MeasureDetector(
            tables=metadata.get("tables", {}),
            columns=metadata.get("columns", {}),
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
        
        return assembler.build()
    
    def _convert_fabric_to_sml(self, settings):
        """Convert Fabric source format to SML."""
        from semabridge.converter.tmsl_to_sml import TMSLTransformer

        behavior = getattr(self.config, "behavior", None)
        metric_overrides = (
            behavior.semantic_model.metric_overrides if behavior is not None else {}
        )
        transformer = TMSLTransformer()
        return transformer.transform(
            self._source_format.tmsl_definition,
            self._source_format.workspace_id,
            self._source_format.dataset_id,
            row_counts=self._source_format.row_counts,
            metric_overrides=metric_overrides,
            behavior=behavior,
        )
    
    def _step_7_persist_artifacts(self) -> None:
        """
        Step 7: Persist Artifacts.
        
        Persists:
        - Source Format artifact
        - Canonical SML artifact
        - Validation and conversion reports
        - Execution metadata (project_id, run_id, timestamps)
        """
        step_start = time.time()
        logger.info("Step 7: Persisting artifacts...")
        
        if not self._db_manager:
            raise ExecutionError(7, "Database manager not initialized")
        
        artifact_ids = []
        
        # Persist Source Format artifact
        if self._source_format:
            source_artifact_id = self._db_manager.persist_source_artifact(
                run_id=self.run_id,
                source_format=self._source_format,
            )
            if source_artifact_id:
                artifact_ids.append(source_artifact_id)
                self.summary.source_artifact_id = source_artifact_id
                logger.info(f"  Persisted source artifact: {source_artifact_id[:12]}...")
        
        # Persist SML artifact (via commit_model)
        if self._sml_model:
            settings = get_settings()
            workspace_id = (
                self.config.source.workspace_id or 
                (settings.fabric.workspace_id if self.config.source.type == "fabric" else None) or
                "local"
            )
            
            self._db_manager.ensure_project(
                self.project_id, 
                self.config.model_name, 
                workspace_id,
                adapter=self.config.source.type
            )
            
            sml_dict = self._sml_model.model_dump(mode='json')
            committed, snapshot_id = self._db_manager.commit_model(
                project_id=self.project_id,
                sml_json=sml_dict,
                tag=self.config.version_tag,
                status="success",
                run_id=self.run_id,
            )
            
            if committed:
                artifact_ids.append(snapshot_id)
                self.summary.sml_snapshot_id = snapshot_id
                logger.info(f"  Committed SML snapshot: {snapshot_id[:12]}...")
            else:
                logger.info("  No changes to SML (using existing snapshot)")
        
        self._artifacts = artifact_ids
        
        duration_ms = int((time.time() - step_start) * 1000)
        self.summary.add_step(7, STEP_NAMES[7], StepStatus.SUCCESS,
                               message=f"{len(artifact_ids)} artifacts persisted",
                               duration_ms=duration_ms,
                               artifact_ids=artifact_ids)
    
    def _step_8_convert_to_target(self) -> None:
        """
        Step 8: Convert to Target Format (Optional).
        
        If a target connector is configured:
        - Converts SML into the Target Format using the appropriate target rule pack.
        - Validates generated output before deployment.
        """
        step_start = time.time()
        
        if not self.config.target:
            logger.info("Step 8: Skipped (no target configured)")
            self.summary.add_step(8, STEP_NAMES[8], StepStatus.SKIPPED,
                                   message="No target configured")
            return
        
        logger.info("Step 8: Converting to target format...")
        
        settings = get_settings()
        
        if self.config.target.type == "snowflake":
            self._target_format = self._convert_sml_to_snowflake(settings)
        elif self.config.target.type == "fabric":
            self._target_format = self._convert_sml_to_fabric(settings)
        else:
            raise ExecutionError(8, f"Unsupported target type: {self.config.target.type}")
        
        logger.info(f"  Converted to {self.config.target.type} format")
        
        duration_ms = int((time.time() - step_start) * 1000)
        self.summary.add_step(8, STEP_NAMES[8], StepStatus.SUCCESS,
                               message=f"Converted to {self.config.target.type}",
                               duration_ms=duration_ms)
    
    def _convert_sml_to_snowflake(self, settings):
        """Convert SML to Snowflake DDL."""
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        
        emitter = SnowflakeEmitter(settings.snowflake, behavior=self.config.behavior)
        ddls = emitter.generate_ddls(self._sml_model)
        return {"ddls": ddls, "emitter": emitter}
    
    def _convert_sml_to_fabric(self, settings):
        """Convert SML to Fabric model.bim."""
        from semabridge.connectors.tmsl_generator import TMSLGenerator
        
        generator = TMSLGenerator(
            self._sml_model,
            snowflake_server=settings.snowflake.account,
            snowflake_warehouse=settings.snowflake.warehouse,
            snowflake_database=settings.snowflake.database,
            snowflake_schema=settings.snowflake.schema_name,
        )
        return {"generator": generator}
    
    def _step_9_deploy(self) -> None:
        """
        Step 9: Deploy to Target (Optional).
        
        If deployment is enabled:
        - Emits or deploys the Target Format to the target system via the target connector.
        - Handles partial deployment failures explicitly.
        """
        step_start = time.time()
        
        if not self.config.target:
            logger.info("Step 9: Skipped (no target configured)")
            self.summary.add_step(9, STEP_NAMES[9], StepStatus.SKIPPED,
                                   message="No target configured")
            return
        
        if not self.config.target.deploy:
            logger.info("Step 9: Skipped (deployment not enabled)")
            self.summary.add_step(9, STEP_NAMES[9], StepStatus.SKIPPED,
                                   message="Deployment not enabled")
            return
        
        logger.info("Step 9: Deploying to target...")
        
        settings = get_settings()
        deployed_artifacts = []
        
        try:
            if self.config.target.type == "snowflake":
                path = self._deploy_to_snowflake(settings)
                deployed_artifacts.append(path)
            elif self.config.target.type == "fabric":
                result = self._deploy_to_fabric(settings)
                deployed_artifacts.append(result.get("id", "unknown"))
            
            logger.info(f"  Deployed to {self.config.target.type}")
            
            duration_ms = int((time.time() - step_start) * 1000)
            self.summary.add_step(9, STEP_NAMES[9], StepStatus.SUCCESS,
                                   message=f"Deployed to {self.config.target.type}",
                                   duration_ms=duration_ms,
                                   artifact_ids=deployed_artifacts)
            self.summary.target_artifact_path = str(deployed_artifacts[0]) if deployed_artifacts else None
            
        except Exception as e:
            # Handle partial deployment failures
            logger.warning(f"  Partial deployment failure: {e}")
            self.summary.add_step(9, STEP_NAMES[9], StepStatus.FAILED,
                                   message=f"Partial failure: {e}")
            self.summary.add_error(9, STEP_NAMES[9], e)
            # Don't raise - allow finalization with PARTIAL status
    
    def _deploy_to_snowflake(self, settings) -> str:
        """Deploy to Snowflake."""
        if not self._target_format:
            raise ExecutionError(9, "No target format to deploy")
        
        emitter = self._target_format.get("emitter")
        if not emitter:
            from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
            emitter = SnowflakeEmitter(settings.snowflake, behavior=self.config.behavior)
        
        emitter.deploy(self._sml_model)
        
        # Save DDL to output file
        output_path = Path("output/reverse/semantic_view.sql")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        ddls = self._target_format.get("ddls", [])
        with open(output_path, "w") as f:
            f.write("\n\n".join(ddls))
        
        return str(output_path)
    
    def _deploy_to_fabric(self, settings) -> Dict[str, Any]:
        """Deploy to Fabric."""
        from semabridge.connectors.fabric_publisher import FabricPublisher
        
        publisher = FabricPublisher(settings.fabric)
        return publisher.publish(
            sml_model=self._sml_model,
            model_name=self.config.model_name,
            snowflake_server=settings.snowflake.account,
            snowflake_warehouse=settings.snowflake.warehouse,
            snowflake_database=settings.snowflake.database,
            snowflake_schema=settings.snowflake.schema_name,
            overwrite=self.config.behavior.fabric.deploy_overwrite,
        )
    
    def _step_10_finalize(
        self, 
        status: RunStatus, 
        error: Optional[ExecutionError] = None
    ) -> RunSummary:
        """
        Step 10: Finalize Run.
        
        Records final run status:
        - SUCCESS: All steps completed successfully
        - FAILED: One or more steps failed
        - PARTIAL: Some steps failed (e.g., partial deployment)
        
        Produces a structured run summary and outputs to CLI.
        """
        step_start = time.time()
        logger.info(f"Step 10: Finalizing run ({status.value})...")
        
        # Ensure summary exists
        if not self.summary:
            self.summary = create_run_summary(
                project_id=self.project_id or "unknown",
                run_id=self.run_id or str(uuid.uuid4()),
                source_type=self.config.source.type,
                target_type=self.config.target.type if self.config.target else None,
            )
        
        # Update status
        self.summary.status = status
        
        # Add error if failed
        if error:
            self.summary.add_error(
                error.step_number,
                error.step_name,
                error.cause or error,
                include_traceback=True
            )
        
        # Add final step
        duration_ms = int((time.time() - step_start) * 1000)
        self.summary.add_step(10, STEP_NAMES[10], StepStatus.SUCCESS,
                               message=f"Status: {status.value}",
                               duration_ms=duration_ms)
        
        # Finalize summary (calculate total duration)
        self.summary.finalize()
        
        # Record run completion in database
        if self._db_manager:
            try:
                self._db_manager.record_run_complete(
                    run_id=self.run_id,
                    status=status.value.lower(),
                    final_step=self.summary.last_successful_step,
                    duration_ms=self.summary.duration_ms or 0,
                    error_message=str(error) if error else None,
                )
            except Exception as e:
                logger.warning(f"Failed to record run completion: {e}")
        
        logger.info(f"  Run complete: {status.value}")
        logger.info(f"  Duration: {self.summary.duration_ms}ms")
        
        return self.summary
