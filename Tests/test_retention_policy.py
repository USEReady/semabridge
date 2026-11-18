"""
Test retention policy implementation.

Tests cover:
1. Policy creation and retrieval
2. Safe pruning (never removes referenced snapshots)
3. Latest deployed snapshot protection
4. Project-wide count pruning
5. Strategy handling (count, days, unlimited)
"""

import pytest
from datetime import datetime, timedelta
from uuid import uuid4

from semabridge.repository.orm.models import RetentionPolicy, SnapshotRow, Run
from semabridge.repository.orm.session_factory import db_manager
from semabridge.api.services.retention_service import (
    set_retention_policy,
    get_retention_policy,
    apply_retention_policy,
    _get_all_referenced_snapshot_ids,
)
from semabridge.repository.orm.base import Base


@pytest.fixture(autouse=True)
def _schema():
    """Create the ORM tables on the test engine (the in-memory SQLite DB
    starts empty; nothing else guarantees they exist when this file runs
    alone or first)."""
    Base.metadata.create_all(bind=db_manager.get_engine())
    yield


class TestRetentionPolicy:
    """Test retention policy CRUD operations."""
    
    def test_set_and_get_policy(self):
        """Test setting and retrieving a retention policy."""
        session = db_manager.get_session_factory()()
        project_id = f"test_proj_{uuid4()}"
        
        try:
            # Set policy
            policy = set_retention_policy(
                session, project_id,
                strategy="count",
                max_snapshots_per_connector=5,
                prune_manual_snapshots=False,
            )
            
            assert policy.strategy == "count"
            assert policy.max_snapshots_per_connector == 5
            assert policy.prune_manual_snapshots is False
            
            # Get policy
            retrieved = get_retention_policy(session, project_id)
            assert retrieved is not None
            assert retrieved.strategy == "count"
            assert retrieved.max_snapshots_per_connector == 5
            
        finally:
            session.close()
    
    def test_policy_strategies(self):
        """Test different policy strategies."""
        session = db_manager.get_session_factory()()
        project_id = f"test_proj_{uuid4()}"
        
        try:
            # Test 'days' strategy
            policy = set_retention_policy(
                session, project_id,
                strategy="days",
                max_age_days=30,
            )
            assert policy.strategy == "days"
            assert policy.max_age_days == 30
            
            # Test 'unlimited' strategy
            policy = set_retention_policy(
                session, project_id,
                strategy="unlimited",
            )
            assert policy.strategy == "unlimited"
            
        finally:
            session.close()


class TestSafePruning:
    """Test that pruning never removes referenced snapshots."""
    
    def test_never_prune_referenced_snapshots(self):
        """Ensure snapshots referenced by runs are never pruned."""
        session = db_manager.get_session_factory()()
        project_id = f"test_proj_{uuid4()}"
        snapshot_id = f"snap_{uuid4()}"
        run_id = f"run_{uuid4()}"
        
        try:
            # Create snapshot
            snapshot = SnapshotRow(
                snapshot_id=snapshot_id,
                project_id=project_id,
                timestamp=datetime.utcnow() - timedelta(days=60),  # Old
                status="success",
            )
            session.add(snapshot)
            
            # Create run that references this snapshot
            run = Run(
                run_id=run_id,
                project_id=project_id,
                started_at=datetime.utcnow(),
                status="success",
                before_src_snapshot_id=snapshot_id,  # References the snapshot
            )
            session.add(run)
            session.commit()
            
            # Set aggressive retention policy (keep only 1 day)
            set_retention_policy(
                session, project_id,
                strategy="days",
                max_age_days=1,
            )
            
            # Apply retention - should NOT prune the referenced snapshot
            result = apply_retention_policy(session, project_id)
            
            # Snapshot should still exist (not pruned because referenced)
            assert result["pruned"] == 0
            assert result["referenced"] == 1
            
        finally:
            session.close()
    
    def test_latest_deployed_snapshot_per_view_is_never_pruned(self):
        """Snapshots have no trigger/manual flag any more (d4dee7c made
        retention project-wide); what pruning must protect besides run
        references is each view's latest DEPLOYED snapshot (04c2122)."""
        session = db_manager.get_session_factory()()
        project_id = f"test_proj_{uuid4()}"
        deployed_snap_id = f"snap_deployed_{uuid4()}"
        plain_snap_id = f"snap_plain_{uuid4()}"

        try:
            old = datetime.utcnow() - timedelta(days=60)
            session.add(SnapshotRow(
                snapshot_id=deployed_snap_id,
                project_id=project_id,
                timestamp=old,
                status="success",
                deployed_at=old,
            ))
            session.add(SnapshotRow(
                snapshot_id=plain_snap_id,
                project_id=project_id,
                timestamp=old,
                status="success",
            ))
            session.commit()

            set_retention_policy(
                session, project_id,
                strategy="days",
                max_age_days=1,
            )

            result = apply_retention_policy(session, project_id)

            # Only the never-deployed snapshot is pruned.
            assert result["pruned"] == 1
            pruned = {
                row.snapshot_id for row in session.query(SnapshotRow).filter(
                    SnapshotRow.project_id == project_id,
                    SnapshotRow.deleted_at.isnot(None),
                )
            }
            assert pruned == {plain_snap_id}

        finally:
            session.close()


class TestProjectWidePruning:
    """The count strategy applies across the whole project (d4dee7c removed
    the per-connector split; ``connector_id`` is accepted and ignored)."""

    def test_count_strategy_is_project_wide(self):
        session = db_manager.get_session_factory()()
        project_id = f"test_proj_{uuid4()}"

        try:
            for i in range(20):
                session.add(SnapshotRow(
                    snapshot_id=f"snap_{i}_{uuid4()}",
                    project_id=project_id,
                    timestamp=datetime.utcnow() - timedelta(days=i),
                    status="success",
                ))
            session.commit()

            set_retention_policy(
                session, project_id,
                strategy="count",
                max_snapshots_per_connector=5,
            )

            result = apply_retention_policy(session, project_id, connector_id="ignored")

            assert result["pruned"] == 15
            assert result["kept"] == 5

        finally:
            session.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
