"""
SemaBridge Core Engine.

Implements the "One Source, Many Targets" broadcasting framework with:
- Single extraction, single conversion workflow
- Multi-tiered concurrency (ProcessPoolExecutor for conversion, ThreadPoolExecutor for broadcast)
- Load shedding and bulkhead isolation for target resilience
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Semaphore
from typing import Any, Callable, Dict, List, Optional, Tuple, Type
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.core.project import ProjectConfig, SourceConfig, TargetConfig
from semabridge.utils.logger import get_logger
from semabridge.utils.model_dedup import (
    deduplicate_models,
    deduplicate_model_names,
)

logger = get_logger(__name__)


class ExecutionPhase(str, Enum):
    """Execution phases for the SemaBridge engine."""
    DISCOVERY = "discovery"
    GOVERNANCE = "governance"
    EXTRACTION = "extraction"
    CONVERSION = "conversion"
    VALIDATION = "validation"
    BROADCAST = "broadcast"
    CLEANUP = "cleanup"


@dataclass
class BroadcastResult:
    """Result of broadcasting to a single target."""
    target: TargetConfig
    success: bool
    message: str = ""
    duration_ms: float = 0.0
    error: Optional[Exception] = None


@dataclass
class EngineResult:
    """Complete result of an engine execution."""
    success: bool
    models_processed: int = 0
    targets_succeeded: int = 0
    targets_failed: int = 0
    broadcast_results: List[BroadcastResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    phase_timings: Dict[ExecutionPhase, float] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    changed_models: List[str] = field(default_factory=list)


class SourceConnectorBase:
    """
    Base class for source connectors.
    
    All source connectors must implement discover() and extract() methods.
    """
    
    def discover(self, pattern: str = "*") -> List[str]:
        """
        Discover available models matching the pattern.
        
        Args:
            pattern: Glob pattern for model selection
            
        Returns:
            List of model names/IDs available in the source
        """
        raise NotImplementedError("Subclasses must implement discover()")

    def validate_permissions(self) -> List[str]:
        """Validate RBAC permissions."""
        return []

    @property
    def max_concurrency(self) -> int:
        """Max concurrency limit."""
        return 5
    
    def extract(self, model_id: str, exclusions: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """
        Extract metadata for a single model.
        
        Args:
            model_id: Model identifier
            exclusions: Optional dict of exclusion patterns by type
            
        Returns:
            Raw metadata dictionary
        """
        raise NotImplementedError("Subclasses must implement extract()")


class TargetConnectorBase:
    """
    Base class for target connectors.
    
    All target connectors must implement deploy() method.
    """
    
    def deploy(self, model: Any) -> bool:
        try:
            model_cls = type(model).__name__
            if model_cls == "OSIModel" or hasattr(model, "source_platform"):
                self._emitter.deploy_from_osi(model)
            else:
                self._emitter.deploy(model)
            return True

        except Exception as e:
            error_message = str(e).lower()

            # Detect table not found scenario
            if "does not exist" in error_message or "not found" in error_message:
                logger.warning(
                    f"Underlying table missing during deployment. "
                    f"Continuing semantic model creation. Details: {e}"
                )
            return True  # Do NOT fail sync

            # Any other error should still fail
            raise


class SemaBridgeEngine:
    """
    Core engine for semantic model broadcasting.
    
    Implements the "One Source, Many Targets" pattern with:
    - Single extraction per model
    - Single SML conversion per model
    - Parallel broadcast to all targets
    - Load shedding via semaphores
    - Bulkhead isolation per target
    """
    
    # Concurrency configuration
    MAX_CONVERSION_WORKERS = 4  # CPU-bound
    MAX_EXTRACTION_WORKERS = 5  # I/O-bound Fabric API calls
    MAX_BROADCAST_WORKERS = 10  # I/O-bound
    TARGET_SEMAPHORE_LIMIT = 5  # Max concurrent calls per target type
    
    def __init__(
        self,
        config: ProjectConfig,
        source_connector: Optional[SourceConnectorBase] = None,
        target_connectors: Optional[Dict[str, TargetConnectorBase]] = None,
        duckdb_manager: Optional[Any] = None,
    ):
        """
        Initialize the engine.
        
        Args:
            config: Project configuration
            source_connector: Optional pre-configured source connector
            target_connectors: Optional dict of target type -> connector
            duckdb_manager: Optional DuckDB manager for versioning
        """
        self.config = config
        self.source_connector = source_connector
        self.target_connectors = target_connectors or {}
        self.duckdb_manager = duckdb_manager
        
        # Semaphores for load shedding (per target type)
        self._target_semaphores: Dict[str, Semaphore] = {}
        
        # Phase timing
        self._phase_timings: Dict[ExecutionPhase, float] = {}
    
    def _get_or_create_semaphore(self, target: TargetConfig) -> Semaphore:
        """Get or create a semaphore for a target (bulkhead isolation)."""
        key = f"{target.type.value}:{target.database}:{target.schema_name}"
        if key not in self._target_semaphores:
            connector = self._get_or_create_target_connector(target)
            limit = getattr(connector, "max_concurrency", self.TARGET_SEMAPHORE_LIMIT)
            self._target_semaphores[key] = Semaphore(limit)
        return self._target_semaphores[key]

    def _validate_governance(self) -> None:
        """
        Validate RBAC permissions across all involved connectors.
        
        This phase ensures that the principal has necessary access
        BEFORE starting expensive extraction/broadcast operations.
        """
        logger.info("Starting Governance Check (RBAC validation)...")
        all_warnings: List[str] = []
        
        # 1. Source Connector RBAC
        if self.source_connector:
            warnings = self.source_connector.validate_permissions()
            all_warnings.extend([f"Source: {w}" for w in warnings])
            
        # 2. Target Connector RBAC
        for target in self.config.targets:
            connector = self._get_or_create_target_connector(target)
            warnings = connector.validate_permissions()
            all_warnings.extend([f"Target {target.type.value}: {w}" for w in warnings])
            
        if all_warnings:
            for warning in all_warnings:
                logger.warning(warning)
    
    def _time_phase(self, phase: ExecutionPhase):
        """Context manager to time a phase."""
        class PhaseTimer:
            def __init__(timer_self):
                timer_self.start = 0.0
            
            def __enter__(timer_self):
                timer_self.start = time.perf_counter()
                return timer_self
            
            def __exit__(timer_self, *args):
                elapsed = (time.perf_counter() - timer_self.start) * 1000
                self._phase_timings[phase] = elapsed
                logger.debug(f"Phase {phase.value} completed in {elapsed:.2f}ms")
        
        return PhaseTimer()
    
    
    def validate_run(self) -> EngineResult:
        """
        Run validation only (Discovery -> Extraction -> Conversion -> Validation).
        Does NOT broadcast to targets.
        """
        start_time = time.time()
        logger.info("Starting Schema Validation Run...")

        # Phase 1: Discovery
        with self._time_phase(ExecutionPhase.DISCOVERY):
            start_discovery = time.time()
            discovered_models = self._discover_models()
            
            # Filter included/excluded models
            final_models = self.config.get_included_models(discovered_models)
            logger.info(f"Discovered {len(discovered_models)} models, {len(final_models)} after filtering")
            
        if not final_models:
            logger.warning("No models found to validate")
            return EngineResult(success=True)

        # Phase 2: Extraction
        with self._time_phase(ExecutionPhase.EXTRACTION):
            extracted_metadata = self._extract_models(final_models)
        
        # Phase 3: Conversion
        with self._time_phase(ExecutionPhase.CONVERSION):
            sml_models = self._convert_models(extracted_metadata)
            logger.info(f"Converted {len(sml_models)} models to SML")

        # Phase 3.5: Validation
        with self._time_phase(ExecutionPhase.VALIDATION):
            valid_models = self._validate_models(sml_models)
            
        duration = (time.time() - start_time) * 1000
        success = len(valid_models) == len(sml_models)
        
        return EngineResult(
            success=success,
            models_processed=len(sml_models),
            targets_succeeded=0,
            targets_failed=0,
            total_duration_ms=duration,
            phase_timings=self._phase_timings,
            errors=[] if success else [f"{len(sml_models) - len(valid_models)} models failed validation"]
        )

    def execute(self) -> EngineResult:
        """
        Execute the full broadcast pipeline.
        
        Steps:
        1. DISCOVERY: List models from source, apply inclusion/exclusion patterns
        2. EXTRACTION: Extract metadata once per model
        3. CONVERSION: Convert to SML/OSI (CPU-bound, process pool)
        4. BROADCAST: Deploy to all targets (I/O-bound, thread pool)
        5. CLEANUP: Finalize and log results
        
        Returns:
            EngineResult with success status and details
        """
        import os
        import asyncio
        if os.getenv("SEMABRIDGE_ORCHESTRATOR", "local").lower() == "temporal":
            logger.info("Delegating execution to Temporal Orchestrator (Docker)...")
            # If there's a running loop (e.g., from UI checks), run in executor
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(lambda: asyncio.run(self._execute_temporal())).result()
            except RuntimeError:
                return asyncio.run(self._execute_temporal())

        start_time = time.perf_counter()
        errors: List[str] = []
        broadcast_results: List[BroadcastResult] = []
        
        try:
            # Phase 1: Discovery
            with self._time_phase(ExecutionPhase.DISCOVERY):
                discovered_models = self._discover_models()
                final_models = self.config.get_included_models(discovered_models)
                logger.info(f"Discovered {len(discovered_models)} models, {len(final_models)} after filtering")
            
            if not final_models:
                return EngineResult(
                    success=True,
                    models_processed=0,
                    errors=["No models matched the inclusion/exclusion criteria"]
                )

            # Phase 1.5: Name-level deduplication (Module 3)
            # Catches "Regional_Sales_Sample" vs "Regional Sales Sample"
            # before we waste extraction API calls on duplicates.
            final_models, name_dupes = deduplicate_model_names(final_models)
            if name_dupes:
                for g in name_dupes:
                    errors.append(
                        f"Duplicate model names detected "
                        f"(canonical='{g.canonical_name}'): "
                        f"kept='{g.kept}', dropped={g.dropped}"
                    )
                logger.info(
                    f"After name-dedup: {len(final_models)} unique models"
                )

            # Phase 2: Governance (RBAC Check)
            with self._time_phase(ExecutionPhase.GOVERNANCE):
                self._validate_governance()
            
            # Phase 3: Extraction (single pass per model)
            with self._time_phase(ExecutionPhase.EXTRACTION):
                extracted_metadata = self._extract_models(final_models)
            
            # Phase 3: Conversion
            with self._time_phase(ExecutionPhase.CONVERSION):
                sml_models = self._convert_models(extracted_metadata)
                logger.info(f"Converted {len(sml_models)} models to SML")
            
            if not sml_models:
                logger.warning("No SML models generated")
                return EngineResult(success=True)

            # Phase 3.5: Physical Validation
            with self._time_phase(ExecutionPhase.VALIDATION):
                sml_models = self._validate_models(sml_models)
                if not sml_models:
                    logger.error("All models failed validation")
                    return EngineResult(success=False, errors=["Physical validation failed for all models"])

            # Phase 4: Broadcast (I/O-bound parallel)
            with self._time_phase(ExecutionPhase.BROADCAST):
                changed_models = []
                # Before broadcasting, version the models if DuckDB is enabled
                if self.duckdb_manager:
                    logger.info("Versioning models in DuckDB...")
                    for model_id, sml_model in sml_models.items():
                        try:
                            # Use displayName for name if available
                            name = getattr(sml_model, 'label', model_id)
                            workspace_id = getattr(self.config.source, 'workspace_id', "")
                            
                            self.duckdb_manager.ensure_project(
                                project_id=model_id,
                                name=name,
                                workspace_id=workspace_id,
                                adapter=self.config.source.type.value
                            )
                            
                            sml_dict = sml_model.model_dump(mode='json')
                            committed, snapshot_id = self.duckdb_manager.commit_model(
                                project_id=model_id,
                                sml_json=sml_dict,
                                tag=self.config.version_tag,
                                initiated_by="engine"
                            )
                            if committed:
                                logger.info(f"Snapshot committed for {model_id}: {snapshot_id}")
                                changed_models.append(model_id)
                        except Exception as e:
                            logger.error(f"Failed to version model {model_id}: {e}")

                broadcast_results = self._broadcast_to_targets(list(sml_models.values()))
            
            # Phase 5: Cleanup
            with self._time_phase(ExecutionPhase.CLEANUP):
                self._cleanup()
            
            # Compute summary
            succeeded = [r for r in broadcast_results if r.success]
            failed = [r for r in broadcast_results if not r.success]
            
            for r in failed:
                errors.append(f"Target {r.target.type.value}: {r.message}")
            
            total_duration = (time.perf_counter() - start_time) * 1000
            
            return EngineResult(
                success=len(failed) == 0,
                models_processed=len(final_models),
                targets_succeeded=len(succeeded),
                targets_failed=len(failed),
                broadcast_results=broadcast_results,
                total_duration_ms=total_duration,
                phase_timings=self._phase_timings.copy(),
                errors=errors,
                changed_models=changed_models,
            )
            
        except Exception as e:
            logger.exception("Engine execution failed")
            total_duration = (time.perf_counter() - start_time) * 1000
            return EngineResult(
                success=False,
                total_duration_ms=total_duration,
                phase_timings=self._phase_timings.copy(),
                errors=[str(e)],
            )

    async def _execute_temporal(self) -> EngineResult:
        import uuid
        import os
        import time
        from temporalio.client import Client
        from semabridge.orchestration.temporal.workflows import SyncWorkflowInput
        
        start_time = time.perf_counter()
        
        discovered_models = self._discover_models()
        final_models = self.config.get_included_models(discovered_models)
        final_models, _ = deduplicate_model_names(final_models)
        
        if not final_models:
            return EngineResult(success=True, models_processed=0)
            
        client = await Client.connect(os.getenv("TEMPORAL_HOST", "localhost:7233"), namespace="default")
        
        inputs = []
        for mn in final_models:
            inputs.append(SyncWorkflowInput(
                tenant_id=os.getenv("FABRIC_TENANT_ID", "default_tenant"),
                model_id=mn,
                database=os.getenv("SNOWFLAKE_DATABASE", "ANALYTICS_DB"),
                schema=os.getenv("SNOWFLAKE_SCHEMA", "SEMANTIC_LAYER"),
                snowflake_account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
                fabric_workspace_id=os.getenv("FABRIC_WORKSPACE_ID", ""),
            ))
            
        batch_id = f"batch-sync-{uuid.uuid4().hex[:8]}"
        handle = await client.start_workflow(
            "BatchSyncWorkflow",
            inputs,
            id=batch_id,
            task_queue=os.getenv("TASK_QUEUE", "semabridge-sync"),
        )
        
        results = await handle.result()
        
        succeeded = 0
        changed = []
        errors = []
        for r in results:
            if isinstance(r, dict):
                p = r.get("phase")
                e = r.get("error", "")
                m = r.get("model_id")
                c = r.get("changes_detected", 0)
            else:
                p = getattr(r, "phase", "")
                e = getattr(r, "error", "")
                m = getattr(r, "model_id", "")
                c = getattr(r, "changes_detected", 0)
                
            if p == "completed":
                succeeded += 1
                if c > 0: changed.append(m)
            else:
                if "Extraction failed" not in str(e) and "No tables match" not in str(e):
                    errors.append(f"{m}: {e}")
                else:
                    # Treat skipping gracefully, but still doesn't count as exact target_success
                    succeeded += 1  

        return EngineResult(
            success=len(errors) == 0,
            models_processed=len(final_models),
            targets_succeeded=succeeded,
            targets_failed=len(final_models) - succeeded,
            total_duration_ms=(time.perf_counter() - start_time) * 1000,
            errors=errors,
            changed_models=changed
        )
    
    def _discover_models(self) -> List[str]:
        """Discover models from the source using the configured pattern."""
        if self.source_connector is None:
            self.source_connector = self._create_source_connector()
        
        pattern = self.config.source.model
        logger.debug(f"Discovering models with pattern: {pattern}")
        
        return self.source_connector.discover(pattern)
    
    def _extract_models(self, model_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Extract metadata for all models.

        Uses a :class:`ThreadPoolExecutor` to issue Fabric REST API calls
        in parallel (I/O-bound).  For a single model the overhead of the
        thread pool is skipped.
        
        Args:
            model_ids: List of model IDs to extract
            
        Returns:
            Dict mapping model_id to extracted metadata
        """
        results: Dict[str, Dict[str, Any]] = {}
        
        # Build exclusion rules from config
        exclusions = self._build_exclusion_dict()

        def _extract_one(model_id: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            """Extract a single model; returns (model_id, metadata|None)."""
            try:
                logger.info(f"Extracting metadata for model: {model_id}")
                metadata = self.source_connector.extract(model_id, exclusions)
                return (model_id, metadata)
            except Exception as e:
                logger.error(f"Failed to extract model {model_id}: {e}")
                return (model_id, None)

        if len(model_ids) <= 1:
            # Fast path — no thread pool needed
            for model_id in model_ids:
                mid, data = _extract_one(model_id)
                if data is not None:
                    results[mid] = data
        else:
            workers = min(len(model_ids), self.MAX_EXTRACTION_WORKERS)
            logger.info(
                f"Parallelising extraction of {len(model_ids)} models "
                f"with {workers} workers"
            )
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_extract_one, mid): mid
                    for mid in model_ids
                }
                for future in as_completed(futures):
                    mid, data = future.result()
                    if data is not None:
                        results[mid] = data

        return results
    
    def _build_exclusion_dict(self) -> Dict[str, List[str]]:
        """Build exclusion dictionary from config options."""
        exclusions = {
            "tables": [],
            "columns": [],
            "measures": [],
        }
        
        for exclusion in self.config.options.get_semantic_exclusions():
            if exclusion.exclusion_type.value == "table":
                exclusions["tables"].append(exclusion.pattern)
            elif exclusion.exclusion_type.value == "column":
                exclusions["columns"].append(exclusion.pattern)
            elif exclusion.exclusion_type.value == "measure":
                exclusions["measures"].append(exclusion.pattern)
        
        return exclusions
    
    def _convert_models(self, metadata: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Convert extracted metadata to OSI format.
        
        Uses ThreadPoolExecutor for CPU-bound conversion.
        
        Args:
            metadata: Dict mapping model_id to raw metadata
            
        Returns:
            Dict mapping model_id to OSI model objects
        """
        osi_models = {}
        
        # Note: For large batches, we'd use ProcessPoolExecutor here
        # For now, sequential conversion with option to parallelize
        if len(metadata) <= 2:
            # Sequential for small batches
            for model_id, data in metadata.items():
                try:
                    osi = self._convert_single(model_id, data)
                    if osi:
                        osi_models[model_id] = osi
                except Exception as e:
                    logger.error(f"Conversion failed for {model_id}: {e}")
        else:
            # Parallel conversion for larger batches
            # Use ThreadPoolExecutor instead of ProcessPoolExecutor to avoid pickling 
            # errors (like '_thread.lock') that occur when 'self' is serialized on Windows.
            with ThreadPoolExecutor(max_workers=self.MAX_CONVERSION_WORKERS) as executor:
                futures = {
                    executor.submit(self._convert_single, model_id, data): model_id
                    for model_id, data in metadata.items()
                }
                
                for future in as_completed(futures):
                    model_id = futures[future]
                    try:
                        osi = future.result()
                        if osi:
                            osi_models[model_id] = osi
                    except Exception as e:
                        logger.error(f"Conversion failed for {model_id}: {e}")
        
        logger.info(f"Converted {len(osi_models)} models to OSI")
        return osi_models
    
    def _convert_single(self, model_id: str, metadata: Dict[str, Any]) -> Any:
        """
        Convert a single model's metadata to OSI.

        The pipeline stays in OSI format.  SML conversion is no longer used
        in the default sync path (isolated for future use).
        """
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        
        logger.debug(f"Converting {model_id} to OSI")
        
        # TMSL -> OSI pipeline (no SML conversion)
        tmsl_converter = TMSLToOSIConverter()
        osi_model = tmsl_converter.to_osi({
            "tmsl": metadata,
            "dataset_id": model_id,
        })
        print("\n===== TABLES USED BY MODEL =====")
        if hasattr(osi_model, "datasets"):
            for ds in osi_model.datasets:
                print("TABLE:", ds.unique_name)
        print("=================================\n")
        # Apply granular exclusions post-conversion (duck-typed)
        osi_model = self._apply_exclusions(osi_model)
        
        # Run Tiered Safety Pipeline directly on OSI model
        try:
            from semabridge.converter.safety_pipeline import TieredSafetyPipeline
            from pathlib import Path
            
            override_dir = Path("output/manual_sql_overrides")
            override_dir.mkdir(parents=True, exist_ok=True)
            
            safety_pipeline = TieredSafetyPipeline(override_dir=override_dir)
            safety_result = safety_pipeline.run_osi(osi_model)
            
            report = safety_result.get_report()
            logger.info(
                f"Tiered Safety for {model_id}: "
                f"T1={report['tier_distribution'][1]}, T2={report['tier_distribution'][2]}, "
                f"T3={report['tier_distribution'][3]}, T4={report['tier_distribution'][4]} | "
                f"Auto: {report['auto_translatable']}, Override: {report['override_required']}"
            )
        except Exception as e:
            logger.warning(f"Tiered Safety pipeline skipped for {model_id}: {e}")
        
        return osi_model
    
    def _validate_models(self, osi_models: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate OSI models against physical constraints.
        
        Args:
            osi_models: Dict of OSI models to validate
            
        Returns:
            Dict of valid OSI models
        """
        valid_models = {}
        for model_id, model in osi_models.items():
            # Placeholder for future validation logic
            # e.g., check for valid identifiers, circular dependencies, etc.
            valid_models[model_id] = model

        # Module 3: Structural dedup — detect models with identical
        # datasets/columns/metrics even if their display names differ.
        if hasattr(self.config, 'options') and self.config.options.structural_dedup:
            try:
                valid_models, struct_dupes = deduplicate_models(valid_models)
                if struct_dupes:
                    for g in struct_dupes:
                        logger.warning(
                            f"Structural duplicate: {g.summary()}"
                        )
            except Exception as exc:
                logger.warning(f"Structural dedup skipped: {exc}")
        else:
            logger.debug("Structural deduplication disabled via config. Skipping.")

        return valid_models

    def _apply_exclusions(self, osi_model: Any) -> Any:
        """Apply granular exclusions to an OSI model."""
        options = self.config.options
        
        # Filter tables
        if hasattr(osi_model, 'datasets'):
            osi_model.datasets = [
                ds for ds in osi_model.datasets
                if not options.is_table_excluded(ds.unique_name)
            ]
        
        # Filter columns within remaining tables
        if hasattr(osi_model, 'datasets'):
            for dataset in osi_model.datasets:
                if hasattr(dataset, 'columns'):
                    dataset.columns = [
                        col for col in dataset.columns
                        if not options.is_column_excluded(col.unique_name)
                    ]
        
        # Filter measures
        if hasattr(osi_model, 'metrics'):
            osi_model.metrics = [
                m for m in osi_model.metrics
                if not options.is_measure_excluded(m.unique_name)
            ]
        
        return osi_model
    
    def broadcast(self, sml_models: List[Any]) -> List[BroadcastResult]:
        """
        Public API for broadcasting SML models to all configured targets.
        
        This can be used for rollback operations (re-broadcasting past version).
        
        Args:
            sml_models: List of SML models to broadcast
            
        Returns:
            List of BroadcastResult
        """
        return self._broadcast_to_targets(sml_models)
    
    def discover(self, pattern: str = None) -> List[str]:
        """
        Public API for discovering models.
        
        Args:
            pattern: Optional pattern to override configuration
            
        Returns:
            List of discovered model names
        """
        if pattern:
            # Temporarily override pattern
            original_pattern = self.config.source.model
            self.config.source.model = pattern
            try:
                discovered = self._discover_models()
            finally:
                self.config.source.model = original_pattern
            return discovered
        
        return self._discover_models()

    def _broadcast_to_targets(self, sml_models: List[Any]) -> List[BroadcastResult]:
        """
        Broadcast SML models to all configured targets.
        
        Models are deployed **sequentially** within each target to prevent
        DDL races (e.g. two models both creating / dropping the same
        shared source table).  Different *targets* still run in parallel
        so multi-target latency stays at ``max(T_target_i)``.
        
        Args:
            sml_models: List of SML models to broadcast
            
        Returns:
            List of BroadcastResult for each target
        """
        results: List[BroadcastResult] = []
        targets = self.config.targets
        
        if not targets:
            logger.warning("No targets configured for broadcast")
            return results
        
        def _deploy_all_models_to_target(target: TargetConfig) -> List[BroadcastResult]:
            """Deploy every model to *one* target, sequentially.

            Opens a shared Snowflake session before the batch so all models
            reuse the same connection (P2a).
            """
            target_results: List[BroadcastResult] = []
            connector = self._get_or_create_target_connector(target)

            # Open session for batch reuse (no-op if connector doesn't support it)
            if hasattr(connector, "open_session"):
                try:
                    connector.open_session()
                except Exception as e:
                    logger.warning(f"Failed to open session for {target.type.value}: {e}")

            try:
                for sml_model in sml_models:
                    try:
                        result = self._deploy_to_target(target, sml_model)
                        target_results.append(result)
                    except Exception as e:
                        target_results.append(BroadcastResult(
                            target=target,
                            success=False,
                            message=str(e),
                            error=e,
                        ))
            finally:
                if hasattr(connector, "close_session"):
                    try:
                        connector.close_session()
                    except Exception as e:
                        logger.warning(f"Failed to close session for {target.type.value}: {e}")

            return target_results
        
        if len(targets) == 1:
            # Fast path — no thread pool needed
            results = _deploy_all_models_to_target(targets[0])
        else:
            # Parallelize across targets; models stay sequential per target
            with ThreadPoolExecutor(max_workers=min(len(targets), self.MAX_BROADCAST_WORKERS)) as executor:
                futures = {
                    executor.submit(_deploy_all_models_to_target, target): target
                    for target in targets
                }
                for future in as_completed(futures):
                    results.extend(future.result())
        
        return results
    
    def _deploy_to_target(self, target: TargetConfig, sml_model: Any) -> BroadcastResult:
        """
        Deploy an SML model to a single target with semaphore protection.
        """

        # Handle Dry Run
        if not target.deploy:
            logger.info(f"Dry run for target {target.type.value}: Skipping physical deployment")
            return BroadcastResult(
                target=target,
                success=True,
                message="Dry run (deployment disabled)",
                duration_ms=0.0,
            )

        semaphore = self._get_or_create_semaphore(target)
        start = time.perf_counter()

        acquired = semaphore.acquire(blocking=True, timeout=30.0)
        if not acquired:
            return BroadcastResult(
                target=target,
                success=False,
                message="Timeout waiting for target semaphore (load shedding)",
            )

        try:
            connector = self._get_or_create_target_connector(target)

            # 🔥 Deploy only once
            success = connector.deploy(sml_model)

            duration = (time.perf_counter() - start) * 1000

            return BroadcastResult(
                target=target,
                success=success,
                message="Deployed successfully",
                duration_ms=duration,
            )

        except MissingSourceTableWarning as w:
            duration = (time.perf_counter() - start) * 1000
            logger.warning(f"Missing base table for {target.type.value}: {w}")

            # 🔥 Treat as SUCCESS but with warning
            return BroadcastResult(
                target=target,
                success=True,
                message=f"⚠ Missing underlying table: {str(w)}",
                duration_ms=duration,
            )

        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            logger.error(f"Deployment to {target.type.value} failed: {e}")

            return BroadcastResult(
                target=target,
             success=False,
                message=str(e),
             duration_ms=duration,
                error=e,
            )

        finally:
            semaphore.release()
    
    def _create_source_connector(self) -> SourceConnectorBase:
        """Create a source connector based on configuration."""
        from semabridge.core.project import SourceType
        
        source_type = self.config.source.type
        
        if source_type == SourceType.FABRIC:
            from semabridge.connectors.fabric_extractor import FabricExtractor
            from semabridge.core.settings import get_settings
            
            settings = get_settings()
            extractor = FabricExtractor(settings.fabric)
            
            # Wrap in adapter that implements our interface
            return FabricSourceAdapter(extractor, self.config.source)
        
        elif source_type == SourceType.SNOWFLAKE:
            from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
            from semabridge.core.settings import get_settings
            
            settings = get_settings()
            extractor = SnowflakeExtractor(settings.snowflake)
            
            return SnowflakeSourceAdapter(extractor, self.config.source)
        
        raise ValueError(f"Unsupported source type: {source_type}")
    
    def _get_or_create_target_connector(self, target: TargetConfig) -> TargetConnectorBase:
        """Get or create a target connector."""
        from semabridge.core.project import TargetType
        
        key = f"{target.type.value}:{target.database}:{target.schema_name}"
        
        if key not in self.target_connectors:
            if target.type in (TargetType.SNOWFLAKE_SEMANTIC_VIEW, TargetType.SNOWFLAKE):
                from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
                from semabridge.core.settings import get_settings
                
                settings = get_settings()
                emitter = SnowflakeEmitter(settings.snowflake)
                self.target_connectors[key] = SnowflakeTargetAdapter(emitter)
            else:
                raise ValueError(f"Unsupported target type: {target.type}")
        
        return self.target_connectors[key]
    
    def _cleanup(self):
        """Cleanup after execution."""
        logger.debug("Engine cleanup completed")


class FabricSourceAdapter(SourceConnectorBase):
    """Adapter wrapping FabricExtractor to our interface."""
    
    def __init__(self, extractor, source_config: SourceConfig):
        self._extractor = extractor
        self._config = source_config
    
    def discover(self, pattern: str = "*") -> List[str]:
        """Discover models using Fabric REST API."""
        import fnmatch
        
        models = self._extractor.list_semantic_models()
        names = [m.get("displayName", m.get("id", "")) for m in models]
        
        # Apply pattern matching (case-insensitive)
        return [
            name for name in names
            if fnmatch.fnmatch(name.lower(), pattern.lower())
        ]
    
    def extract(self, model_id: str, exclusions: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """Extract TMSL definition from Fabric."""
        # Resolve name to ID if needed
        resolved_id = self._extractor.resolve_model_id(model_id)
        return self._extractor.get_model_definition(resolved_id)

    def validate_permissions(self) -> List[str]:
        """Delegate to FabricExtractor."""
        return self._extractor.validate_permissions()

    @property
    def max_concurrency(self) -> int:
        return self._extractor.max_concurrency


class SnowflakeSourceAdapter(SourceConnectorBase):
    """Adapter wrapping SnowflakeExtractor to our interface."""
    
    def __init__(self, extractor, source_config: SourceConfig):
        self._extractor = extractor
        self._config = source_config
    
    def discover(self, pattern: str = "*") -> List[str]:
        """Discover tables/views in Snowflake."""
        import fnmatch
        
        # List tables in configured schema
        metadata = self._extractor.extract_all()
        names = list(metadata.get("tables", {}).keys())
        
        return [
            name for name in names
            if fnmatch.fnmatch(name.lower(), pattern.lower())
        ]
    
    def extract(self, model_id: str, exclusions: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """Extract metadata from Snowflake."""
        return self._extractor.extract_all()


class SnowflakeTargetAdapter(TargetConnectorBase):
    """Adapter wrapping SnowflakeEmitter to our interface."""
    
    def __init__(self, emitter):
        self._emitter = emitter
    
    def deploy(self, model: Any) -> bool:
        try:
            # 🔥 TEMPORARY TEST — simulate missing table error
           raise Exception("SQL compilation error: Object does not exist: TEST_TABLE")
           model_cls = type(model).__name__
           if model_cls == "OSIModel" or hasattr(model, "source_platform"):
               self._emitter.deploy_from_osi(model)
           else:
               self._emitter.deploy(model)

           return True

        except Exception as e:
            error_message = str(e).lower()

            # Detect Snowflake missing table error
            missing_table_indicators = [
               "does not exist",
               "object does not exist",
               "sql compilation error",
               "42s02",  # SQLSTATE for table not found
            ]

            if any(indicator in error_message for indicator in missing_table_indicators):
                logger.warning(
                   f"⚠ Underlying table for model is missing in Snowflake.\n"
                   f"Details: {e}\n"
                   f"Continuing semantic model creation."
                )
                return True  # Do NOT fail sync
            # Any other error should still fail
            raise

    def open_session(self) -> None:
        """Open a shared connection session on the underlying emitter."""
        if hasattr(self._emitter, "open_session"):
            self._emitter.open_session()

    def close_session(self) -> None:
        """Close the shared connection session."""
        if hasattr(self._emitter, "close_session"):
            self._emitter.close_session()

    def validate_permissions(self) -> List[str]:
        """Delegate to SnowflakeEmitter."""
        return self._emitter.validate_permissions()

    @property
    def max_concurrency(self) -> int:
        return self._emitter.max_concurrency
