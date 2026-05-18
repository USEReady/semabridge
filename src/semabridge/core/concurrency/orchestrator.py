"""
Concurrency orchestrator — main coordinator for parallel model processing.

Uses ProcessPoolExecutor to dispatch models across worker processes,
integrating ProgressReporter, RetryManager, ValidationEngine,
ResourceManager, ResumeManager, and FileManager.
"""

from __future__ import annotations

import logging
import signal
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed

from semabridge.core.concurrency.config_resolver import ConfigurationResolver
from semabridge.core.concurrency.error_classifier import ErrorClassifier
from semabridge.core.concurrency.file_manager import FileManager
from semabridge.core.concurrency.model_processor import (
    ModelProcessor,
    _process_model_in_worker,
)
from semabridge.core.concurrency.models import (
    BatchResult,
    ConcurrencyConfig,
    ExecutionMode,
    ModelResult,
    ProcessingStage,
    RetryConfig,
)
from semabridge.core.concurrency.progress_reporter import ProgressReporter
from semabridge.core.concurrency.resource_manager import ResourceManager
from semabridge.core.concurrency.resume_manager import ResumeManager
from semabridge.core.concurrency.validation_engine import ValidationEngine

logger = logging.getLogger(__name__)


class ConcurrencyOrchestrator:
    """Main coordinator for parallel model synchronization.

    Brings together all concurrency subsystems:

    - **ResourceManager** — determines worker count;
    - **ValidationEngine** — fail-fast pre-flight checks;
    - **ModelProcessor** — wraps ExecutionEngine + retry;
    - **ProgressReporter** — live Rich dashboard;
    - **ResumeManager** — persist partial results;
    - **FileManager** — atomic artifact writes;
    - **ErrorClassifier** — classify transient vs permanent.

    Args:
        config: Project configuration.
        concurrency_config: Concurrency-specific settings.
        retry_config: Retry policy.
        source: Source connector type.
        target: Target connector type.
        db_path: Path to DuckDB database file.
        config_path: Path to semabridge config file.
        deploy: Whether to deploy to target.
        tag: Version tag.
        dry_run: Skip deployment.
        dataset_id: Fabric dataset ID.
        workspace_id: Fabric workspace ID.
        mode: Execution mode (best-effort / strict).
        show_dashboard: Whether to show the Rich progress dashboard.
        db_manager: Optional DuckDBManager for resume persistence.
    """

    def __init__(
        self,
        config,
        concurrency_config: ConcurrencyConfig | None = None,
        retry_config: RetryConfig | None = None,
        source: str = "snowflake",
        target: str | None = None,
        db_path: str | None = None,
        config_path: str | None = None,
        deploy: bool = True,
        tag: str | None = None,
        dry_run: bool = False,
        dataset_id: str | None = None,
        workspace_id: str | None = None,
        mode: ExecutionMode = ExecutionMode.BEST_EFFORT,
        show_dashboard: bool = True,
        db_manager=None,
    ) -> None:
        self._config = config
        self._cc = concurrency_config or ConcurrencyConfig()
        self._retry_config = retry_config or RetryConfig()
        self._source = source
        self._target = target
        self._db_path = db_path
        self._config_path = config_path
        self._deploy = deploy
        self._tag = tag
        self._dry_run = dry_run
        self._dataset_id = dataset_id
        self._workspace_id = workspace_id
        self._mode = mode
        self._show_dashboard = show_dashboard
        self._db_manager = db_manager

        # Sub-systems
        self._resource_mgr = ResourceManager(
            max_processes=self._cc.max_processes,
            memory_threshold_percent=self._cc.memory_threshold_percent,
        )
        self._classifier = ErrorClassifier()
        self._file_mgr = FileManager(base_path=db_path or ".")
        self._resume_mgr = (
            ResumeManager(db_manager) if db_manager else None
        )
        self._validation = ValidationEngine(config)
        self._config_resolver = ConfigurationResolver(config)

        # Graceful shutdown
        self._shutdown_requested = False
        self._original_sigint = None
        self._original_sigterm = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        models: list[str] | None = None,
    ) -> BatchResult:
        """Execute parallel model synchronization.

        Args:
            models: Explicit list of models; if *None*, resolve from config.

        Returns:
            BatchResult with per-model outcomes.
        """
        batch_id = uuid.uuid4().hex[:12]
        start = time.time()

        # 1. Resolve model list
        model_list = models or self._resolve_models()
        if not model_list:
            logger.warning("No models to process")
            return BatchResult(
                batch_id=batch_id,
                total_models=0,
                successful_models=[],
                failed_models=[],
            )

        # 2. Validate pre-conditions
        validation = self._validation.validate_all()
        if not validation.success:
            msg = "; ".join(validation.errors)
            logger.error("Pre-flight validation failed: %s", msg)
            if self._mode == ExecutionMode.STRICT:
                raise RuntimeError(f"Validation failed: {msg}")
            # In best effort, log but continue (some models may still work)
            logger.warning("Continuing in best-effort mode despite validation issues")

        # 3. Calculate worker count
        worker_count = self._resource_mgr.calculate_worker_count(len(model_list))
        logger.info(
            "Processing %d models with %d workers (mode=%s)",
            len(model_list),
            worker_count,
            self._mode.value,
        )

        # 4. Start progress dashboard
        reporter = ProgressReporter(
            total_models=len(model_list),
            show_dashboard=self._show_dashboard,
        )
        reporter.start()

        # 5. Dispatch to workers
        results: list[ModelResult] = []
        try:
            self._install_signal_handlers()
            results = self._dispatch_models(
                model_list, worker_count, reporter
            )
        except KeyboardInterrupt:
            logger.warning("Interrupted — saving partial results")
            self._shutdown_requested = True
        finally:
            self._restore_signal_handlers()
            reporter.stop()

        # 6. Build batch result
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        batch = BatchResult(
            batch_id=batch_id,
            total_models=len(model_list),
            successful_models=successful,
            failed_models=failed,
            total_duration_seconds=time.time() - start,
        )

        # 7. Persist resume state if there are failures
        if failed and self._resume_mgr:
            context = {
                "source": self._source,
                "target": self._target,
                "config_path": self._config_path,
                "deploy": self._deploy,
                "tag": self._tag,
                "dry_run": self._dry_run,
                "dataset_id": self._dataset_id,
                "workspace_id": self._workspace_id,
            }
            try:
                self._resume_mgr.save_batch_state(
                    batch_id=batch_id,
                    failed_models=[r.model_name for r in failed],
                    successful_models=[r.model_name for r in successful],
                    context=context,
                )
            except Exception as exc:
                logger.warning("Failed to persist resume state: %s", exc)

        duration = time.time() - start
        logger.info(
            "Batch %s completed in %.1fs — %d succeeded, %d failed",
            batch_id,
            duration,
            len(successful),
            len(failed),
        )

        return batch

    def resume(self, batch_id: str) -> BatchResult:
        """Resume processing of failed models from a previous batch.

        Args:
            batch_id: The batch identifier to resume.

        Returns:
            BatchResult for the resumed models.

        Raises:
            RuntimeError: If no ResumeManager is available.
            ValueError: If the batch_id is not found.
        """
        if not self._resume_mgr:
            raise RuntimeError("Resume requires a db_manager")

        failed_models = self._resume_mgr.get_failed_models(batch_id)
        if not failed_models:
            logger.info("No failed models to resume for batch %s", batch_id)
            return BatchResult(
                batch_id=batch_id,
                total_models=0,
                successful_models=[],
                failed_models=[],
            )

        logger.info(
            "Resuming %d failed models from batch %s",
            len(failed_models),
            batch_id,
        )

        result = self.run(models=failed_models)

        # Link the resume run to the original batch
        try:
            self._resume_mgr.link_resume_to_original(batch_id, result.batch_id)
            if not result.failed_models:
                self._resume_mgr.mark_batch_complete(batch_id)
        except Exception as exc:
            logger.warning("Failed to update resume audit trail: %s", exc)

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_models(self) -> list[str]:
        """Resolve model list from configuration.

        Uses explicit ``source.models`` when available, falls back to
        ``source.model`` as a single-item list (unless it is a wildcard).
        For wildcard patterns the engine would normally query the live source
        to enumerate models; to keep the orchestrator lightweight we treat
        the wildcard as "all models" and expect the caller to pass an
        explicit list via ``run(models=[...])`` when source enumeration
        is needed.
        """
        try:
            cfg = self._config

            # 1. Explicit model list in config takes priority
            if cfg.source.models:
                available = list(cfg.source.models)
                return self._config_resolver.resolve_models(available)

            # 2. Single non-wildcard model name
            if cfg.source.model and cfg.source.model != "*":
                available = [cfg.source.model]
                return self._config_resolver.resolve_models(available)

            # 3. model_name field on ProjectConfig
            if getattr(cfg, "model_name", None):
                available = [cfg.model_name]
                return self._config_resolver.resolve_models(available)

            # 4. Wildcard — cannot enumerate without querying the live source
            logger.warning(
                "Source pattern is '*' with no explicit model list. "
                "Pass models explicitly via run(models=[...]) or set "
                "source.models in your config."
            )
            return []
        except Exception as exc:
            logger.error("Failed to resolve models: %s", exc)
            return []

    def _dispatch_models(
        self,
        models: list[str],
        worker_count: int,
        reporter: ProgressReporter,
    ) -> list[ModelResult]:
        """Submit models to ProcessPoolExecutor and collect results."""
        processor = ModelProcessor(
            source=self._source,
            target=self._target,
            db_path=self._db_path,
            config_path=self._config_path,
            retry_config=self._retry_config,
            deploy=self._deploy,
            tag=self._tag,
            dry_run=self._dry_run,
            dataset_id=self._dataset_id,
            workspace_id=self._workspace_id,
        )

        results: list[ModelResult] = []

        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            future_to_model = {}
            for model_name in models:
                if self._shutdown_requested:
                    break

                args = processor.get_worker_args(model_name)
                future = executor.submit(_process_model_in_worker, **args)
                future_to_model[future] = model_name

                reporter.update_worker_status(
                    worker_id=f"worker-{len(future_to_model)}",
                    model_name=model_name,
                    stage=ProcessingStage.EXTRACTING,
                )

            for future in as_completed(future_to_model):
                model_name = future_to_model[future]
                try:
                    result = future.result()
                    results.append(result)

                    if result.success:
                        reporter.report_success(
                            model_name, result.duration_seconds
                        )
                    else:
                        reporter.report_failure(
                            model_name, result.error or "Unknown"
                        )

                        # In strict mode, cancel remaining futures
                        if self._mode == ExecutionMode.STRICT:
                            logger.warning(
                                "Strict mode — cancelling remaining after %s failure",
                                model_name,
                            )
                            for f in future_to_model:
                                f.cancel()
                            break

                except Exception as exc:
                    error_msg = str(exc)
                    results.append(
                        ModelResult(
                            model_name=model_name,
                            success=False,
                            error=error_msg,
                            error_type=self._classifier.classify(exc),
                        )
                    )
                    reporter.report_failure(model_name, error_msg)

                    if self._mode == ExecutionMode.STRICT:
                        for f in future_to_model:
                            f.cancel()
                        break

        return results

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        """Install graceful shutdown handlers for SIGINT/SIGTERM."""
        try:
            self._original_sigint = signal.getsignal(signal.SIGINT)
            self._original_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGINT, self._handle_shutdown)
            signal.signal(signal.SIGTERM, self._handle_shutdown)
        except (OSError, ValueError):
            # Can only be set in main thread
            pass

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        try:
            if self._original_sigint is not None:
                signal.signal(signal.SIGINT, self._original_sigint)
            if self._original_sigterm is not None:
                signal.signal(signal.SIGTERM, self._original_sigterm)
        except (OSError, ValueError):
            pass

    def _handle_shutdown(self, signum, frame) -> None:
        """Handle shutdown signal gracefully."""
        logger.warning(
            "Received signal %s — requesting graceful shutdown", signum
        )
        self._shutdown_requested = True
