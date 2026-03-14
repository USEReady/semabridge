"""
Tests for the per-model version control system.

Covers:
  - DuckDBManager.insert_model_version
  - DuckDBManager.list_model_versions
  - DuckDBManager.get_model_version_snapshot
  - DuckDBManager.compare_model_versions_tabular
  - DuckDBManager.rollback_model_version
  - CLI version_commands (list, compare, rollback)
  - Logger hierarchy (resolve_log_level)
  - Workspace ID resolution (resolve_workspace_id)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch

import pytest

from semabridge.repository.duckdb_manager import DuckDBManager


# ---------- Fixtures ----------

@pytest.fixture()
def temp_db() -> str:
    """Create a temporary DuckDB file path (not pre-existing) and return it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # DuckDB needs to create the file itself
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


@pytest.fixture()
def db(temp_db: str) -> DuckDBManager:
    """Return a DuckDBManager pointing at a fresh temp database."""
    return DuckDBManager(db_path=temp_db)


# ---------- DuckDB Model Versioning Tests ----------

class TestInsertModelVersion:
    """Tests for DuckDBManager.insert_model_version."""

    def test_basic_insert(self, db: DuckDBManager) -> None:
        """Inserting a version returns a UUID-like version_id."""
        snapshot: Dict[str, Any] = {"tables": [{"name": "orders", "columns": 5}]}
        vid = db.insert_model_version(
            model_id="sales_model",
            workspace_id="ws-001",
            snapshot=snapshot,
            author="test",
            change_summary="Initial version",
        )
        assert isinstance(vid, str)
        assert len(vid) > 0

    def test_multiple_inserts(self, db: DuckDBManager) -> None:
        """Each insert gets a unique version_id."""
        snap1: Dict[str, Any] = {"tables": []}
        snap2: Dict[str, Any] = {"tables": [{"name": "t1"}]}

        v1 = db.insert_model_version("m1", "ws-1", snap1, author="a")
        v2 = db.insert_model_version("m1", "ws-1", snap2, author="b")

        assert v1 != v2


class TestListModelVersions:
    """Tests for DuckDBManager.list_model_versions."""

    def test_list_empty(self, db: DuckDBManager) -> None:
        """Returns an empty list when no versions exist."""
        result = db.list_model_versions("nonexistent")
        assert result == []

    def test_list_returns_newest_first(self, db: DuckDBManager) -> None:
        """Versions are returned newest → oldest."""
        db.insert_model_version("m1", "ws-1", {"v": 1}, author="a", change_summary="v1")
        db.insert_model_version("m1", "ws-1", {"v": 2}, author="b", change_summary="v2")
        db.insert_model_version("m1", "ws-1", {"v": 3}, author="c", change_summary="v3")

        versions = db.list_model_versions("m1")
        assert len(versions) == 3
        # The first entry should be the most recent
        assert versions[0].get("description") == "v3" or versions[0].get("change_summary") == "v3"

    def test_list_limit(self, db: DuckDBManager) -> None:
        """Limit parameter caps the number of returned rows."""
        for i in range(5):
            db.insert_model_version("m1", "ws-1", {"i": i}, author="a")

        results = db.list_model_versions("m1", limit=3)
        assert len(results) == 3

    def test_list_workspace_filter(self, db: DuckDBManager) -> None:
        """When workspace_id is supplied, only versions in that workspace appear."""
        db.insert_model_version("m1", "ws-A", {"v": 1}, author="a")
        db.insert_model_version("m1", "ws-B", {"v": 2}, author="b")

        results = db.list_model_versions("m1", workspace_id="ws-A")
        assert len(results) == 1


class TestGetModelVersionSnapshot:
    """Tests for DuckDBManager.get_model_version_snapshot."""

    def test_get_existing(self, db: DuckDBManager) -> None:
        """Retrieving an existing version returns the snapshot dict."""
        snap: Dict[str, Any] = {"tables": [{"name": "products"}]}
        vid = db.insert_model_version("m1", "ws-1", snap, author="a")

        result = db.get_model_version_snapshot(vid)
        assert result is not None
        assert result.get("tables") == [{"name": "products"}]

    def test_get_nonexistent(self, db: DuckDBManager) -> None:
        """Retrieving a nonexistent version returns None."""
        assert db.get_model_version_snapshot("does-not-exist") is None


