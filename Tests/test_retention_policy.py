"""
Test retention policy implementation.

Tests cover:
1. Policy creation and retrieval
2. Safe pruning (never removes referenced snapshots)
3. Manual snapshot protection
4. Per-connector pruning
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


class TestRetentionPolicy:
    """Test retention policy CRUD operations."""
    
    def test_set_and_get_policy(self):
        """Test setting and retrieving a retention policy."""
        session = db_manager._session()
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
        session = db_manager._session()
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
        session = db_manager._session()
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
    
    def test_protect_manual_snapshots(self):
        """Ensure manual snapshots are protected when flag is set."""
        session = db_manager._session()
        project_id = f"test_proj_{uuid4()}"
        manual_snap_id = f"snap_manual_{uuid4()}"
        auto_snap_id = f"snap_auto_{uuid4()}"
        
        try:
            # Create manual snapshot
            manual_snap = SnapshotRow(
                snapshot_id=manual_snap_id,
                project_id=project_id,
                timestamp=datetime.utcnow() - timedelta(days=60),
                status="success",
                trigger="manual",
            )
            session.add(manual_snap)
            
            # Create auto snapshot
            auto_snap = SnapshotRow(
                snapshot_id=auto_snap_id,
                project_id=project_id,
                timestamp=datetime.utcnow() - timedelta(days=60),
                status="success",
                trigger="scheduled",
            )
            session.add(auto_snap)
            session.commit()
            
            # Set policy that doesn't prune manual snapshots
            set_retention_policy(
                session, project_id,
                strategy="days",
                max_age_days=1,
                prune_manual_snapshots=False,
            )
            
            result = apply_retention_policy(session, project_id)
            
            # Manual snapshot should be kept, auto snapshot pruned
            assert result["pruned"] == 1  # Only auto snapshot
            
        finally:
            session.close()


class TestPerConnectorPruning:
    """Test per-connector retention policy application."""
    
    def test_prune_per_connector(self):
        """Test that count strategy applies per connector."""
        session = db_manager._session()
        project_id = f"test_proj_{uuid4()}"
        connector_a = f"conn_a_{uuid4()}"
        connector_b = f"conn_b_{uuid4()}"
        
        try:
            # Create 10 snapshots for connector A
            for i in range(10):
                snap = SnapshotRow(
                    snapshot_id=f"snap_a_{i}_{uuid4()}",
                    project_id=project_id,
                    connector_id=connector_a,
                    timestamp=datetime.utcnow() - timedelta(days=i),
                    status="success",
                )
                session.add(snap)
            
            # Create 10 snapshots for connector B
            for i in range(10):
                snap = SnapshotRow(
                    snapshot_id=f"snap_b_{i}_{uuid4()}",
                    project_id=project_id,
                    connector_id=connector_b,
                    timestamp=datetime.utcnow() - timedelta(days=i),
                    status="success",
                )
                session.add(snap)
            
            session.commit()
            
            # Set policy: keep only 5 per connector
            set_retention_policy(
                session, project_id,
                strategy="count",
                max_snapshots_per_connector=5,
            )
            
            result = apply_retention_policy(session, project_id)
            
            # Should prune 5 from each connector (10 total)
            assert result["pruned"] == 10
            
        finally:
            session.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
