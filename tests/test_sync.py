"""
Tests for the bidirectional sync engine.

Covers:
- Sync domain models (serialization, validation)
- SyncRepository CRUD (DuckDB persistence)
- ConflictResolver (detection, strategy application)
- SchemaEvolutionTracker (hashing, versioning, diffing)
- SyncOrchestrator (end-to-end job lifecycle)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from semabridge.intermediate.models import (
    OSIAggregationType,
    OSICardinality,
    OSIColumn,
    OSIDataType,
    OSIDataset,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)
from semabridge.sync.models import (
    ConflictResolution,
    ConflictSeverity,
    ModelMapping,
    SchemaChangeType,
    SchemaVersion,
    SyncCheckpoint,
    SyncConfig,
    SyncConflict,
    SyncDirection,
    SyncItemStatus,
    SyncJob,
    SyncJobItem,
    SyncJobStatus,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return path to a temporary DuckDB database file."""
    return str(tmp_path / "test_sync.db")


@pytest.fixture
def sync_repo(tmp_db: str):
    """Create a SyncRepository backed by a temp DB."""
    from semabridge.sync.repository import SyncRepository

    return SyncRepository(db_path=tmp_db)


@pytest.fixture
def sample_osi_model() -> OSIModel:
    """Create a sample OSI model for testing."""
    return OSIModel(
        unique_name="test_model",
        label="Test Model",
        description="A test semantic model",
        datasets=[
            OSIDataset(
                unique_name="customers",
                source_table="CUSTOMERS",
                source_schema="PUBLIC",
                columns=[
                    OSIColumn(
                        unique_name="customer_id",
                        data_type=OSIDataType.INTEGER,
                        is_key=True,
                    ),
                    OSIColumn(
                        unique_name="name",
                        data_type=OSIDataType.STRING,
                    ),
                    OSIColumn(
                        unique_name="revenue",
                        data_type=OSIDataType.DECIMAL,
                    ),
                ],
            ),
            OSIDataset(
                unique_name="orders",
                source_table="ORDERS",
                source_schema="PUBLIC",
                columns=[
                    OSIColumn(
                        unique_name="order_id",
                        data_type=OSIDataType.INTEGER,
                        is_key=True,
                    ),
                    OSIColumn(
                        unique_name="customer_id",
                        data_type=OSIDataType.INTEGER,
                    ),
                    OSIColumn(
                        unique_name="amount",
                        data_type=OSIDataType.DECIMAL,
                    ),
                ],
                is_fact=True,
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="total_revenue",
                dataset="orders",
                source_column="amount",
                aggregation=OSIAggregationType.SUM,
                expression="SUM(amount)",
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="orders_to_customers",
                from_dataset="orders",
                from_columns=["customer_id"],
                to_dataset="customers",
                to_columns=["customer_id"],
                cardinality=OSICardinality.MANY_TO_ONE,
            ),
        ],
        source_platform="pbix",
    )


@pytest.fixture
def sample_sync_config(tmp_path: Path) -> SyncConfig:
    """Create a sample sync config pointing at a temp PBIX folder."""
    return SyncConfig(
        direction=SyncDirection.PBIX_TO_SNOWFLAKE,
        conflict_resolution=ConflictResolution.FAIL_AND_APPROVE,
        pbix_folder=str(tmp_path),
        snowflake_schema="SEMANTIC_MODELS",
        max_workers=1,
        enable_parallel=False,
        incremental=False,
    )


# =============================================================================
# Sync Models Tests
# =============================================================================