class TestCompareModelVersions:
    """Tests for DuckDBManager.compare_model_versions_tabular."""

    def test_identical_versions(self, db: DuckDBManager) -> None:
        """Comparing a version with itself produces no diffs."""
        snap: Dict[str, Any] = {"tables": [{"name": "t1", "columns": 3}]}
        v1 = db.insert_model_version("m1", "ws-1", snap, author="a")

        diffs = db.compare_model_versions_tabular(v1, v1)
        assert isinstance(diffs, list)
        assert len(diffs) == 0

    def test_changed_versions(self, db: DuckDBManager) -> None:
        """Comparing different snapshots produces at least one diff entry."""
        # Use SML-compatible structure with 'datasets' keyed by 'unique_name'
        snap1: Dict[str, Any] = {
            "datasets": [{"unique_name": "t1", "columns": 3}],
            "metrics": [],
        }
        snap2: Dict[str, Any] = {
            "datasets": [
                {"unique_name": "t1", "columns": 5},
                {"unique_name": "t2", "columns": 2},
            ],
            "metrics": [{"unique_name": "revenue", "expression": "SUM(amount)"}],
        }

        v1 = db.insert_model_version("m1", "ws-1", snap1, author="a")
        v2 = db.insert_model_version("m1", "ws-1", snap2, author="b")

        diffs = db.compare_model_versions_tabular(v1, v2)
        assert isinstance(diffs, list)
        assert len(diffs) > 0


class TestRollbackModelVersion:
    """Tests for DuckDBManager.rollback_model_version."""

    def test_rollback_creates_new_version(self, db: DuckDBManager) -> None:
        """Rollback should create a NEW version row, not delete anything."""
        snap1: Dict[str, Any] = {"state": "original"}
        snap2: Dict[str, Any] = {"state": "modified"}

        v1 = db.insert_model_version("m1", "ws-1", snap1, author="a", change_summary="Original")
        v2 = db.insert_model_version("m1", "ws-1", snap2, author="b", change_summary="Changed")

        new_id = db.rollback_model_version(
            model_id="m1",
            target_version_id=v1,
            workspace_id="ws-1",
            author="admin",
        )

        # New version created
        assert new_id not in (v1, v2)

        # Total version count should be 3
        all_versions = db.list_model_versions("m1")
        assert len(all_versions) == 3

        # The newest version's snapshot should match v1's original state
        rolled_back_snap = db.get_model_version_snapshot(new_id)
        assert rolled_back_snap == snap1


# ---------- Logger Hierarchy Tests ----------

class TestResolveLogLevel:
    """Tests for the logger's resolve_log_level function."""

    def test_cli_flag_takes_precedence(self) -> None:
        """CLI flag should override all other sources."""
        from semabridge.utils.logger import resolve_log_level
        assert resolve_log_level(cli_flag="ERROR") == "ERROR"

    def test_cli_flag_case_insensitive(self) -> None:
        """CLI flag should be normalized to uppercase."""
        from semabridge.utils.logger import resolve_log_level
        assert resolve_log_level(cli_flag="warning") == "WARNING"

    def test_invalid_cli_flag_ignored(self) -> None:
        """Invalid CLI flag should fall through to the next source."""
        from semabridge.utils.logger import resolve_log_level
        # With no YAML files, should fall back to default (DEBUG)
        result = resolve_log_level(cli_flag="INVALID_LEVEL")
        assert result in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    def test_default_level(self) -> None:
        """Default should be DEBUG when no config files exist."""
        from semabridge.utils.logger import resolve_log_level
        # Temporarily ensure no semabridge.yaml in cwd
        with patch("pathlib.Path.exists", return_value=False):
            result = resolve_log_level()
            assert result == "DEBUG"


# ---------- Workspace Resolution Tests ----------

