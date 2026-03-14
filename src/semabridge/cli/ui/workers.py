"""
Background Workers for SemaBridge UI.

QThread-based workers for running long operations without freezing the UI.
"""

from __future__ import annotations

from typing import Optional, Dict, Any, List

from PyQt6.QtCore import QThread, pyqtSignal

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SyncWorker(QThread):
    """
    Background worker for running semantic model synchronization.
    
    Executes SemaBridgeEngine.execute() in a separate thread and
    emits progress signals for UI updates.
    """
    
    # Signals
    progress = pyqtSignal(int, str)  # (percentage, status_message)
    finished = pyqtSignal(dict)       # EngineResult as dict
    error = pyqtSignal(str)           # Error message
    
    def __init__(self, config_path: Optional[str] = None, config: Optional[Any] = None, duckdb_manager: Optional[Any] = None):
        super().__init__()
        self.config_path = config_path or "semabridge.yaml"
        self.config = config
        self.duckdb_manager = duckdb_manager
        self._result: Optional[Dict[str, Any]] = None
    
    def run(self):
        """Execute the sync operation in background."""
        try:
            self.progress.emit(5, "Loading configuration...")
            
            from semabridge.core.engine import SemaBridgeEngine
            
            if self.config:
                 # Use injected config directly (already validated)
                 config = self.config
                 self.progress.emit(10, "Using validated configuration...")
            else:
                # Fallback to loading from settings (legacy/CLI path)
                from semabridge.core.settings import get_settings, reload_settings
                from semabridge.core.project import ProjectConfig, SourceConfig, TargetConfig
                
                # Force reload to pick up latest UI changes
                settings = reload_settings()
                
                self.progress.emit(10, "Building project configuration...")
                
                source_config = SourceConfig(
                    type=settings.source.type if hasattr(settings, 'source') else "fabric",
                    workspace_id=settings.fabric.workspace_id if hasattr(settings, 'fabric') else "",
                    model_pattern="*",
                )
                
                targets = []
                if hasattr(settings, 'targets') and settings.targets:
                    for t in settings.targets:
                        targets.append(TargetConfig(
                            type=t.get('type', 'snowflake_semantic_view'),
                            database=t.get('database', ''),
                            schema_name=t.get('schema', ''),
                        ))
                else:
                    # Default target from snowflake settings
                    targets.append(TargetConfig(
                        type="snowflake_semantic_view",
                        database=settings.snowflake.database if hasattr(settings, 'snowflake') else "",
                        schema_name=settings.snowflake.schema_name if hasattr(settings, 'snowflake') else "",
                    ))
                
                config = ProjectConfig(
                    name="SemaBridge Sync",
                    source=source_config,
                    targets=targets,
                )
            
            self.progress.emit(20, "Initializing engine...")
            
            # Initialize engine with DuckDB manager for versioning
            engine = SemaBridgeEngine(config, duckdb_manager=self.duckdb_manager)
            
            self.progress.emit(30, "Executing sync pipeline (Discovery -> Convert -> Broadcast)...")
            
            # Execute full pipeline (handles versioning, RBAC, etc.)
            result = engine.execute()
            
            # Convert EngineResult to dict for UI consumption
            result_dict = {
                "success": result.success,
                "models_processed": result.models_processed,
                "targets_succeeded": result.targets_succeeded,
                "targets_failed": result.targets_failed,
                "total_duration_ms": result.total_duration_ms,
                "errors": result.errors,
                "changed_models": result.changed_models if hasattr(result, "changed_models") else [],
                "broadcast_results": [
                    {"type": r.target.type.value, "success": r.success, "message": r.message, "deploy": r.target.deploy}
                    for r in (result.broadcast_results or [])
                ]
            }
            
            if result.success:
                self.progress.emit(100, f"Sync complete! ({result.total_duration_ms:.0f}ms)")
                self._result = result_dict
                self.finished.emit(result_dict)
            else:
                self.progress.emit(100, "Sync failed.")
                # We emit finished even on logical failure, distinct from worker crash
                self.finished.emit(result_dict)
            
        except Exception as e:
            logger.exception("Sync worker error")
            self.error.emit(str(e))