class TestSyncModels:
    """Test sync domain model serialization and validation."""

    def test_sync_job_creation(self):
        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        assert job.status == SyncJobStatus.PENDING
        assert job.direction == SyncDirection.PBIX_TO_SNOWFLAKE
        assert job.job_id  # UUID generated
        assert job.created_at  # timestamp generated

    def test_sync_job_item_creation(self):
        item = SyncJobItem(
            job_id="test-job-123",
            model_name="sales_model",
            source_path="/path/to/sales.pbix",
        )
        assert item.status == SyncItemStatus.QUEUED
        assert item.model_name == "sales_model"

    def test_sync_job_update_counts(self):
        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        job.items = [
            SyncJobItem(job_id=job.job_id, model_name="a", status=SyncItemStatus.COMPLETED),
            SyncJobItem(job_id=job.job_id, model_name="b", status=SyncItemStatus.COMPLETED),
            SyncJobItem(job_id=job.job_id, model_name="c", status=SyncItemStatus.FAILED),
        ]
        job.update_counts()
        assert job.total_items == 3
        assert job.completed_items == 2
        assert job.failed_items == 1

    def test_sync_config_validation(self):
        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            max_workers=5,
            source_path="/path/to/model.pbix",
        )
        assert config.enable_parallel is True
        assert config.incremental is True

    def test_sync_config_accepts_single_pbix_source_path_aliases(self):
        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            file_path="/path/to/model.pbix",
            target_snowflake_schema="SEMANTIC_MODELS",
        )
        assert config.source_path == "/path/to/model.pbix"
        assert config.snowflake_schema == "SEMANTIC_MODELS"

    def test_sync_conflict_is_resolved(self):
        conflict = SyncConflict(
            job_id="job-1",
            model_name="test",
            change_type=SchemaChangeType.COLUMN_ADDED,
            severity=ConflictSeverity.INFO,
            description="test conflict",
        )
        assert not conflict.is_resolved
        conflict.resolution = ConflictResolution.SOURCE_WINS
        assert conflict.is_resolved

    def test_model_mapping_creation(self):
        mapping = ModelMapping(
            source_type="pbix",
            source_identifier="/path/to/file.pbix",
            target_type="snowflake",
            target_identifier="snowflake://db/schema",
            model_name="test_model",
        )
        assert mapping.is_active is True
        assert mapping.mapping_id  # UUID generated


# =============================================================================
# Repository Tests
# =============================================================================


class TestSyncRepository:
    """Test DuckDB persistence for sync data."""

    def test_create_and_get_job(self, sync_repo):
        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        retrieved = sync_repo.get_job(job.job_id)
        assert retrieved is not None
        assert retrieved.job_id == job.job_id
        assert retrieved.direction == SyncDirection.PBIX_TO_SNOWFLAKE

    def test_update_job(self, sync_repo):
        job = SyncJob(direction=SyncDirection.SNOWFLAKE_TO_PBI)
        sync_repo.create_job(job)

        job.status = SyncJobStatus.RUNNING
        job.total_items = 5
        sync_repo.update_job(job)

        retrieved = sync_repo.get_job(job.job_id)
        assert retrieved.status == SyncJobStatus.RUNNING
        assert retrieved.total_items == 5

    def test_list_jobs(self, sync_repo):
        for _ in range(3):
            sync_repo.create_job(
                SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
            )
        jobs = sync_repo.list_jobs()
        assert len(jobs) == 3

    def test_list_jobs_filter_status(self, sync_repo):
        job1 = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        job2 = SyncJob(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            status=SyncJobStatus.COMPLETED,
        )
        sync_repo.create_job(job1)
        sync_repo.create_job(job2)

        pending = sync_repo.list_jobs(status=SyncJobStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].job_id == job1.job_id

    def test_create_and_get_items(self, sync_repo):
        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        item = SyncJobItem(
            job_id=job.job_id,
            model_name="sales_model",
            source_path="/path/to/sales.pbix",
        )
        sync_repo.create_item(item)

        items = sync_repo.get_items_for_job(job.job_id)
        assert len(items) == 1
        assert items[0].model_name == "sales_model"

    def test_update_item(self, sync_repo):
        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        item = SyncJobItem(job_id=job.job_id, model_name="test")
        sync_repo.create_item(item)

        item.status = SyncItemStatus.COMPLETED
        item.duration_ms = 1500
        sync_repo.update_item(item)

        items = sync_repo.get_items_for_job(job.job_id)
        assert items[0].status == SyncItemStatus.COMPLETED
        assert items[0].duration_ms == 1500

    def test_upsert_mapping(self, sync_repo):
        mapping = ModelMapping(
            source_type="pbix",
            source_identifier="/path/to/file.pbix",
            target_type="snowflake",
            target_identifier="snowflake://db/schema",
            model_name="test_model",
        )
        result = sync_repo.upsert_mapping(mapping)
        assert result.mapping_id

        # Upsert (update)
        mapping.last_osi_hash = "abc123"
        sync_repo.upsert_mapping(mapping)

        retrieved = sync_repo.get_mapping("pbix", "/path/to/file.pbix", "snowflake")
        assert retrieved is not None
        assert retrieved.last_osi_hash == "abc123"

    def test_schema_version_crud(self, sync_repo):
        version = SchemaVersion(
            model_name="test_model",
            version_number=1,
            schema_hash="abc123",
            schema_snapshot={"datasets": [], "metrics": []},
        )
        sync_repo.create_schema_version(version)

        latest = sync_repo.get_latest_schema_version("test_model")
        assert latest is not None
        assert latest.version_number == 1
        assert latest.schema_hash == "abc123"

    def test_schema_version_history(self, sync_repo):
        for i in range(3):
            sync_repo.create_schema_version(
                SchemaVersion(
                    model_name="test_model",
                    version_number=i + 1,
                    schema_hash=f"hash_{i}",
                    schema_snapshot={"v": i},
                )
            )

        history = sync_repo.get_schema_history("test_model")
        assert len(history) == 3
        assert history[0].version_number == 3  # newest first

    def test_conflict_crud(self, sync_repo):
        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        conflict = SyncConflict(
            job_id=job.job_id,
            model_name="test",
            change_type=SchemaChangeType.COLUMN_ADDED,
            severity=ConflictSeverity.INFO,
            description="New column added",
        )
        sync_repo.create_conflict(conflict)

        unresolved = sync_repo.get_unresolved_conflicts(job.job_id)
        assert len(unresolved) == 1

        sync_repo.resolve_conflict(
            conflict.conflict_id, ConflictResolution.SOURCE_WINS
        )

        unresolved = sync_repo.get_unresolved_conflicts(job.job_id)
        assert len(unresolved) == 0

    def test_checkpoint_save_and_get(self, sync_repo):
        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        checkpoint = SyncCheckpoint(
            job_id=job.job_id,
            last_processed_item_id="item-1",
            last_processed_index=2,
            state_snapshot={"progress": 50},
        )
        sync_repo.save_checkpoint(checkpoint)

        retrieved = sync_repo.get_checkpoint(job.job_id)
        assert retrieved is not None
        assert retrieved.last_processed_index == 2
        assert retrieved.state_snapshot == {"progress": 50}