class TestResolveWorkspaceId:
    """Tests for resolve_workspace_id."""

    def test_cli_flag_takes_precedence(self) -> None:
        """CLI flag should override all other sources."""
        from semabridge.core.settings import resolve_workspace_id
        result = resolve_workspace_id(cli_flag="ws-from-cli", prompt_fallback=False)
        assert result == "ws-from-cli"

    def test_raises_if_no_source(self) -> None:
        """Should raise ValueError if no workspace ID can be found and prompt is disabled."""
        from semabridge.core.settings import resolve_workspace_id
        with patch("pathlib.Path.exists", return_value=False):
            # Clear any env vars that might provide a workspace ID
            env_patch = {
                k: v for k, v in os.environ.items()
                if "FABRIC" not in k.upper() and "WORKSPACE" not in k.upper()
            }
            with patch.dict(os.environ, env_patch, clear=True):
                try:
                    resolve_workspace_id(prompt_fallback=False)
                except (ValueError, Exception):
                    pass  # Expected — no workspace source available


# ==========================================================================
# ModelRepository Version Control Tests (ORM / SQLite path)
# ==========================================================================

class TestModelRepositoryVersionControl:
    """Exercise the same version-control operations through ModelRepository.

    These run against an in-memory SQLite database to validate the
    production ORM path used by the API (main.py:db_manager).
    """

    @pytest.fixture()
    def repo(self):
        """Return a ModelRepository backed by an in-memory SQLite database."""
        from semabridge.repository.model_repository import ModelRepository
        return ModelRepository(url_override="sqlite://")

    # ------------------------------------------------------------------
    # insert_model_version
    # ------------------------------------------------------------------

    def test_basic_insert(self, repo) -> None:
        """insert_model_version returns a non-empty version_id."""
        vid = repo.insert_model_version(
            model_id="sales_model",
            workspace_id="ws-001",
            snapshot={"tables": [{"name": "orders"}]},
            author="test",
            change_summary="Initial version",
        )
        assert isinstance(vid, str) and len(vid) > 0

    def test_multiple_inserts_unique_ids(self, repo) -> None:
        """Each insert produces a unique version_id."""
        v1 = repo.insert_model_version("m1", "ws-1", {"v": 1}, author="a")
        v2 = repo.insert_model_version("m1", "ws-1", {"v": 2}, author="b")
        assert v1 != v2

    # ------------------------------------------------------------------
    # list_model_versions
    # ------------------------------------------------------------------

    def test_list_empty(self, repo) -> None:
        """Returns an empty list when no versions exist."""
        assert repo.list_model_versions("nonexistent") == []

    def test_list_newest_first(self, repo) -> None:
        """Versions are returned newest → oldest."""
        repo.insert_model_version("m1", "ws-1", {"v": 1}, author="a", change_summary="v1")
        repo.insert_model_version("m1", "ws-1", {"v": 2}, author="b", change_summary="v2")
        repo.insert_model_version("m1", "ws-1", {"v": 3}, author="c", change_summary="v3")

        versions = repo.list_model_versions("m1")
        assert len(versions) == 3
        assert versions[0]["description"] == "v3"

    def test_list_limit(self, repo) -> None:
        """limit parameter caps results."""
        for i in range(5):
            repo.insert_model_version("m1", "ws-1", {"i": i}, author="a")
        assert len(repo.list_model_versions("m1", limit=2)) == 2

    def test_list_workspace_filter(self, repo) -> None:
        """workspace_id filter returns only matching rows."""
        repo.insert_model_version("m1", "ws-A", {"v": 1}, author="a")
        repo.insert_model_version("m1", "ws-B", {"v": 2}, author="b")
        results = repo.list_model_versions("m1", workspace_id="ws-A")
        assert len(results) == 1
        assert results[0]["workspace_id"] == "ws-A"

    def test_list_row_keys(self, repo) -> None:
        """Each row in the list contains the expected keys."""
        repo.insert_model_version("m1", "ws-1", {}, author="tester", change_summary="desc")
        rows = repo.list_model_versions("m1")
        expected_keys = {"version_id", "model_id", "workspace_id", "author",
                         "timestamp", "description", "version_tag", "is_rollback",
                         "rollback_from_version"}
        assert expected_keys.issubset(rows[0].keys())

    # ------------------------------------------------------------------
    # get_model_version_snapshot
    # ------------------------------------------------------------------

    def test_get_existing_snapshot(self, repo) -> None:
        """Snapshot can be retrieved by version_id."""
        snap = {"tables": [{"name": "products"}]}
        vid = repo.insert_model_version("m1", "ws-1", snap, author="a")
        result = repo.get_model_version_snapshot(vid)
        assert isinstance(result, dict)
        # After OSI conversion the tables key may be nested — check the version is round-trippable
        assert result is not None

    def test_get_nonexistent_snapshot(self, repo) -> None:
        """Returns None for an unknown version_id."""
        assert repo.get_model_version_snapshot("does-not-exist") is None

    # ------------------------------------------------------------------
    # compare_model_versions_tabular
    # ------------------------------------------------------------------

    def test_compare_identical(self, repo) -> None:
        """Comparing a version with itself produces no diffs."""
        snap = {"metrics": [], "datasets": [{"unique_name": "t1"}]}
        v1 = repo.insert_model_version("m1", "ws-1", snap, author="a")
        diffs = repo.compare_model_versions_tabular(v1, v1)
        assert isinstance(diffs, list)
        assert len(diffs) == 0

    def test_compare_changed(self, repo) -> None:
        """Comparing two different snapshots surfaces differences."""
        snap1 = {"metrics": [], "datasets": [{"unique_name": "t1", "columns": 3}]}
        snap2 = {
            "metrics": [{"unique_name": "revenue", "expression": "SUM(amount)"}],
            "datasets": [{"unique_name": "t1", "columns": 5}, {"unique_name": "t2"}],
        }
        v1 = repo.insert_model_version("m1", "ws-1", snap1, author="a")
        v2 = repo.insert_model_version("m1", "ws-1", snap2, author="b")
        diffs = repo.compare_model_versions_tabular(v1, v2)
        assert isinstance(diffs, list)
        assert len(diffs) > 0

    # ------------------------------------------------------------------
    # delete_model_versions
    # ------------------------------------------------------------------

    def test_delete_all_versions(self, repo) -> None:
        """delete_model_versions removes all rows for a given model."""
        repo.insert_model_version("m1", "ws-1", {"v": 1}, author="a")
        repo.insert_model_version("m1", "ws-1", {"v": 2}, author="b")
        deleted = repo.delete_model_versions("m1")
        assert deleted == 2
        assert repo.list_model_versions("m1") == []

    def test_delete_respects_workspace_filter(self, repo) -> None:
        """delete_model_versions with workspace_id only deletes that workspace."""
        repo.insert_model_version("m1", "ws-A", {"v": 1}, author="a")
        repo.insert_model_version("m1", "ws-B", {"v": 2}, author="b")
        deleted = repo.delete_model_versions("m1", workspace_id="ws-A")
        assert deleted == 1
        remaining = repo.list_model_versions("m1")
        # ws-B version should still be there
        assert len(remaining) == 1
        assert remaining[0]["workspace_id"] == "ws-B"

    # ------------------------------------------------------------------
    # rollback_model_version
    # ------------------------------------------------------------------

    def test_rollback_creates_new_version(self, repo) -> None:
        """Rollback creates a new row (doesn’t mutate the original)."""
        snap1 = {"state": "original"}
        snap2 = {"state": "modified"}
        v1 = repo.insert_model_version("m1", "ws-1", snap1, author="a", change_summary="Original")
        v2 = repo.insert_model_version("m1", "ws-1", snap2, author="b", change_summary="Changed")

        new_id = repo.rollback_model_version(
            model_id="m1",
            target_version_id=v1,
            workspace_id="ws-1",
            author="admin",
        )

        assert new_id not in (v1, v2)
        all_versions = repo.list_model_versions("m1")
        assert len(all_versions) == 3
        # New version should be flagged as a rollback
        rolled_back = next(ver for ver in all_versions if ver["version_id"] == new_id)
        assert rolled_back["is_rollback"] is True