class ConnectionCheckWorker(QThread):
    """
    Background worker for checking source connection status.
    """
    
    finished = pyqtSignal(bool, str)  # (is_connected, status_message)
    
    def __init__(self, source_type: str = "fabric"):
        super().__init__()
        self.source_type = source_type
    
    def run(self):
        """Check connection status."""
        try:
            if self.source_type == "fabric":
                from semabridge.core.settings import get_settings
                from semabridge.connectors.fabric_extractor import FabricExtractor
                
                settings = get_settings()
                extractor = FabricExtractor(settings.fabric)
                
                # Try to list models - if it works, we're connected
                extractor.list_semantic_models()
                
                self.finished.emit(True, "Connected to Fabric")
            else:
                self.finished.emit(False, "Unknown source type")
                
        except Exception as e:
            logger.debug(f"Connection check failed: {e}")
            self.finished.emit(False, f"Disconnected: {str(e)[:30]}")


class RollbackWorker(QThread):
    """
    Background worker for executing rollback operations.
    """
    
    progress = pyqtSignal(str)  # Status message
    finished = pyqtSignal(bool, str)  # (success, message)
    
    def __init__(self, version_id: str, adapter: str = "snowflake"):
        super().__init__()
        self.version_id = version_id
        self.adapter = adapter
    
    def run(self):
        """Execute rollback to specified version."""
        try:
            self.progress.emit(f"Rolling back to {self.version_id}...")
            
            from semabridge.repository.semantic_version_manager import SemanticVersionManager
            
            manager = SemanticVersionManager()
            
            # Get the target version
            target_version = manager.get_version(self.version_id)
            if not target_version:
                self.finished.emit(False, f"Version {self.version_id} not found")
                return
            
            self.progress.emit("Loading version snapshot...")
            
            # Load the snapshot file
            snapshot_path = manager.metadata_dir / "snapshots" / f"{self.version_id}.json"
            if not snapshot_path.exists():
                self.finished.emit(False, f"Snapshot file not found for {self.version_id}")
                return
            
            import json
            with open(snapshot_path) as f:
                semantic_state = json.load(f)
            
            self.progress.emit("Creating rollback version...")
            
            # Get current version for lineage tracking
            current = manager.get_current_version(self.adapter)
            current_id = current.version_id if current else None
            
            # Create new rollback version
            new_version = manager.create_version_record(
                adapter=self.adapter,
                semantic_state=semantic_state,
                change_type="rollback",
                change_summary=f"Rollback to {self.version_id}",
                parent_version_id=current_id,
                rollback_metadata={"target_version": self.version_id},
            )
            
            # Link rollback operation
            if current_id:
                manager.link_rollback_operation(
                    from_version_id=current_id,
                    to_version_id=self.version_id,
                    rollback_version_id=new_version.version_id,
                    reason="UI rollback",
                )
            
            self.finished.emit(True, f"Rolled back to {self.version_id}")
            
        except Exception as e:
            logger.exception("Rollback worker error")
            self.finished.emit(False, str(e))