# =============================================================================
# Conflict Resolver Tests
# =============================================================================


class TestConflictResolver:
    """Test conflict detection and resolution."""

    def test_no_conflicts_on_first_sync(self, sync_repo, sample_osi_model):
        from semabridge.sync.conflict_resolver import ConflictResolver

        resolver = ConflictResolver(sync_repo)
        conflicts = resolver.detect_conflicts(
            source_model=sample_osi_model,
            target_schema=None,
            job_id="job-1",
        )
        assert len(conflicts) == 0

    def test_detect_new_table(self, sync_repo, sample_osi_model):
        from semabridge.sync.conflict_resolver import ConflictResolver

        resolver = ConflictResolver(sync_repo)

        # Target has only 'customers', source has 'customers' + 'orders'
        target_schema = {
            "datasets": [
                {
                    "unique_name": "customers",
                    "columns": [
                        {"unique_name": "customer_id", "data_type": "integer", "is_key": True},
                        {"unique_name": "name", "data_type": "string", "is_key": False},
                        {"unique_name": "revenue", "data_type": "decimal", "is_key": False},
                    ],
                }
            ],
            "metrics": [],
            "relationships": [],
        }

        conflicts = resolver.detect_conflicts(
            source_model=sample_osi_model,
            target_schema=target_schema,
            job_id="job-1",
        )

        table_added = [
            c for c in conflicts if c.change_type == SchemaChangeType.TABLE_ADDED
        ]
        assert len(table_added) == 1
        assert "orders" in table_added[0].description

    def test_detect_removed_table(self, sync_repo, sample_osi_model):
        from semabridge.sync.conflict_resolver import ConflictResolver

        resolver = ConflictResolver(sync_repo)

        target_schema = {
            "datasets": [
                {"unique_name": "customers", "columns": [
                    {"unique_name": "customer_id", "data_type": "integer", "is_key": True},
                    {"unique_name": "name", "data_type": "string", "is_key": False},
                    {"unique_name": "revenue", "data_type": "decimal", "is_key": False},
                ]},
                {"unique_name": "orders", "columns": [
                    {"unique_name": "order_id", "data_type": "integer", "is_key": True},
                    {"unique_name": "customer_id", "data_type": "integer", "is_key": False},
                    {"unique_name": "amount", "data_type": "decimal", "is_key": False},
                ]},
                {"unique_name": "legacy_table", "columns": []},
            ],
            "metrics": [
                {"unique_name": "total_revenue", "expression": "SUM(amount)"},
            ],
            "relationships": [
                {"unique_name": "orders_to_customers"},
            ],
        }

        conflicts = resolver.detect_conflicts(
            source_model=sample_osi_model,
            target_schema=target_schema,
            job_id="job-1",
        )

        removed = [
            c for c in conflicts if c.change_type == SchemaChangeType.TABLE_REMOVED
        ]
        assert len(removed) == 1
        assert removed[0].severity == ConflictSeverity.CRITICAL

    def test_detect_column_type_change(self, sync_repo, sample_osi_model):
        from semabridge.sync.conflict_resolver import ConflictResolver

        resolver = ConflictResolver(sync_repo)

        # Target has 'revenue' as string instead of decimal
        target_schema = {
            "datasets": [
                {
                    "unique_name": "customers",
                    "columns": [
                        {"unique_name": "customer_id", "data_type": "integer", "is_key": True},
                        {"unique_name": "name", "data_type": "string", "is_key": False},
                        {"unique_name": "revenue", "data_type": "string", "is_key": False},
                    ],
                },
                {
                    "unique_name": "orders",
                    "columns": [
                        {"unique_name": "order_id", "data_type": "integer", "is_key": True},
                        {"unique_name": "customer_id", "data_type": "integer", "is_key": False},
                        {"unique_name": "amount", "data_type": "decimal", "is_key": False},
                    ],
                },
            ],
            "metrics": [{"unique_name": "total_revenue", "expression": "SUM(amount)"}],
            "relationships": [{"unique_name": "orders_to_customers"}],
        }

        conflicts = resolver.detect_conflicts(
            source_model=sample_osi_model,
            target_schema=target_schema,
            job_id="job-1",
        )

        type_changes = [
            c for c in conflicts
            if c.change_type == SchemaChangeType.COLUMN_TYPE_CHANGED
        ]
        assert len(type_changes) == 1
        assert type_changes[0].severity == ConflictSeverity.CRITICAL

    def test_apply_source_wins_strategy(self, sync_repo):
        from semabridge.sync.conflict_resolver import ConflictResolver

        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        resolver = ConflictResolver(sync_repo)

        conflicts = [
            SyncConflict(
                job_id=job.job_id,
                model_name="test",
                change_type=SchemaChangeType.COLUMN_TYPE_CHANGED,
                severity=ConflictSeverity.CRITICAL,
                description="Type changed",
            ),
        ]

        can_proceed, persisted = resolver.apply_strategy(
            conflicts, ConflictResolution.SOURCE_WINS, job.job_id
        )
        assert can_proceed is True
        assert persisted[0].resolution == ConflictResolution.SOURCE_WINS

    def test_fail_and_approve_blocks_critical(self, sync_repo):
        from semabridge.sync.conflict_resolver import ConflictResolver

        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        resolver = ConflictResolver(sync_repo)

        conflicts = [
            SyncConflict(
                job_id=job.job_id,
                model_name="test",
                change_type=SchemaChangeType.TABLE_REMOVED,
                severity=ConflictSeverity.CRITICAL,
                description="Table removed",
            ),
        ]

        can_proceed, _ = resolver.apply_strategy(
            conflicts, ConflictResolution.FAIL_AND_APPROVE, job.job_id
        )
        assert can_proceed is False

    def test_resolve_all(self, sync_repo):
        from semabridge.sync.conflict_resolver import ConflictResolver

        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        resolver = ConflictResolver(sync_repo)

        for i in range(3):
            sync_repo.create_conflict(
                SyncConflict(
                    job_id=job.job_id,
                    model_name="test",
                    change_type=SchemaChangeType.COLUMN_ADDED,
                    severity=ConflictSeverity.INFO,
                    description=f"Conflict {i}",
                )
            )

        count = resolver.resolve_all(
            job.job_id, ConflictResolution.SOURCE_WINS
        )
        assert count == 3

        unresolved = sync_repo.get_unresolved_conflicts(job.job_id)
        assert len(unresolved) == 0


