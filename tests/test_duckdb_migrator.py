"""
Test suite for the SQLite → DuckDB migrator.

Creates a temporary SQLite database, runs the migration, and verifies
data integrity, type casting, and legacy file archival.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from semabridge.core.exceptions import RepositoryError
from semabridge.repository.duckdb_migrator import SQLiteToDuckDBMigrator, MigrationReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def legacy_sqlite(tmp_path: Path) -> Path:
    """Create a populated legacy SQLite database."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create tables matching the SemaBridge schema
    cursor.execute("""
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            workspace_id TEXT,
            last_updated TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE snapshots (
            snapshot_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            version_tag TEXT,
            sml_blob TEXT,
            status TEXT DEFAULT 'success'
        )
    """)

    # Insert sample data
    cursor.execute(
        "INSERT INTO projects VALUES (?, ?, ?, datetime('now'))",
        ("proj-001", "TestProject", "ws-001"),
    )
    cursor.execute(
        "INSERT INTO snapshots VALUES (?, ?, datetime('now'), ?, ?, ?)",
        ("snap-001", "proj-001", "v1.0", json.dumps({"tables": []}), "success"),
    )
    cursor.execute(
        "INSERT INTO snapshots VALUES (?, ?, datetime('now'), ?, ?, ?)",
        ("snap-002", "proj-001", "v1.1", json.dumps({"tables": [{"name": "Sales"}]}), "success"),
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def duckdb_target(tmp_path: Path) -> Path:
    """Provide a target path for the new DuckDB file."""
    return tmp_path / "migrated.db"


# ---------------------------------------------------------------------------
# Migration Tests
# ---------------------------------------------------------------------------

class TestMigration:
    """Test the full SQLite → DuckDB migration pipeline."""

    def test_successful_migration(
        self, legacy_sqlite: Path, duckdb_target: Path
    ) -> None:
        """Should migrate all tables and data from SQLite to DuckDB."""
        migrator = SQLiteToDuckDBMigrator(
            str(legacy_sqlite), str(duckdb_target)
        )
        report = migrator.migrate(verify=True)

        assert report.success
        assert "projects" in report.tables_migrated
        assert "snapshots" in report.tables_migrated
        assert report.rows_transferred["projects"] == 1
        assert report.rows_transferred["snapshots"] == 2
        assert len(report.tables_failed) == 0

    def test_migration_creates_duckdb_file(
        self, legacy_sqlite: Path, duckdb_target: Path
    ) -> None:
        """Should create the DuckDB file at the target path."""
        migrator = SQLiteToDuckDBMigrator(
            str(legacy_sqlite), str(duckdb_target)
        )
        migrator.migrate()
        assert duckdb_target.exists()

    def test_migration_archives_legacy(
        self, legacy_sqlite: Path, duckdb_target: Path
    ) -> None:
        """Should archive the legacy SQLite file after successful migration."""
        migrator = SQLiteToDuckDBMigrator(
            str(legacy_sqlite), str(duckdb_target)
        )
        report = migrator.migrate()

        assert report.legacy_file_archived
        assert report.archive_path is not None
        assert not legacy_sqlite.exists()  # Original should be moved
        assert Path(report.archive_path).exists()  # Archive should exist

    def test_migration_data_integrity(
        self, legacy_sqlite: Path, duckdb_target: Path
    ) -> None:
        """Should preserve all data values during migration."""
        import duckdb

        migrator = SQLiteToDuckDBMigrator(
            str(legacy_sqlite), str(duckdb_target)
        )
        migrator.migrate()

        conn = duckdb.connect(str(duckdb_target))
        try:
            # Verify project data
            row = conn.execute(
                "SELECT name FROM projects WHERE project_id = 'proj-001'"
            ).fetchone()
            assert row is not None
            assert row[0] == "TestProject"

            # Verify snapshot data
            snap = conn.execute(
                "SELECT sml_blob FROM snapshots WHERE snapshot_id = 'snap-002'"
            ).fetchone()
            assert snap is not None
            data = json.loads(snap[0])
            assert len(data["tables"]) == 1
        finally:
            conn.close()

    def test_migration_report_timing(
        self, legacy_sqlite: Path, duckdb_target: Path
    ) -> None:
        """Migration report should include accurate timing info."""
        migrator = SQLiteToDuckDBMigrator(
            str(legacy_sqlite), str(duckdb_target)
        )
        report = migrator.migrate()

        assert report.started_at is not None
        assert report.completed_at is not None
        assert report.duration_ms >= 0

    def test_migration_type_casts_logged(
        self, legacy_sqlite: Path, duckdb_target: Path
    ) -> None:
        """Should log type casts applied during migration."""
        migrator = SQLiteToDuckDBMigrator(
            str(legacy_sqlite), str(duckdb_target)
        )
        report = migrator.migrate()

        assert "projects" in report.type_casts_applied
        assert len(report.type_casts_applied["projects"]) > 0


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------

class TestMigrationErrors:
    """Test error handling in the migration pipeline."""

    def test_missing_sqlite_file(self, tmp_path: Path) -> None:
        """Should raise RepositoryError for missing SQLite file."""
        with pytest.raises(RepositoryError, match="not found"):
            SQLiteToDuckDBMigrator(
                str(tmp_path / "nonexistent.db"),
                str(tmp_path / "target.db"),
            )


# ---------------------------------------------------------------------------
# MigrationReport Tests
# ---------------------------------------------------------------------------

class TestMigrationReport:
    """Test the MigrationReport model."""

    def test_success_when_no_failures(self) -> None:
        """Report should be successful when all tables migrated."""
        report = MigrationReport()
        report.tables_migrated = ["t1", "t2"]
        report.tables_failed = []
        assert report.success

    def test_failure_when_tables_failed(self) -> None:
        """Report should fail when any table migration failed."""
        report = MigrationReport()
        report.tables_migrated = ["t1"]
        report.tables_failed = [("t2", "error")]
        assert not report.success

    def test_failure_when_no_tables(self) -> None:
        """Report should fail when no tables were migrated."""
        report = MigrationReport()
        assert not report.success

    def test_summary_format(self) -> None:
        """Summary should be human-readable."""
        report = MigrationReport()
        report.tables_migrated = ["t1"]
        report.rows_transferred = {"t1": 100}
        summary = report.summary()
        assert "SUCCEEDED" in summary
        assert "100" in summary
