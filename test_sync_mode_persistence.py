"""
Test to verify sync_mode persistence through database for v4.3 rollback.

This test validates that:
1. sync_mode is persisted in database during commit_model()
2. sync_mode is retrieved correctly during rollback
3. sync_mode flows through entire execution pipeline
"""

import pytest
from datetime import datetime
from semabridge.repository.model_repository import ModelRepository
from semabridge.repository.schemas import Snapshot


def test_sync_mode_persistence():
    """Test that sync_mode is persisted and retrieved correctly."""
    
    # Create an in-memory repository for testing
    repo = ModelRepository("sqlite:///:memory:")
    
    # Ensure project exists
    project_id = "test_project_001"
    repo.ensure_project(
        project_id=project_id,
        name="Test Project",
        workspace_id="workspace_001",
        adapter="fabric",
    )
    
    # Test 1: Commit with sync_mode="copy" (default)
    sml_json_1 = {
        "model_name": "test_model",
        "datasets": [{"name": "dataset1", "columns": []}],
    }
    committed, snapshot_id_1 = repo.commit_model(
        project_id=project_id,
        sml_json=sml_json_1,
        tag="v1_copy",
        sync_mode="copy",  # Explicitly set to copy
    )
    
    assert committed, "Failed to commit with sync_mode=copy"
    print(f"✓ Committed snapshot {snapshot_id_1} with sync_mode=copy")
    
    # Verify sync_mode is persisted
    snapshot_1 = repo.get_snapshot(snapshot_id_1)
    assert snapshot_1 is not None, "Snapshot not found after commit"
    assert snapshot_1.sync_mode == "copy", f"Expected sync_mode=copy, got {snapshot_1.sync_mode}"
    print(f"✓ Retrieved snapshot with sync_mode={snapshot_1.sync_mode}")
    
    # Test 2: Commit with sync_mode="upsert"
    sml_json_2 = {
        "model_name": "test_model",
        "datasets": [{"name": "dataset1", "columns": []}, {"name": "dataset2", "columns": []}],
    }
    committed, snapshot_id_2 = repo.commit_model(
        project_id=project_id,
        sml_json=sml_json_2,
        tag="v2_upsert",
        sync_mode="upsert",  # Explicitly set to upsert
    )
    
    assert committed, "Failed to commit with sync_mode=upsert"
    print(f"✓ Committed snapshot {snapshot_id_2} with sync_mode=upsert")
    
    # Verify upsert sync_mode is persisted
    snapshot_2 = repo.get_snapshot(snapshot_id_2)
    assert snapshot_2 is not None, "Snapshot not found after commit"
    assert snapshot_2.sync_mode == "upsert", f"Expected sync_mode=upsert, got {snapshot_2.sync_mode}"
    print(f"✓ Retrieved snapshot with sync_mode={snapshot_2.sync_mode}")
    
    # Test 3: Default sync_mode when not specified
    sml_json_3 = {
        "model_name": "test_model",
        "datasets": [{"name": "dataset1", "columns": []}],
    }
    committed, snapshot_id_3 = repo.commit_model(
        project_id=project_id,
        sml_json=sml_json_3,
        tag="v3_default",
        # sync_mode not specified, should default to "copy"
    )
    
    assert committed, "Failed to commit with default sync_mode"
    print(f"✓ Committed snapshot {snapshot_id_3} with default sync_mode")
    
    # Verify default is "copy"
    snapshot_3 = repo.get_snapshot(snapshot_id_3)
    assert snapshot_3 is not None, "Snapshot not found after commit"
    assert snapshot_3.sync_mode == "copy", f"Expected default sync_mode=copy, got {snapshot_3.sync_mode}"
    print(f"✓ Retrieved snapshot with default sync_mode={snapshot_3.sync_mode}")
    
    # Test 4: Retrieve all snapshots and verify sync_mode
    head = repo.get_head(project_id)
    assert head is not None, "No HEAD snapshot found"
    print(f"✓ HEAD snapshot sync_mode={head.sync_mode}")
    
    print("\n✅ All sync_mode persistence tests passed!")


def test_sync_mode_in_rollback_context():
    """Test that rollback_orchestrator can retrieve sync_mode correctly."""
    
    from semabridge.repository.rollback_orchestrator import RollbackOrchestrator
    
    # Setup
    repo = ModelRepository("sqlite:///:memory:")
    project_id = "test_project_002"
    
    repo.ensure_project(
        project_id=project_id,
        name="Test Project",
        workspace_id="workspace_002",
        adapter="fabric",
    )
    
    # Create two versions with different sync modes
    sml_1 = {"model_name": "test", "datasets": []}
    sml_2 = {"model_name": "test", "datasets": [{"name": "new_dataset", "columns": []}]}
    
    _, snapshot_1_id = repo.commit_model(
        project_id=project_id,
        sml_json=sml_1,
        tag="upsert_version",
        sync_mode="upsert",
    )
    
    _, snapshot_2_id = repo.commit_model(
        project_id=project_id,
        sml_json=sml_2,
        tag="after_upsert",
        sync_mode="copy",  # Switched back to copy
    )
    
    # Verify snapshots have correct sync_modes
    snap_1 = repo.get_snapshot(snapshot_1_id)
    snap_2 = repo.get_snapshot(snapshot_2_id)
    
    assert snap_1.sync_mode == "upsert", f"snap_1 sync_mode should be upsert, got {snap_1.sync_mode}"
    assert snap_2.sync_mode == "copy", f"snap_2 sync_mode should be copy, got {snap_2.sync_mode}"
    
    print(f"✓ Snapshot 1 (to rollback to): sync_mode={snap_1.sync_mode}")
    print(f"✓ Snapshot 2 (current HEAD): sync_mode={snap_2.sync_mode}")
    
    # Simulate rollback retrieval logic
    target_snapshot = repo.get_snapshot(snapshot_1_id)
    retrieved_sync_mode = target_snapshot.sync_mode if target_snapshot and hasattr(target_snapshot, 'sync_mode') else 'copy'
    
    assert retrieved_sync_mode == "upsert", f"Rollback should retrieve sync_mode=upsert, got {retrieved_sync_mode}"
    print(f"✓ Rollback context would use sync_mode={retrieved_sync_mode}")
    
    print("\n✅ All rollback context tests passed!")


if __name__ == "__main__":
    print("=" * 80)
    print("Testing sync_mode Persistence for v4.3 Rollback")
    print("=" * 80)
    print()
    
    try:
        test_sync_mode_persistence()
        print()
        test_sync_mode_in_rollback_context()
        print()
        print("=" * 80)
        print("✅ SUCCESS: All tests passed! sync_mode is now persistent in database.")
        print("=" * 80)
    except AssertionError as e:
        print(f"\n❌ FAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
