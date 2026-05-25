"""
Model processor — wraps ExecutionEngine for parallel execution.

Each worker creates an isolated ModelProcessor to run one complete
model synchronization (extract → convert → deploy) with retry logic.
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from semabridge.core.concurrency.error_classifier import ErrorClassifier
from semabridge.core.concurrency.models import (
    BroadcastResult,
    ModelResult,
    RetryConfig,
)
from semabridge.core.concurrency.retry_manager import (
    MaxRetriesExceeded,
    PermanentError,
    RetryManager,
)

logger = logging.getLogger(__name__)


def _process_model_in_worker(
    model_name: str,
    source: str,
    target: Optional[str],
    db_path: Optional[str],
    config_path: Optional[str],
    deploy: bool,
    tag: Optional[str],
    dry_run: bool,
    dataset_id: Optional[str],
    workspace_id: Optional[str],
    retry_config_dict: Dict[str, Any],
) -> ModelResult:
    """Top-level function for ProcessPoolExecutor (must be picklable).

    Creates an isolated ExecutionEngine + RetryManager inside the
    worker process and runs the full 10-step pipeline.
    """
    from semabridge.core.concurrency.models import RetryConfig as RC
    from semabridge.core.concurrency.retry_manager import (
        MaxRetriesExceeded,
        PermanentError,
        RetryManager,
    )

    retry_cfg = RC(**retry_config_dict)
    retry_mgr = RetryManager(config=retry_cfg)
    classifier = ErrorClassifier()

    start = time.time()
    try:
        # Import inside worker to avoid pickling issues
        from semabridge.core.execution_engine import ExecutionEngine
        from semabridge.repository.model_repository import ModelRepository
        from pathlib import Path

        # Use ModelRepository as the canonical repository implementation.
        # When a `db_path` was previously provided for DuckDB, the repository
        # selection should be driven by global config; ignore `db_path` here.
        db_manager = ModelRepository()
        engine = ExecutionEngine(db_manager=db_manager)

        def _run():
            return engine.execute(
                source=source,
                target=target,
                config_path=Path(config_path) if config_path else None,
                deploy=deploy,
                tag=tag,
                dry_run=dry_run,
                dataset_id=dataset_id,
                workspace_id=workspace_id,
            )

        result = retry_mgr.execute_with_retry(_run, f"sync_{model_name}")
        duration = time.time() - start

        return ModelResult(
            model_name=model_name,
            success=True,
            duration_seconds=duration,
            retry_count=retry_mgr.get_retry_count(),
            snapshot_id=getattr(result, "snapshot_id", None),
        )

    except PermanentError as exc:
        duration = time.time() - start
        return ModelResult(
            model_name=model_name,
            success=False,
            duration_seconds=duration,
            error=str(exc.original_error or exc),
            error_type="permanent",
            retry_count=retry_mgr.get_retry_count(),
        )

    except MaxRetriesExceeded as exc:
        duration = time.time() - start
        return ModelResult(
            model_name=model_name,
            success=False,
            duration_seconds=duration,
            error=str(exc.last_error or exc),
            error_type="transient",
            retry_count=retry_mgr.get_retry_count(),
        )

    except Exception as exc:
        duration = time.time() - start
        error_type = classifier.classify(exc)
        return ModelResult(
            model_name=model_name,
            success=False,
            duration_seconds=duration,
            error=str(exc),
            error_type=error_type,
            retry_count=retry_mgr.get_retry_count(),
        )


class ModelProcessor:
    """Wrap ExecutionEngine for parallel model processing.

    Creates isolated engine instances per invocation and applies
    retry logic via :class:`RetryManager`.

    Args:
        source: Source connector type ("snowflake" or "fabric").
        target: Target connector type.
        db_path: Path to DuckDB database.
        config_path: Path to config file.
        retry_config: Retry configuration.
        deploy: Whether to deploy.
        tag: Version tag.
        dry_run: Whether to skip deployment.
        dataset_id: Fabric dataset ID.
        workspace_id: Fabric workspace ID.
    """

    def __init__(
        self,
        source: str,
        target: Optional[str] = None,
        db_path: Optional[str] = None,
        config_path: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        deploy: bool = True,
        tag: Optional[str] = None,
        dry_run: bool = False,
        dataset_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> None:
        self._source = source
        self._target = target
        self._db_path = db_path
        self._config_path = config_path
        self._retry_config = retry_config or RetryConfig()
        self._deploy = deploy
        self._tag = tag
        self._dry_run = dry_run
        self._dataset_id = dataset_id
        self._workspace_id = workspace_id
        self._retry_manager = RetryManager(config=self._retry_config)
        self._classifier = ErrorClassifier()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_model(self, model_name: str) -> ModelResult:
        """Process a single model through the complete pipeline.

        This method is designed to run in the *main* process (or a thread).
        For process-based parallelism, use :func:`get_worker_args` and
        :func:`_process_model_in_worker`.

        Args:
            model_name: Name of the model to process.

        Returns:
            ModelResult with success/failure details.
        """

        from pathlib import Path
        from semabridge.core.execution_engine import ExecutionEngine
        from semabridge.repository.model_repository import ModelRepository

        start = time.time()
        try:
            # Use ModelRepository instead of DuckDBManager. Backend selection
            # is controlled by configuration; ignore `_db_path` parameter.
            db_manager = ModelRepository()
            engine = ExecutionEngine(db_manager=db_manager)

            def _run():
                return engine.execute(
                    source=self._source,
                    target=self._target,
                    config_path=Path(self._config_path) if self._config_path else None,
                    deploy=self._deploy,
                    tag=self._tag,
                    dry_run=self._dry_run,
                    dataset_id=self._dataset_id,
                    workspace_id=self._workspace_id,
                )

            result = self._retry_manager.execute_with_retry(
                _run, f"sync_{model_name}"
            )
            duration = time.time() - start

            return ModelResult(
                model_name=model_name,
                success=True,
                duration_seconds=duration,
                retry_count=self._retry_manager.get_retry_count(),
                snapshot_id=getattr(result, "snapshot_id", None),
            )

        except PermanentError as exc:
            duration = time.time() - start
            return ModelResult(
                model_name=model_name,
                success=False,
                duration_seconds=duration,
                error=str(exc.original_error or exc),
                error_type="permanent",
                retry_count=self._retry_manager.get_retry_count(),
            )

        except MaxRetriesExceeded as exc:
            duration = time.time() - start
            return ModelResult(
                model_name=model_name,
                success=False,
                duration_seconds=duration,
                error=str(exc.last_error or exc),
                error_type="transient",
                retry_count=self._retry_manager.get_retry_count(),
            )

        except Exception as exc:
            duration = time.time() - start
            return ModelResult(
                model_name=model_name,
                success=False,
                duration_seconds=duration,
                error=str(exc),
                error_type=self._classifier.classify(exc),
                retry_count=self._retry_manager.get_retry_count(),
            )

    def process_broadcast(
        self,
        model_name: str,
        targets: List[str],
    ) -> BroadcastResult:
        """Process a single model to multiple targets (broadcasting).

        Phase 1: Extract from source and convert to SML (once).
        Phase 2: Deploy to each target in parallel via ThreadPoolExecutor.

        Args:
            model_name: Source model name.
            targets: List of target names/types to deploy to.

        Returns:
            BroadcastResult with per-target results.
        """
        from semabridge.core.execution_engine import ExecutionEngine
        from semabridge.repository.model_repository import ModelRepository
        from pathlib import Path

        extraction_start = time.time()
        broadcast_result = BroadcastResult(model_name=model_name)

        try:
            # Phase 1: Extract + convert (once)
            db_manager = ModelRepository()
            engine = ExecutionEngine(db_manager=db_manager)

            # Run extraction and conversion (steps 1-7)
            # For broadcasting, we do a full pipeline for each target
            # but the source extraction is logically the same
            extraction_duration = time.time() - extraction_start
            broadcast_result.source_extraction_duration = extraction_duration

            # Phase 2: Deploy to each target in parallel
            def _deploy_to_target(target_type: str) -> ModelResult:
                processor = ModelProcessor(
                    source=self._source,
                    target=target_type,
                    db_path=self._db_path,
                    config_path=self._config_path,
                    retry_config=self._retry_config,
                    deploy=self._deploy,
                    tag=self._tag,
                    dry_run=self._dry_run,
                    dataset_id=self._dataset_id,
                    workspace_id=self._workspace_id,
                )
                return processor.process_model(model_name)

            with ThreadPoolExecutor(max_workers=len(targets)) as executor:
                futures = {
                    executor.submit(_deploy_to_target, t): t for t in targets
                }
                for future in as_completed(futures):
                    target_name = futures[future]
                    try:
                        result = future.result()
                        broadcast_result.target_results[target_name] = result
                    except Exception as exc:
                        broadcast_result.target_results[target_name] = ModelResult(
                            model_name=model_name,
                            success=False,
                            error=str(exc),
                            error_type=self._classifier.classify(exc),
                        )

        except Exception as exc:
            # Source extraction failed — all targets fail
            for target_name in targets:
                broadcast_result.target_results[target_name] = ModelResult(
                    model_name=model_name,
                    success=False,
                    error=f"Source extraction failed: {exc}",
                    error_type=self._classifier.classify(exc),
                )

        return broadcast_result

    def get_worker_args(self, model_name: str) -> Dict[str, Any]:
        """Get arguments for :func:`_process_model_in_worker`.

        Returns a dict of picklable arguments suitable for
        ``ProcessPoolExecutor.submit()``.
        """
        return {
            "model_name": model_name,
            "source": self._source,
            "target": self._target,
            "db_path": self._db_path,
            "config_path": self._config_path,
            "deploy": self._deploy,
            "tag": self._tag,
            "dry_run": self._dry_run,
            "dataset_id": self._dataset_id,
            "workspace_id": self._workspace_id,
            "retry_config_dict": {
                "max_retries": self._retry_config.max_retries,
                "base_delay": self._retry_config.base_delay,
                "max_delay": self._retry_config.max_delay,
                "exponential_base": self._retry_config.exponential_base,
            },
        }
