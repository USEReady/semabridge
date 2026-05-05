"""
Sync Orchestrator.

Central coordinator for bidirectional PBIX ↔ Snowflake ↔ Power BI
synchronization. Manages the end-to-end lifecycle of sync jobs:

    1. Create a SyncJob with items from source discovery.
    2. For each item: Extract → Convert to OSI → Detect Conflicts →
       Track Schema Evolution → Deploy to Target.
    3. Persist results, update mappings, save checkpoints.

Supports parallel item processing via TracedThreadPoolExecutor and
resumable execution via checkpoints.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from semabridge.core.exceptions import ConflictError, SyncError
from semabridge.intermediate.models import OSIModel
from semabridge.sync.conflict_resolver import ConflictResolver
from semabridge.sync.models import (
    ConflictResolution,
    ModelMapping,
    SyncCheckpoint,
    SyncConfig,
    SyncDirection,
    SyncItemStatus,
    SyncJob,
    SyncJobItem,
    SyncJobStatus,
    _new_id,
    _utc_now,
)
from semabridge.sync.repository import SyncRepository
from semabridge.sync.schema_evolution import SchemaEvolutionTracker
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import RelationshipNameTracker

logger = get_logger(__name__)


class SyncOrchestrator:
    """
    End-to-end orchestrator for bidirectional synchronization.

    Usage::

        repo = SyncRepository()
        orchestrator = SyncOrchestrator(repo)

        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            pbix_folder="/path/to/pbix/files",
            snowflake_schema="SEMANTIC_MODELS",
        )

        job = orchestrator.run(config)
        print(f"Job {job.job_id}: {job.status.value}")

    Args:
        repository: SyncRepository for persistence.
        extractor_factory: Optional factory to create extractors.
        deployer_factory: Optional factory to create deployers.
    """

    def __init__(
        self,
        repository: SyncRepository,
        extractor_factory: Optional[Callable] = None,
        deployer_factory: Optional[Callable] = None,
    ) -> None:
        self._repo = repository
        self._conflict_resolver = ConflictResolver(repository)
        self._schema_tracker = SchemaEvolutionTracker(repository)
        self._extractor_factory = extractor_factory
        self._deployer_factory = deployer_factory

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def run(
        self,
        config: SyncConfig,
        initiated_by: str = "cli",
        resume_job_id: Optional[str] = None,
    ) -> SyncJob:
        """
        Execute a synchronization job.

        Args:
            config: Sync configuration.
            initiated_by: Who initiated the job (cli, api, scheduler).
            resume_job_id: If set, resume a previously paused/failed job.

        Returns:
            Completed (or paused) SyncJob.

        Raises:
            SyncError: If the sync cannot be started.
            ConflictError: If unresolved conflicts block progress.
        """
        start_time = time.time()

        # --- Resume or create job ---
        if resume_job_id:
            job = self._resume_job(resume_job_id)
        else:
            job = self._create_job(config, initiated_by)

        job.status = SyncJobStatus.RUNNING
        job.started_at = _utc_now()
        self._repo.update_job(job)

        logger.info(
            f"Starting sync job {job.job_id} "
            f"[{job.direction.value}] with {job.total_items} items"
        )

        try:
            # --- Determine start index from checkpoint ---
            start_index = 0
            if resume_job_id:
                checkpoint = self._repo.get_checkpoint(job.job_id)
                if checkpoint:
                    start_index = checkpoint.last_processed_index + 1
                    logger.info(f"Resuming from index {start_index}")

            # --- Process items ---
            if config.enable_parallel and config.max_workers > 1:
                self._process_items_parallel(job, config, start_index)
            else:
                self._process_items_sequential(job, config, start_index)

            # --- Finalize ---
            job.update_counts()

            if job.failed_items > 0 and job.completed_items == 0:
                job.status = SyncJobStatus.FAILED
                job.error_message = f"All {job.failed_items} items failed"
            elif any(
                not c.is_resolved
                for c in self._repo.get_unresolved_conflicts(job.job_id)
            ):
                job.status = SyncJobStatus.CONFLICT
            else:
                job.status = SyncJobStatus.COMPLETED

        except ConflictError as e:
            job.status = SyncJobStatus.CONFLICT
            job.error_message = str(e)
            logger.warning(f"Sync job {job.job_id} paused: {e}")
        except Exception as e:
            job.status = SyncJobStatus.FAILED
            job.error_message = str(e)
            logger.error(f"Sync job {job.job_id} failed: {e}")

        job.completed_at = _utc_now()
        job.duration_ms = int((time.time() - start_time) * 1000)
        job.update_counts()
        self._repo.update_job(job)

        logger.info(
            f"Sync job {job.job_id} finished: status={job.status.value} "
            f"completed={job.completed_items}/{job.total_items} "
            f"failed={job.failed_items} duration={job.duration_ms}ms"
        )
        return job

    def cancel(self, job_id: str) -> SyncJob:
        """
        Cancel a running or paused sync job.

        Args:
            job_id: Job to cancel.

        Returns:
            Updated SyncJob.
        """
        job = self._repo.get_job(job_id)
        if job is None:
            raise SyncError(f"Job {job_id} not found", job_id=job_id)

        job.status = SyncJobStatus.CANCELLED
        job.completed_at = _utc_now()
        self._repo.update_job(job)
        logger.info(f"Cancelled sync job {job_id}")
        return job

    def resolve_and_resume(
        self,
        job_id: str,
        resolution: ConflictResolution,
        resolved_by: str = "user",
    ) -> SyncJob:
        """
        Resolve all conflicts on a paused job and resume execution.

        Args:
            job_id: Job to resume.
            resolution: How to resolve the conflicts.
            resolved_by: Who resolved them.

        Returns:
            Completed SyncJob after resumption.
        """
        count = self._conflict_resolver.resolve_all(job_id, resolution, resolved_by)
        logger.info(f"Resolved {count} conflicts for job {job_id}, resuming…")

        job = self._repo.get_job(job_id)
        if job is None:
            raise SyncError(f"Job {job_id} not found", job_id=job_id)

        # Re-construct config from job metadata
        config = SyncConfig(
            direction=job.direction,
            conflict_resolution=resolution,
            pbix_folder=job.source_folder,
            snowflake_schema=job.target_snowflake_schema,
            fabric_workspace_id=job.target_workspace_id,
        )
        return self.run(config, initiated_by="user", resume_job_id=job_id)

    def get_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get detailed status of a sync job.

        Returns:
            Dict with job metadata, item statuses, and conflicts.
        """
        job = self._repo.get_job(job_id)
        if job is None:
            raise SyncError(f"Job {job_id} not found", job_id=job_id)

        items = self._repo.get_items_for_job(job_id)
        conflicts = self._repo.get_conflicts_for_job(job_id)

        return {
            "job": job.model_dump(),
            "items": [i.model_dump() for i in items],
            "conflicts": [c.model_dump() for c in conflicts],
            "summary": {
                "total": len(items),
                "completed": sum(1 for i in items if i.status == SyncItemStatus.COMPLETED),
                "failed": sum(1 for i in items if i.status == SyncItemStatus.FAILED),
                "pending": sum(
                    1
                    for i in items
                    if i.status in (SyncItemStatus.QUEUED, SyncItemStatus.EXTRACTING)
                ),
                "unresolved_conflicts": sum(1 for c in conflicts if not c.is_resolved),
            },
        }

    # -----------------------------------------------------------------
    # Job Creation & Resumption
    # -----------------------------------------------------------------

    def _create_job(self, config: SyncConfig, initiated_by: str) -> SyncJob:
        """Create a new SyncJob and discover items from the source."""
        job = SyncJob(
            job_id=_new_id(),
            direction=config.direction,
            conflict_resolution=config.conflict_resolution,
            initiated_by=initiated_by,
            source_folder=config.pbix_folder,
            target_snowflake_schema=config.snowflake_schema,
            target_workspace_id=config.fabric_workspace_id,
        )
        self._repo.create_job(job)

        # Discover items from source
        items = self._discover_items(config, job.job_id)
        for item in items:
            self._repo.create_item(item)
        job.items = items
        job.total_items = len(items)
        self._repo.update_job(job)

        logger.info(f"Created sync job {job.job_id} with {len(items)} items")
        return job

    def _resume_job(self, job_id: str) -> SyncJob:
        """Load an existing job for resumption."""
        job = self._repo.get_job(job_id)
        if job is None:
            raise SyncError(f"Job {job_id} not found", job_id=job_id)

        if job.status not in (SyncJobStatus.FAILED, SyncJobStatus.CONFLICT):
            raise SyncError(
                f"Job {job_id} cannot be resumed (status={job.status.value})",
                job_id=job_id,
            )

        job.items = self._repo.get_items_for_job(job_id)
        return job

    def _discover_items(
        self, config: SyncConfig, job_id: str
    ) -> List[SyncJobItem]:
        """Discover source items based on direction and config."""
        items: List[SyncJobItem] = []

        if config.direction in (
            SyncDirection.PBIX_TO_SNOWFLAKE,
            SyncDirection.BIDIRECTIONAL,
        ):
            items.extend(self._discover_pbix_items(config, job_id))

        if config.direction in (
            SyncDirection.SNOWFLAKE_TO_PBI,
            SyncDirection.BIDIRECTIONAL,
        ):
            items.extend(self._discover_snowflake_items(config, job_id))

        # ── Fabric ↔ Snowflake semantic model directions ──────────────
        if config.direction in (
            SyncDirection.FABRIC_TO_SNOWFLAKE,
            SyncDirection.FABRIC_SNOWFLAKE_BIDIRECTIONAL,
        ):
            items.extend(self._discover_fabric_items(config, job_id))

        if config.direction in (
            SyncDirection.SNOWFLAKE_TO_FABRIC,
            SyncDirection.FABRIC_SNOWFLAKE_BIDIRECTIONAL,
        ):
            items.extend(self._discover_snowflake_semantic_items(config, job_id))

        return items

    def _discover_pbix_items(
        self, config: SyncConfig, job_id: str
    ) -> List[SyncJobItem]:
        """Find .pbix files from either a single source path or a configured folder."""
        pbix_files: List[Path] = []

        if config.source_path:
            pbix_file = Path(config.source_path)
            if not pbix_file.exists():
                raise SyncError(
                    f"PBIX file not found: {config.source_path}",
                    job_id=job_id,
                )
            pbix_files = [pbix_file]
        elif config.pbix_folder:
            folder = Path(config.pbix_folder)
            if not folder.exists():
                raise SyncError(
                    f"PBIX folder not found: {config.pbix_folder}",
                    job_id=job_id,
                )
            pbix_files = sorted(folder.glob(config.pbix_pattern))
        else:
            logger.warning("No PBIX source_path or pbix_folder configured — skipping PBIX discovery")
            return []

        if not pbix_files:
            logger.warning("No PBIX files matched the configured discovery inputs")
            return []

        items: List[SyncJobItem] = []
        for pbix_path in pbix_files:
            # Check incremental: skip if OSI hash unchanged
            if config.incremental:
                mapping = self._repo.get_mapping(
                    source_type="pbix",
                    source_identifier=str(pbix_path),
                    target_type="snowflake",
                )
                if mapping and mapping.last_osi_hash:
                    # We'll check hash after extraction; for now just queue
                    pass

            import re
            raw_stem = pbix_path.stem
            # Strip 32-character UUID prefix if it exists (e.g. d501c10cadeb4687b0398c755d2b1add_continent -> continent)
            clean_stem = re.sub(r'^[0-9a-f]{32}_', '', raw_stem)
            
            items.append(
                SyncJobItem(
                    item_id=_new_id(),
                    job_id=job_id,
                    model_name=clean_stem,
                    source_path=str(pbix_path),
                )
            )

        logger.info("Discovered %s PBIX source item(s)", len(items))
        return items

    def _discover_snowflake_items(
        self, config: SyncConfig, job_id: str
    ) -> List[SyncJobItem]:
        """
        Discover models from Snowflake (for reverse sync to Power BI).

        Uses the SnowflakeExtractor to list available semantic models
        or schemas that can be published to Power BI.
        """
        # For now, create a single item representing the Snowflake schema
        if not config.snowflake_schema:
            logger.warning("No snowflake_schema configured — skipping Snowflake discovery")
            return []

        return [
            SyncJobItem(
                item_id=_new_id(),
                job_id=job_id,
                model_name=f"snowflake_{config.snowflake_schema}",
                source_path=config.snowflake_schema,
            )
        ]

    def _discover_fabric_items(
        self, config: SyncConfig, job_id: str
    ) -> List[SyncJobItem]:
        """
        Discover Fabric semantic models for Fabric → Snowflake sync.

        Lists all semantic models in the configured workspace via
        ``FabricExtractor.list_semantic_models()``.  Optionally filters
        by ``config.fabric_model_names`` if provided.
        """
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.core.settings import get_settings

        settings = get_settings()
        extractor = FabricExtractor(settings.fabric)
        models = extractor.list_semantic_models()

        # Optional name filter
        filter_names: Optional[list[str]] = config.fabric_model_names
        if filter_names:
            lower_filter = {n.lower() for n in filter_names}
            models = [
                m for m in models
                if m.get("displayName", "").lower() in lower_filter
            ]

        items: List[SyncJobItem] = []
        for model in models:
            items.append(
                SyncJobItem(
                    item_id=_new_id(),
                    job_id=job_id,
                    model_name=model.get("displayName", model.get("id", "unknown")),
                    source_path=model.get("id", ""),
                )
            )

        logger.info(
            f"Discovered {len(items)} Fabric semantic model(s) "
            f"in workspace {settings.fabric.workspace_id}"
        )
        return items

    def _discover_snowflake_semantic_items(
        self, config: SyncConfig, job_id: str
    ) -> List[SyncJobItem]:
        """
        Discover Snowflake semantic views for Snowflake → Fabric sync.

        Uses ``SnowflakeExtractor.discover_semantic_views()``.
        Optionally filters by ``config.snowflake_semantic_views``.
        """
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.core.settings import get_settings

        settings = get_settings()
        extractor = SnowflakeExtractor(config=settings.snowflake)
        views = extractor.discover_semantic_views()

        # Optional name filter
        filter_views: Optional[list[str]] = config.snowflake_semantic_views
        if filter_views:
            lower_filter = {v.lower() for v in filter_views}
            views = [v for v in views if v["name"].lower() in lower_filter]

        items: List[SyncJobItem] = []
        for view in views:
            items.append(
                SyncJobItem(
                    item_id=_new_id(),
                    job_id=job_id,
                    model_name=view["name"],
                    source_path=view["name"],
                )
            )

        logger.info(
            f"Discovered {len(items)} Snowflake semantic view(s) "
            f"in {settings.snowflake.database}.{settings.snowflake.schema_name}"
        )
        return items

    # -----------------------------------------------------------------
    # Item Processing
    # -----------------------------------------------------------------

    def _process_items_sequential(
        self,
        job: SyncJob,
        config: SyncConfig,
        start_index: int = 0,
    ) -> None:
        """Process items one at a time."""
        for i, item in enumerate(job.items[start_index:], start=start_index):
            self._process_single_item(item, config, job)

            # Save checkpoint after each successful item
            if item.status == SyncItemStatus.COMPLETED:
                self._repo.save_checkpoint(
                    SyncCheckpoint(
                        job_id=job.job_id,
                        last_processed_item_id=item.item_id,
                        last_processed_index=i,
                    )
                )

    def _process_items_parallel(
        self,
        job: SyncJob,
        config: SyncConfig,
        start_index: int = 0,
    ) -> None:
        """Process items concurrently using TracedThreadPoolExecutor."""
        from semabridge.utils.concurrency import TracedThreadPoolExecutor

        pending_items = job.items[start_index:]
        if not pending_items:
            return

        logger.info(
            f"Processing {len(pending_items)} items in parallel "
            f"(max_workers={config.max_workers})"
        )

        with TracedThreadPoolExecutor(max_workers=config.max_workers) as executor:
            futures = {
                executor.submit(self._process_single_item, item, config, job): item
                for item in pending_items
            }

            for future in futures:
                item = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(
                        f"Item '{item.model_name}' failed in parallel: {e}"
                    )

    def _process_single_item(
        self,
        item: SyncJobItem,
        config: SyncConfig,
        job: SyncJob,
    ) -> None:
        """
        Process a single sync item through the full pipeline:
        Extract → Convert to OSI → Check Conflicts → Schema Evolution → Deploy.
        """
        item_start = time.time()
        item.status = SyncItemStatus.EXTRACTING
        item.started_at = _utc_now()
        self._repo.update_item(item)

        try:
            # Step 1: Extract from source
            osi_model = self._extract_to_osi(item, config, job)
            item.osi_snapshot = osi_model.model_dump(mode="json")

            # Step 2: Check incremental skip
            if config.incremental:
                current_hash = self._schema_tracker.compute_schema_hash(osi_model)
                mapping = self._repo.get_mapping(
                    source_type=self._source_type(config),
                    source_identifier=item.source_path or item.model_name,
                    target_type=self._target_type(config),
                )
                if mapping and mapping.last_osi_hash == current_hash:
                    logger.info(
                        f"Skipping '{item.model_name}' — schema unchanged "
                        f"(hash={current_hash[:12]}…)"
                    )
                    item.status = SyncItemStatus.SKIPPED
                    item.completed_at = _utc_now()
                    item.duration_ms = int((time.time() - item_start) * 1000)
                    self._repo.update_item(item)
                    return

            # Step 3: Detect conflicts
            item.status = SyncItemStatus.CONVERTING
            self._repo.update_item(item)

            target_schema = self._schema_tracker.get_target_schema(
                osi_model.unique_name
            )
            conflicts = self._conflict_resolver.detect_conflicts(
                source_model=osi_model,
                target_schema=target_schema,
                job_id=job.job_id,
                item_id=item.item_id,
            )

            if conflicts:
                can_proceed, _ = self._conflict_resolver.apply_strategy(
                    conflicts, config.conflict_resolution, job.job_id
                )
                if not can_proceed:
                    raise ConflictError(
                        f"Unresolved conflicts for '{item.model_name}'",
                        conflict_ids=[c.conflict_id for c in conflicts if not c.is_resolved],
                        job_id=job.job_id,
                    )

            # Step 4: Record schema version
            self._schema_tracker.record_version(osi_model, job_id=job.job_id)

            # Step 5: Deploy to target
            item.status = SyncItemStatus.DEPLOYING
            self._repo.update_item(item)

            target_id = self._deploy_to_target(osi_model, config, job)
            item.target_artifact_id = target_id

            # Step 6: Update mapping
            schema_hash = self._schema_tracker.compute_schema_hash(osi_model)
            self._repo.upsert_mapping(
                ModelMapping(
                    source_type=self._source_type(config),
                    source_identifier=item.source_path or item.model_name,
                    target_type=self._target_type(config),
                    target_identifier=target_id or "",
                    model_name=osi_model.unique_name,
                    last_synced_at=_utc_now(),
                    last_osi_hash=schema_hash,
                )
            )

            # Done
            item.status = SyncItemStatus.COMPLETED
            item.completed_at = _utc_now()
            item.duration_ms = int((time.time() - item_start) * 1000)
            self._repo.update_item(item)

            logger.info(
                f"Item '{item.model_name}' completed in {item.duration_ms}ms"
            )

        except ConflictError:
            # Re-raise to let orchestrator handle job-level pause
            item.status = SyncItemStatus.FAILED
            item.error_message = "Blocked by unresolved conflicts"
            item.completed_at = _utc_now()
            item.duration_ms = int((time.time() - item_start) * 1000)
            self._repo.update_item(item)
            raise

        except Exception as e:
            item.status = SyncItemStatus.FAILED
            item.error_message = str(e)
            item.completed_at = _utc_now()
            item.duration_ms = int((time.time() - item_start) * 1000)
            self._repo.update_item(item)
            logger.error(f"Item '{item.model_name}' failed: {e}")

    # -----------------------------------------------------------------
    # Extraction and Deployment (pluggable via factories)
    # -----------------------------------------------------------------

    def _extract_to_osi(
        self,
        item: SyncJobItem,
        config: SyncConfig,
        job: SyncJob,
    ) -> OSIModel:
        """
        Extract source data and convert to OSI model.

        For PBIX: Use LocalPBIXConnector → TMSLToOSIConverter.
        For Snowflake: Use SnowflakeExtractor → metadata-to-OSI.
        For Fabric (semantic model): Use FabricExtractor → TMSLToOSIConverter.
        For Snowflake semantic view: Use SnowflakeExtractor DDL → SemanticViewToOSIConverter.
        """
        if config.direction in (
            SyncDirection.PBIX_TO_SNOWFLAKE,
            SyncDirection.BIDIRECTIONAL,
        ) and item.source_path and item.source_path.endswith(".pbix"):
            return self._extract_pbix_to_osi(item)
        elif config.direction in (
            SyncDirection.SNOWFLAKE_TO_PBI,
            SyncDirection.BIDIRECTIONAL,
        ):
            return self._extract_snowflake_to_osi(item, config)
        elif config.direction in (
            SyncDirection.FABRIC_TO_SNOWFLAKE,
            SyncDirection.FABRIC_SNOWFLAKE_BIDIRECTIONAL,
        ):
            return self._extract_fabric_to_osi(item)
        elif config.direction in (
            SyncDirection.SNOWFLAKE_TO_FABRIC,
        ):
            return self._extract_snowflake_semantic_to_osi(item, config)
        else:
            raise SyncError(
                f"No extractor for direction={config.direction.value} "
                f"item={item.model_name}",
                job_id=job.job_id,
            )

    def _extract_pbix_to_osi(self, item: SyncJobItem) -> OSIModel:
        """Extract PBIX file and convert to OSI model."""
        from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter

        connector = LocalPBIXConnector({"pbix_path": item.source_path})
        raw_tmsl = connector.extract()
        converter = TMSLToOSIConverter()
        tmsl_data = {
            "tmsl": raw_tmsl,
            "workspace_id": "local",
            "dataset_id": item.model_name,
        }

        osi_model = converter.to_osi(tmsl_data)
        logger.info(
            f"Extracted '{item.model_name}' → OSI: "
            f"{len(osi_model.datasets)} datasets, "
            f"{len(osi_model.metrics)} metrics, "
            f"{len(osi_model.relationships)} relationships"
        )
        return osi_model

    def _extract_fabric_to_osi(self, item: SyncJobItem) -> OSIModel:
        """
        Extract a Fabric semantic model definition and convert to OSI.

        Uses ``FabricExtractor.get_model_definition()`` (TMSL JSON) and
        pipes it through the existing ``TMSLToOSIConverter``.
        """
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.core.settings import get_settings

        settings = get_settings()
        extractor = FabricExtractor(settings.fabric)
        # item.source_path holds the Fabric dataset GUID
        dataset_id: str = item.source_path or item.model_name
        tmsl_data = extractor.get_model_definition(dataset_id)

        converter = TMSLToOSIConverter()
        osi_model = converter.to_osi(
            {
                "tmsl": tmsl_data,
                "workspace_id": settings.fabric.workspace_id,
                "dataset_id": item.model_name,
            }
        )

        logger.info(
            f"Extracted Fabric model '{item.model_name}' → OSI: "
            f"{len(osi_model.datasets)} datasets, "
            f"{len(osi_model.metrics)} metrics, "
            f"{len(osi_model.relationships)} relationships"
        )
        return osi_model

    def _extract_snowflake_semantic_to_osi(
        self, item: SyncJobItem, config: SyncConfig
    ) -> OSIModel:
        """
        Extract a Snowflake semantic view DDL and convert to OSI.

        Retrieves the DDL via ``GET_DDL``, optionally enriches with
        ``INFORMATION_SCHEMA.COLUMNS`` metadata, then parses everything
        through ``SemanticViewToOSIConverter``.
        """
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
        from semabridge.core.settings import get_settings

        settings = get_settings()
        extractor = SnowflakeExtractor(config=settings.snowflake)
        view_name: str = item.source_path or item.model_name

        ddl = extractor.extract_semantic_view_ddl(view_name)

        # Enrich: get INFORMATION_SCHEMA columns for base tables
        raw_metadata = extractor.extract_all()
        col_meta: dict = {
            t: raw_metadata["columns"].get(t, [])
            for t in raw_metadata.get("tables", {})
        }

        converter = SemanticViewToOSIConverter()
        osi_model = converter.to_osi(
            {
                "ddl": ddl,
                "view_name": view_name,
                "column_metadata": col_meta,
            }
        )

        logger.info(
            f"Extracted Snowflake semantic view '{view_name}' → OSI: "
            f"{len(osi_model.datasets)} datasets, "
            f"{len(osi_model.metrics)} metrics"
        )
        return osi_model

    def _extract_snowflake_to_osi(
        self, item: SyncJobItem, config: SyncConfig
    ) -> OSIModel:
        """Extract Snowflake metadata and convert to OSI model."""
        from semabridge.core.settings import get_settings

        settings = get_settings()
        sf_config = settings.snowflake

        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor

        extractor = SnowflakeExtractor(config=sf_config)
        metadata = extractor.extract_all()

        # Convert Snowflake metadata to OSI
        from semabridge.intermediate.models import (
            OSIColumn,
            OSIDataset,
            OSIDataType,
            OSIModel,
            OSIRelationship,
        )

        datasets: list = []
        for table_info in metadata.get("tables", []):
            columns = []
            for col_info in table_info.get("columns", []):
                columns.append(
                    OSIColumn(
                        unique_name=col_info["name"],
                        data_type=self._map_snowflake_type(col_info.get("type", "VARCHAR")),
                        is_key=col_info.get("is_primary_key", False),
                    )
                )
            datasets.append(
                OSIDataset(
                    unique_name=table_info["name"],
                    source_table=table_info["name"],
                    source_schema=config.snowflake_schema,
                    columns=columns,
                )
            )

        relationships: list = []
        for rel_info in self._normalize_snowflake_relationships(
            metadata.get("relationships", [])
        ):
            relationships.append(
                OSIRelationship(
                    unique_name=rel_info["name"],
                    from_dataset=rel_info["from_table"],
                    from_columns=[rel_info["from_column"]],
                    to_dataset=rel_info["to_table"],
                    to_columns=[rel_info["to_column"]],
                )
            )

        osi_model = OSIModel(
            unique_name=item.model_name,
            datasets=datasets,
            relationships=relationships,
            source_platform="snowflake",
        )

        logger.info(
            f"Extracted Snowflake schema → OSI: "
            f"{len(datasets)} datasets, {len(relationships)} relationships"
        )
        return osi_model

    def _deploy_to_target(
        self,
        osi_model: OSIModel,
        config: SyncConfig,
        job: SyncJob,
    ) -> Optional[str]:
        """
        Deploy OSI model to the target platform.

        For PBIX_TO_SNOWFLAKE: Generate Snowflake DDL + Cortex YAML.
        For SNOWFLAKE_TO_PBI: Publish to Power BI Fabric workspace.
        """
        if config.direction == SyncDirection.PBIX_TO_SNOWFLAKE:
            return self._deploy_to_snowflake(osi_model, config)
        elif config.direction == SyncDirection.SNOWFLAKE_TO_PBI:
            return self._deploy_to_powerbi(osi_model, config)
        elif config.direction == SyncDirection.BIDIRECTIONAL:
            # Deploy to both — Snowflake first, then Power BI
            sf_id = self._deploy_to_snowflake(osi_model, config)
            pbi_id = self._deploy_to_powerbi(osi_model, config)
            return sf_id or pbi_id
        # ── Fabric ↔ Snowflake semantic model directions ─────────────────
        elif config.direction == SyncDirection.FABRIC_TO_SNOWFLAKE:
            return self._deploy_to_snowflake(osi_model, config)
        elif config.direction == SyncDirection.SNOWFLAKE_TO_FABRIC:
            return self._deploy_to_powerbi(osi_model, config)
        elif config.direction == SyncDirection.FABRIC_SNOWFLAKE_BIDIRECTIONAL:
            # Deploy to both
            sf_id = self._deploy_to_snowflake(osi_model, config)
            fabric_id = self._deploy_to_powerbi(osi_model, config)
            return sf_id or fabric_id
        return None

    def _deploy_to_snowflake(
        self, osi_model: OSIModel, config: SyncConfig
    ) -> Optional[str]:
        """Convert OSI to SML and deploy to Snowflake."""
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
        from semabridge.core.settings import get_settings

        settings = get_settings()

        # Convert OSI → SML
        converter = OSIToSMLConverter()
        sml_model = converter.from_osi(osi_model)

        # Deploy via SnowflakeEmitter
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        emitter = SnowflakeEmitter(config=settings.snowflake)
        emitter.deploy(sml_model)

        target_id = f"snowflake://{settings.snowflake.database}/{config.snowflake_schema or settings.snowflake.schema_name}"
        logger.info(f"Deployed '{osi_model.unique_name}' to Snowflake: {target_id}")
        return target_id

    def _deploy_to_powerbi(
        self, osi_model: OSIModel, config: SyncConfig
    ) -> Optional[str]:
        """Convert OSI to TMSL and publish to Power BI Fabric."""
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
        from semabridge.core.settings import get_settings

        settings = get_settings()

        # Convert OSI → SML → TMSL → Fabric
        converter = OSIToSMLConverter()
        sml_model = converter.from_osi(osi_model)

        from semabridge.connectors.fabric_publisher import FabricPublisher

        publisher = FabricPublisher(config=settings.fabric)
        result = publisher.publish(
            sml_model=sml_model,
            model_name=osi_model.unique_name,
            description=osi_model.description or "Synced by SemaBridge",
            overwrite=True,
        )

        target_id = config.fabric_workspace_id or settings.fabric.workspace_id
        logger.info(f"Published '{osi_model.unique_name}' to Fabric workspace {target_id}")
        return target_id

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _source_type(config: SyncConfig) -> str:
        """Determine source platform type from config."""
        if config.direction == SyncDirection.PBIX_TO_SNOWFLAKE:
            return "pbix"
        elif config.direction == SyncDirection.SNOWFLAKE_TO_PBI:
            return "snowflake"
        elif config.direction == SyncDirection.FABRIC_TO_SNOWFLAKE:
            return "fabric"
        elif config.direction == SyncDirection.SNOWFLAKE_TO_FABRIC:
            return "snowflake_semantic_view"
        elif config.direction == SyncDirection.FABRIC_SNOWFLAKE_BIDIRECTIONAL:
            return "fabric"
        return "pbix"  # bidirectional defaults to PBIX as source

    @staticmethod
    def _target_type(config: SyncConfig) -> str:
        """Determine target platform type from config."""
        if config.direction == SyncDirection.PBIX_TO_SNOWFLAKE:
            return "snowflake"
        elif config.direction == SyncDirection.SNOWFLAKE_TO_PBI:
            return "fabric"
        elif config.direction == SyncDirection.FABRIC_TO_SNOWFLAKE:
            return "snowflake_semantic_view"
        elif config.direction == SyncDirection.SNOWFLAKE_TO_FABRIC:
            return "fabric"
        elif config.direction == SyncDirection.FABRIC_SNOWFLAKE_BIDIRECTIONAL:
            return "snowflake_semantic_view"
        return "snowflake"  # bidirectional defaults to Snowflake as primary target

    @staticmethod
    def _normalize_snowflake_relationships(
        raw_relationships: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Normalize Snowflake relationship rows for deterministic API behavior.

        Rules:
        - Preserve all valid relationships regardless of existing name.
        - Deduplicate by relationship endpoint tuple.
        - Force canonical name ``REL_<FROM>_<FROM_COL>__<TO>_<TO_COL>``.
        """
        tracker = RelationshipNameTracker()
        seen_endpoints: set[tuple[str, str, str, str]] = set()
        normalized: list[dict[str, str]] = []

        removed_duplicates = 0
        renamed_count = 0

        for rel in raw_relationships or []:
            if not isinstance(rel, dict):
                continue

            from_table = str(rel.get("from_table") or rel.get("fromTable") or "").strip()
            from_column = str(rel.get("from_column") or rel.get("fromColumn") or "").strip()
            to_table = str(rel.get("to_table") or rel.get("toTable") or "").strip()
            to_column = str(rel.get("to_column") or rel.get("toColumn") or "").strip()

            if not (from_table and from_column and to_table and to_column):
                continue

            endpoint_key = (
                from_table.upper(),
                from_column.upper(),
                to_table.upper(),
                to_column.upper(),
            )
            if endpoint_key in seen_endpoints:
                removed_duplicates += 1
                continue
            seen_endpoints.add(endpoint_key)

            canonical_name = tracker.next_name(
                from_table,
                from_column,
                to_table,
                to_column,
            )
            original_name = str(rel.get("name") or rel.get("relationship_name") or "").strip()
            if original_name != canonical_name:
                renamed_count += 1

            normalized.append(
                {
                    "name": canonical_name,
                    "from_table": from_table,
                    "from_column": from_column,
                    "to_table": to_table,
                    "to_column": to_column,
                }
            )

        if renamed_count or removed_duplicates:
            logger.info(
                "Normalized Snowflake relationships: kept=%s renamed=%s removed_duplicates=%s",
                len(normalized),
                renamed_count,
                removed_duplicates,
            )

        return normalized

    @staticmethod
    def _map_snowflake_type(sf_type: str) -> "OSIDataType":
        """Map a Snowflake data type string to OSI data type."""
        from semabridge.intermediate.models import OSIDataType

        sf_upper = sf_type.upper().split("(")[0].strip()
        mapping = {
            "VARCHAR": OSIDataType.STRING,
            "STRING": OSIDataType.STRING,
            "TEXT": OSIDataType.STRING,
            "CHAR": OSIDataType.STRING,
            "NUMBER": OSIDataType.DECIMAL,
            "DECIMAL": OSIDataType.DECIMAL,
            "NUMERIC": OSIDataType.DECIMAL,
            "INT": OSIDataType.INTEGER,
            "INTEGER": OSIDataType.INTEGER,
            "BIGINT": OSIDataType.INTEGER,
            "SMALLINT": OSIDataType.INTEGER,
            "TINYINT": OSIDataType.INTEGER,
            "FLOAT": OSIDataType.FLOAT,
            "FLOAT4": OSIDataType.FLOAT,
            "FLOAT8": OSIDataType.FLOAT,
            "DOUBLE": OSIDataType.FLOAT,
            "REAL": OSIDataType.FLOAT,
            "BOOLEAN": OSIDataType.BOOLEAN,
            "DATE": OSIDataType.DATE,
            "DATETIME": OSIDataType.DATETIME,
            "TIMESTAMP": OSIDataType.DATETIME,
            "TIMESTAMP_LTZ": OSIDataType.DATETIME,
            "TIMESTAMP_NTZ": OSIDataType.DATETIME,
            "TIMESTAMP_TZ": OSIDataType.DATETIME,
            "TIME": OSIDataType.TIME,
            "BINARY": OSIDataType.BINARY,
            "VARBINARY": OSIDataType.BINARY,
            "VARIANT": OSIDataType.VARIANT,
            "OBJECT": OSIDataType.VARIANT,
            "ARRAY": OSIDataType.VARIANT,
        }
        return mapping.get(sf_upper, OSIDataType.UNKNOWN)