# =============================================================================
# Schema Evolution Tracker Tests
# =============================================================================


class TestSchemaEvolutionTracker:
    """Test schema versioning and change detection."""

    def test_compute_schema_hash(self, sync_repo, sample_osi_model):
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        tracker = SchemaEvolutionTracker(sync_repo)
        h1 = tracker.compute_schema_hash(sample_osi_model)
        h2 = tracker.compute_schema_hash(sample_osi_model)
        assert h1 == h2  # deterministic
        assert len(h1) == 64  # SHA-256

    def test_has_changed_new_model(self, sync_repo, sample_osi_model):
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        tracker = SchemaEvolutionTracker(sync_repo)
        assert tracker.has_changed(sample_osi_model) is True

    def test_has_changed_after_record(self, sync_repo, sample_osi_model):
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        tracker = SchemaEvolutionTracker(sync_repo)
        tracker.record_version(sample_osi_model)

        # Same model — no change
        assert tracker.has_changed(sample_osi_model) is False

    def test_has_changed_after_modification(self, sync_repo, sample_osi_model):
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        tracker = SchemaEvolutionTracker(sync_repo)
        tracker.record_version(sample_osi_model)

        # Modify the model — add a column
        sample_osi_model.datasets[0].columns.append(
            OSIColumn(unique_name="email", data_type=OSIDataType.STRING)
        )
        assert tracker.has_changed(sample_osi_model) is True

    def test_record_version_increments(self, sync_repo, sample_osi_model):
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        tracker = SchemaEvolutionTracker(sync_repo)

        v1 = tracker.record_version(sample_osi_model, job_id="job-1")
        assert v1.version_number == 1

        # Modify and record again
        sample_osi_model.datasets[0].columns.append(
            OSIColumn(unique_name="phone", data_type=OSIDataType.STRING)
        )
        v2 = tracker.record_version(sample_osi_model, job_id="job-2")
        assert v2.version_number == 2
        assert len(v2.changes_from_previous) >= 1

    def test_get_target_schema(self, sync_repo, sample_osi_model):
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        tracker = SchemaEvolutionTracker(sync_repo)

        # No versions yet
        assert tracker.get_target_schema("test_model") is None

        tracker.record_version(sample_osi_model)
        schema = tracker.get_target_schema("test_model")
        assert schema is not None
        assert "datasets" in schema
        assert len(schema["datasets"]) == 2

    def test_diff_snapshots_detects_changes(self, sync_repo):
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        old_snapshot = {
            "unique_name": "test",
            "datasets": [
                {"unique_name": "t1", "columns": [
                    {"unique_name": "c1", "data_type": "string", "is_key": False},
                ]},
            ],
            "metrics": [],
            "relationships": [],
        }

        new_snapshot = {
            "unique_name": "test",
            "datasets": [
                {"unique_name": "t1", "columns": [
                    {"unique_name": "c1", "data_type": "integer", "is_key": False},
                    {"unique_name": "c2", "data_type": "string", "is_key": False},
                ]},
                {"unique_name": "t2", "columns": []},
            ],
            "metrics": [
                {"unique_name": "m1", "expression": "SUM(x)"},
            ],
            "relationships": [],
        }

        changes = SchemaEvolutionTracker._diff_snapshots(old_snapshot, new_snapshot)
        change_types = [c["change_type"] for c in changes]

        assert SchemaChangeType.TABLE_ADDED.value in change_types
        assert SchemaChangeType.COLUMN_ADDED.value in change_types
        assert SchemaChangeType.COLUMN_TYPE_CHANGED.value in change_types
        assert SchemaChangeType.MEASURE_ADDED.value in change_types


