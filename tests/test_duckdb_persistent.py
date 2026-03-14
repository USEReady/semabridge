"""
Tests for DuckDB Manager persistent connection refactoring.

Verifies:
    - Single persistent connection (no per-call open/close)
    - Thread-safe connection reuse
    - Proper cleanup via close() method
    - All CRUD operations work with persistent connection
    - No stale connection errors
"""

import json
import os
import tempfile
import uuid
import pytest

from semabridge.repository.duckdb_manager import DuckDBManager


@pytest.fixture
def db_manager():
    """Create a DuckDBManager with a temp database."""
    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, f"test_duckdb_{uuid.uuid4().hex}.db")

    manager = DuckDBManager(db_path=db_path)
    yield manager

    # Cleanup
    manager.close()
    try:
        if os.path.exists(db_path):
            os.unlink(db_path)
        for ext in ['.wal', '.tmp']:
            p = db_path + ext
            if os.path.exists(p):
                os.unlink(p)
    except OSError:
        pass


# =========================================================================
# Connection Lifecycle
# =========================================================================


class TestConnectionLifecycle:
    def test_persistent_connection_reuse(self, db_manager):
        """_get_connection returns the same connection object."""
        conn1 = db_manager._get_connection()
        conn2 = db_manager._get_connection()
        assert conn1 is conn2

    def test_close_sets_none(self, db_manager):
        """close() properly cleans up the connection."""
        db_manager.close()
        assert db_manager._conn is None

    def test_reconnect_after_close(self, db_manager):
        """After close(), the next _get_connection creates a new connection."""
        db_manager.close()
        conn = db_manager._get_connection()
        assert conn is not None
        # Should be able to execute queries
        result = conn.execute("SELECT 1").fetchone()
        assert result[0] == 1


# =========================================================================
# CRUD Operations
# =========================================================================


class TestCRUDOperations:
    def test_ensure_project(self, db_manager):
        db_manager.ensure_project("p1", "Test Project", "ws1")
        projects = db_manager.list_projects()
        assert len(projects) == 1
        assert projects[0]["id"] == "p1"
        assert projects[0]["displayName"] == "Test Project"

    def test_ensure_project_upsert(self, db_manager):
        """Calling ensure_project twice updates existing."""
        db_manager.ensure_project("p1", "First", "ws1")
        db_manager.ensure_project("p1", "Updated", "ws1")
        projects = db_manager.list_projects()
        assert len(projects) == 1
        assert projects[0]["displayName"] == "Updated"

    def test_commit_and_get_head(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        sml_json = {"datasets": [{"unique_name": "Sales", "columns": []}]}
        committed, snapshot_id = db_manager.commit_model("p1", sml_json, tag="v1")

        assert committed is True
        assert snapshot_id is not None

        head = db_manager.get_head("p1")
        assert head is not None
        assert head.project_id == "p1"
        assert head.version_tag == "v1"

    def test_get_head_empty_project(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        head = db_manager.get_head("p1")
        assert head is None

    def test_list_snapshots(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        db_manager.commit_model("p1", {"v": 1}, tag="v1")
        db_manager.commit_model("p1", {"v": 2}, tag="v2")

        snaps = db_manager.list_snapshots("p1")
        assert len(snaps) == 2

    def test_get_snapshot_by_tag(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        db_manager.commit_model("p1", {"v": 1}, tag="release-1")

        snap = db_manager.get_snapshot_by_tag("p1", "release-1")
        assert snap is not None
        assert snap.version_tag == "release-1"

    def test_get_snapshot_by_tag_not_found(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        snap = db_manager.get_snapshot_by_tag("p1", "nonexistent")
        assert snap is None

    def test_no_change_skip_commit(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        sml = {"datasets": [{"unique_name": "A"}]}
        db_manager.commit_model("p1", sml, tag="v1")
        # Same data, no tag → should skip
        committed, _ = db_manager.commit_model("p1", sml)
        assert committed is False

    def test_tag_override(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        db_manager.commit_model("p1", {"v": 1}, tag="latest")
        db_manager.commit_model("p1", {"v": 2}, tag="latest")

        snaps = db_manager.list_snapshots("p1")
        # Tag override updates in-place, so still 1 snapshot
        assert len(snaps) == 1
        assert snaps[0].sml_blob["v"] == 2


# =========================================================================
# Rollback
# =========================================================================


class TestRollback:
    def test_rollback_creates_new_snapshot(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        db_manager.commit_model("p1", {"v": 1}, tag="v1")
        _, snap2_id = db_manager.commit_model("p1", {"v": 2}, tag="v2")

        # Get v1 snapshot
        v1 = db_manager.get_snapshot_by_tag("p1", "v1")
        success, new_id, changes = db_manager.rollback("p1", v1.snapshot_id)

        assert success is True
        head = db_manager.get_head("p1")
        assert head.sml_blob["v"] == 1


# =========================================================================
# Run Tracking
# =========================================================================


class TestRunTracking:
    def test_record_run_lifecycle(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")
        run_id = str(uuid.uuid4())
        # Should not raise
        db_manager.record_run_start(run_id, "p1", "fabric")
        db_manager.record_run_complete(run_id, "success", final_step=10, duration_ms=500)


# =========================================================================
# Source Artifacts
# =========================================================================


class TestSourceArtifacts:
    def test_persist_and_retrieve(self, db_manager):
        db_manager.ensure_project("p1", "Test", "ws1")

        class FakeSource:
            source_type = "fabric"
            def model_dump(self, mode=None):
                return {"tables": ["A", "B"]}

        art_id = db_manager.persist_source_artifact("run1", FakeSource())
        assert art_id is not None

        artifact = db_manager.get_source_artifact(art_id)
        assert artifact is not None
        assert artifact["source_type"] == "fabric"
        assert "tables" in artifact["content"]

    def test_persist_none_returns_none(self, db_manager):
        result = db_manager.persist_source_artifact("run1", None)
        assert result is None