class ParallelSyncWorker(QThread):
    """
    Background worker for running concurrent multi-model synchronization.

    Uses the same :class:`SemaBridgeEngine` pipeline as :class:`SyncWorker`
    but processes each model independently, emitting per-model progress
    signals for real-time UI updates.
    """

    # Signals
    progress = pyqtSignal(int, str)  # (percentage, status_message)
    model_completed = pyqtSignal(str, bool, float)  # (model_name, success, duration_seconds)
    finished = pyqtSignal(dict)  # BatchResult as dict
    error = pyqtSignal(str)  # Error message

    def __init__(
        self,
        config: Optional[Any] = None,
        duckdb_manager: Optional[Any] = None,
        max_workers: int = 0,
        strict_mode: bool = False,
        max_retries: int = 3,
        resume_batch_id: Optional[str] = None,
    ):
        super().__init__()
        self.config = config
        self.duckdb_manager = duckdb_manager
        self.max_workers = max_workers
        self.strict_mode = strict_mode
        self.max_retries = max_retries
        self.resume_batch_id = resume_batch_id

    def run(self):
        """Execute the parallel sync in background."""
        import time
        import uuid
        from concurrent.futures import ThreadPoolExecutor, as_completed

        try:
            self.progress.emit(5, "Initializing parallel processing…")

            from semabridge.core.engine import SemaBridgeEngine

            if not self.config:
                self.error.emit("No configuration provided.")
                return

            batch_id = uuid.uuid4().hex[:12]
            start = time.time()

            # ── Step 1: Discover models using a temporary engine ──
            self.progress.emit(10, "Discovering models…")
            discovery_engine = SemaBridgeEngine(
                self.config, duckdb_manager=self.duckdb_manager
            )
            # Create source connector and discover
            try:
                discovery_engine.source_connector = (
                    discovery_engine._create_source_connector()
                )
                pattern = self.config.source.model
                all_models = discovery_engine.source_connector.discover(pattern)
                model_list = self.config.get_included_models(all_models)
            except Exception as exc:
                logger.exception("Model discovery failed")
                self.error.emit(f"Model discovery failed: {exc}")
                return

            if not model_list:
                self.finished.emit({
                    "success": True,
                    "batch_id": batch_id,
                    "total_models": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "successful_models": [],
                    "failed_models": {},
                    "duration_seconds": 0.0,
                })
                return

            total = len(model_list)
            self.progress.emit(
                15, f"Found {total} model(s) to process"
            )

            # ── Step 2: Process each model using SemaBridgeEngine ──
            successful: List[str] = []
            failed: Dict[str, str] = {}

            def _sync_single_model(model_name: str) -> Dict[str, Any]:
                """Run the full engine pipeline for a single model."""
                model_start = time.time()
                try:
                    # Create a per-model config copy with source.models = [model_name]
                    model_config = self.config.model_copy(deep=True)
                    model_config.source.models = [model_name]
                    model_config.source.model = model_name

                    engine = SemaBridgeEngine(
                        model_config, duckdb_manager=self.duckdb_manager
                    )
                    result = engine.execute()

                    duration = time.time() - model_start
                    return {
                        "model_name": model_name,
                        "success": result.success,
                        "duration": duration,
                        "error": "; ".join(result.errors) if result.errors else None,
                    }
                except Exception as exc:
                    duration = time.time() - model_start
                    return {
                        "model_name": model_name,
                        "success": False,
                        "duration": duration,
                        "error": str(exc),
                    }

            # Decide worker count
            import os
            if self.max_workers > 0:
                workers = min(self.max_workers, total)
            else:
                workers = min(total, max(1, (os.cpu_count() or 1) - 1))

            self.progress.emit(20, f"Processing {total} models with {workers} worker(s)…")

            from collections import defaultdict

            # 1. Group models by target (database, schema)
            groups = defaultdict(list)
            for model_name in model_list:
                # We need to peek at the target config for this model
                # Assuming all models in this worker share the same base config targets
                for target in self.config.targets:
                    key = (target.database, target.schema_name)
                    groups[key].append(model_name)
                    break # Only group by first target for now, common case

            completed_count = 0

            def _run_group_sequentially(group_models: List[str]):
                nonlocal completed_count
                for model_name in group_models:
                    res = _sync_single_model(model_name)
                    completed_count += 1
                    pct = 20 + int(80 * completed_count / total)

                    if res["success"]:
                        successful.append(res["model_name"])
                        self.model_completed.emit(res["model_name"], True, res["duration"])
                    else:
                        failed[res["model_name"]] = res["error"] or "unknown"
                        self.model_completed.emit(res["model_name"], False, res["duration"])
                        if self.strict_mode:
                            self.progress.emit(pct, f"Stopped: {res['model_name']} failed (strict mode)")
                            return
                    
                    self.progress.emit(pct, f"{completed_count}/{total} models processed")

            # 2. Parallel execution across DIFFERENT targets
            if len(groups) == 1:
                # Sequential — simpler, no thread overhead
                target_key = list(groups.keys())[0]
                _run_group_sequentially(groups[target_key])
            else:
                # Process groups in parallel
                with ThreadPoolExecutor(max_workers=min(len(groups), workers)) as executor:
                    futures = [
                        executor.submit(_run_group_sequentially, group_models)
                        for group_models in groups.values()
                    ]
                    for future in as_completed(futures):
                        future.result() # Catch errors if any


            # ── Step 3: Build result ──
            duration = time.time() - start
            result_dict = {
                "success": len(failed) == 0,
                "batch_id": batch_id,
                "total_models": total,
                "success_count": len(successful),
                "failure_count": len(failed),
                "successful_models": successful,
                "failed_models": failed,
                "duration_seconds": duration,
            }

            if not failed:
                self.progress.emit(100, f"All {total} models synced!")
            else:
                self.progress.emit(
                    100,
                    f"{len(successful)}/{total} succeeded, {len(failed)} failed",
                )

            self.finished.emit(result_dict)

        except Exception as e:
            logger.exception("ParallelSyncWorker error")
            self.error.emit(str(e))