# =============================================================================
# Sync Orchestrator Tests
# =============================================================================


class TestSyncOrchestrator:
    """Test the end-to-end sync orchestrator."""

    def test_create_job_with_pbix_files(self, sync_repo, tmp_path):
        """Test that the orchestrator discovers PBIX files and creates items."""
        from semabridge.sync.orchestrator import SyncOrchestrator

        # Create dummy PBIX files
        (tmp_path / "model_a.pbix").write_bytes(b"PK\x03\x04dummy")
        (tmp_path / "model_b.pbix").write_bytes(b"PK\x03\x04dummy")
        (tmp_path / "notes.txt").write_text("not a pbix")

        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            pbix_folder=str(tmp_path),
            enable_parallel=False,
            incremental=False,
        )

        orchestrator = SyncOrchestrator(repository=sync_repo)
        job = orchestrator._create_job(config, "test")

        assert job.total_items == 2
        assert {i.model_name for i in job.items} == {"model_a", "model_b"}

    def test_create_job_with_single_pbix_source_path(self, sync_repo, tmp_path):
        """Test that the orchestrator accepts a single PBIX file via source_path."""
        from semabridge.sync.orchestrator import SyncOrchestrator

        pbix_file = tmp_path / "sales.pbix"
        pbix_file.write_bytes(b"PK\x03\x04dummy")

        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            source_path=str(pbix_file),
            enable_parallel=False,
            incremental=False,
        )

        orchestrator = SyncOrchestrator(repository=sync_repo)
        job = orchestrator._create_job(config, "test")

        assert job.total_items == 1
        assert job.items[0].model_name == "sales"
        assert job.items[0].source_path == str(pbix_file)

    def test_empty_folder_creates_no_items(self, sync_repo, tmp_path):
        from semabridge.sync.orchestrator import SyncOrchestrator

        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            pbix_folder=str(tmp_path),
            enable_parallel=False,
        )

        orchestrator = SyncOrchestrator(repository=sync_repo)
        job = orchestrator._create_job(config, "test")
        assert job.total_items == 0

    def test_cancel_job(self, sync_repo):
        from semabridge.sync.orchestrator import SyncOrchestrator

        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        orchestrator = SyncOrchestrator(repository=sync_repo)
        cancelled = orchestrator.cancel(job.job_id)

        assert cancelled.status == SyncJobStatus.CANCELLED

    def test_get_status(self, sync_repo):
        from semabridge.sync.orchestrator import SyncOrchestrator

        job = SyncJob(direction=SyncDirection.PBIX_TO_SNOWFLAKE)
        sync_repo.create_job(job)

        item = SyncJobItem(
            job_id=job.job_id,
            model_name="test",
            status=SyncItemStatus.COMPLETED,
        )
        sync_repo.create_item(item)

        orchestrator = SyncOrchestrator(repository=sync_repo)
        status = orchestrator.get_status(job.job_id)

        assert status["summary"]["total"] == 1
        assert status["summary"]["completed"] == 1

    def test_run_with_mocked_extraction(self, sync_repo, tmp_path, sample_osi_model):
        """Test full run with mocked PBIX extraction."""
        from semabridge.sync.orchestrator import SyncOrchestrator

        # Create a dummy PBIX file
        (tmp_path / "test_model.pbix").write_bytes(b"PK\x03\x04dummy")

        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            pbix_folder=str(tmp_path),
            snowflake_schema="TEST_SCHEMA",
            enable_parallel=False,
            incremental=False,
        )

        orchestrator = SyncOrchestrator(repository=sync_repo)

        # Mock the extraction and deployment methods
        with patch.object(
            orchestrator, "_extract_pbix_to_osi", return_value=sample_osi_model
        ), patch.object(
            orchestrator, "_deploy_to_snowflake", return_value="snowflake://test/schema"
        ):
            job = orchestrator.run(config, initiated_by="test")

        assert job.status == SyncJobStatus.COMPLETED
        assert job.completed_items == 1
        assert job.failed_items == 0
        assert job.duration_ms is not None
        assert job.duration_ms > 0

    def test_run_with_conflict_blocks(self, sync_repo, tmp_path, sample_osi_model):
        """Test that critical conflicts with fail_and_approve block the job."""
        from semabridge.sync.orchestrator import SyncOrchestrator

        (tmp_path / "test_model.pbix").write_bytes(b"PK\x03\x04dummy")

        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            pbix_folder=str(tmp_path),
            conflict_resolution=ConflictResolution.FAIL_AND_APPROVE,
            enable_parallel=False,
            incremental=False,
        )

        orchestrator = SyncOrchestrator(repository=sync_repo)

        # Pre-seed a schema version so conflicts will be detected
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        tracker = SchemaEvolutionTracker(sync_repo)
        # Record with different schema (missing 'orders' table)
        modified_model = OSIModel(
            unique_name="test_model",
            datasets=[
                OSIDataset(
                    unique_name="customers",
                    columns=[
                        OSIColumn(
                            unique_name="customer_id",
                            data_type=OSIDataType.STRING,  # Different type!
                        ),
                    ],
                ),
            ],
        )
        tracker.record_version(modified_model)

        with patch.object(
            orchestrator, "_extract_pbix_to_osi", return_value=sample_osi_model
        ):
            job = orchestrator.run(config, initiated_by="test")

        # Job should be in CONFLICT or FAILED state due to critical type change
        assert job.status in (SyncJobStatus.CONFLICT, SyncJobStatus.FAILED)

    def test_run_source_wins_auto_resolves(self, sync_repo, tmp_path, sample_osi_model):
        """Test that source_wins strategy auto-resolves conflicts."""
        from semabridge.sync.orchestrator import SyncOrchestrator

        (tmp_path / "test_model.pbix").write_bytes(b"PK\x03\x04dummy")

        config = SyncConfig(
            direction=SyncDirection.PBIX_TO_SNOWFLAKE,
            pbix_folder=str(tmp_path),
            conflict_resolution=ConflictResolution.SOURCE_WINS,
            enable_parallel=False,
            incremental=False,
        )

        orchestrator = SyncOrchestrator(repository=sync_repo)

        # Pre-seed schema to force conflict detection
        from semabridge.sync.schema_evolution import SchemaEvolutionTracker

        tracker = SchemaEvolutionTracker(sync_repo)
        different_model = OSIModel(
            unique_name="test_model",
            datasets=[
                OSIDataset(
                    unique_name="customers",
                    columns=[
                        OSIColumn(
                            unique_name="customer_id",
                            data_type=OSIDataType.STRING,
                        ),
                    ],
                ),
            ],
        )
        tracker.record_version(different_model)

        with patch.object(
            orchestrator, "_extract_pbix_to_osi", return_value=sample_osi_model
        ), patch.object(
            orchestrator, "_deploy_to_snowflake", return_value="snowflake://test/schema"
        ):
            job = orchestrator.run(config, initiated_by="test")

        assert job.status == SyncJobStatus.COMPLETED
        assert job.completed_items == 1

    def test_snowflake_type_mapping(self):
        from semabridge.sync.orchestrator import SyncOrchestrator

        assert SyncOrchestrator._map_snowflake_type("VARCHAR") == OSIDataType.STRING
        assert SyncOrchestrator._map_snowflake_type("NUMBER(38,2)") == OSIDataType.DECIMAL
        assert SyncOrchestrator._map_snowflake_type("TIMESTAMP_NTZ") == OSIDataType.DATETIME
        assert SyncOrchestrator._map_snowflake_type("BOOLEAN") == OSIDataType.BOOLEAN
        assert SyncOrchestrator._map_snowflake_type("UNKNOWN_TYPE") == OSIDataType.UNKNOWN

    def test_normalize_snowflake_relationships_preserves_non_duplicates_and_renames(self):
        from semabridge.sync.orchestrator import SyncOrchestrator

        raw_relationships = [
            {
                "name": "SYS_RELATIONSHIP_123",
                "from_table": "FACT",
                "from_column": "PRODUCT_ID",
                "to_table": "PRODUCT",
                "to_column": "ID",
            },
            {
                "name": "A4CF2E2E-9A2A-4A0A-BE0D-123456789ABC",
                "from_table": "FACT",
                "from_column": "CALENDAR_ID",
                "to_table": "CALENDAR",
                "to_column": "ID",
            },
        ]

        normalized = SyncOrchestrator._normalize_snowflake_relationships(raw_relationships)

        assert len(normalized) == 2
        assert normalized[0]["name"] == "REL_FACT_PRODUCT_ID__PRODUCT_ID"
        assert normalized[1]["name"] == "REL_FACT_CALENDAR_ID__CALENDAR_ID"
        assert all(not r["name"].startswith("SYS_RELATIONSHIP") for r in normalized)

    def test_normalize_snowflake_relationships_removes_exact_duplicates_only(self):
        from semabridge.sync.orchestrator import SyncOrchestrator

        raw_relationships = [
            {
                "name": "SYS_RELATIONSHIP_1",
                "from_table": "FACT",
                "from_column": "CUSTOMER_ID",
                "to_table": "CUSTOMER",
                "to_column": "ID",
            },
            {
                "name": "SYS_RELATIONSHIP_2",
                "from_table": "FACT",
                "from_column": "CUSTOMER_ID",
                "to_table": "CUSTOMER",
                "to_column": "ID",
            },
            {
                "name": "SYS_RELATIONSHIP_3",
                "from_table": "FACT",
                "from_column": "BILL_TO_CUSTOMER_ID",
                "to_table": "CUSTOMER",
                "to_column": "ID",
            },
        ]

        normalized = SyncOrchestrator._normalize_snowflake_relationships(raw_relationships)

        assert len(normalized) == 2
        assert normalized[0]["name"] == "REL_FACT_CUSTOMER_ID__CUSTOMER_ID"
        assert normalized[1]["name"] == "REL_FACT_BILL_TO_CUSTOMER_ID__CUSTOMER_ID"
