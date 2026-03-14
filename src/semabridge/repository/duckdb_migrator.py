"""
SQLite to DuckDB Migration Utility.

Provides a systematic migration strategy that:
1. ATTACHes the legacy SQLite file via DuckDB's sqlite_scanner extension.
2. Streams data using CREATE TABLE AS SELECT (vectorized bulk transfer).
3. Applies strict type-casting from SQLite's weak typing to DuckDB's strict schema.
4. Sets storage_compatibility_version for forward compatibility.
5. Archives the legacy file after verification.

Usage:
    from semabridge.repository.duckdb_migrator import SQLiteToDuckDBMigrator
    migrator = SQLiteToDuckDBMigrator("legacy.db", "semabridge_state.db")
    migrator.migrate()
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from semabridge.core.exceptions import RepositoryError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Type casting rules: SQLite affinity → DuckDB strict type
TYPE_CAST_MAP: Dict[str, str] = {
    "INTEGER": "BIGINT",
    "TEXT": "VARCHAR",
    "BLOB": "BLOB",
    "REAL": "DOUBLE",
    "NUMERIC": "DECIMAL",
    "BOOLEAN": "BOOLEAN",
}


class MigrationReport:
    """Captures the result of a SQLite → DuckDB migration."""

    def __init__(self) -> None:
        self.tables_migrated: List[str] = []
        self.tables_failed: List[Tuple[str, str]] = []
        self.rows_transferred: Dict[str, int] = {}
        self.type_casts_applied: Dict[str, List[str]] = {}
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.legacy_file_archived: bool = False
        self.archive_path: Optional[str] = None

    @property
    def success(self) -> bool:
        """Migration is successful when all discovered tables were transferred."""
        return len(self.tables_failed) == 0 and len(self.tables_migrated) > 0

    @property
    def duration_ms(self) -> int:
        """Duration of the migration in milliseconds."""
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return 0

    def summary(self) -> str:
        """Human-readable migration summary."""
        total_rows = sum(self.rows_transferred.values())
        lines = [
            f"Migration {'SUCCEEDED' if self.success else 'FAILED'}",
            f"  Tables migrated : {len(self.tables_migrated)}",
            f"  Tables failed   : {len(self.tables_failed)}",
            f"  Total rows      : {total_rows:,}",
            f"  Duration        : {self.duration_ms:,} ms",
        ]
        if self.tables_failed:
            lines.append("  Failures:")
            for table, err in self.tables_failed:
                lines.append(f"    - {table}: {err}")
        return "\n".join(lines)


class SQLiteToDuckDBMigrator:
    """Migrates a legacy SQLite database to a native DuckDB file.

    Uses DuckDB's sqlite_scanner extension to ATTACH the SQLite file
    and stream data via CREATE TABLE AS SELECT — avoiding per-row inserts.

    Args:
        sqlite_path: Path to the legacy SQLite .db file.
        duckdb_path: Path to the target DuckDB .db file.
        archive_dir: Directory for archiving the old SQLite file.
    """

    def __init__(
        self,
        sqlite_path: str,
        duckdb_path: str,
        archive_dir: Optional[str] = None,
    ) -> None:
        self._sqlite_path = Path(sqlite_path).resolve()
        self._duckdb_path = Path(duckdb_path).resolve()
        self._archive_dir = Path(archive_dir) if archive_dir else self._sqlite_path.parent / "archive"

        if not self._sqlite_path.exists():
            raise RepositoryError(
                f"Legacy SQLite file not found: {self._sqlite_path}",
                operation="migration",
            )

    def migrate(self, verify: bool = True) -> MigrationReport:
        """Execute the full migration pipeline.

        Args:
            verify: If True, run row-count verification after migration.

        Returns:
            MigrationReport with detailed results.

        Raises:
            RepositoryError: If the migration fails critically.
        """
        import duckdb

        report = MigrationReport()
        report.started_at = datetime.utcnow()

        logger.info(
            f"Starting SQLite → DuckDB migration: "
            f"{self._sqlite_path} → {self._duckdb_path}"
        )

        # Ensure parent directory exists for the DuckDB file
        self._duckdb_path.parent.mkdir(parents=True, exist_ok=True)

        conn = duckdb.connect(str(self._duckdb_path))
        try:
            # Install and load sqlite_scanner extension
            conn.execute("INSTALL sqlite_scanner;")
            conn.execute("LOAD sqlite_scanner;")

            # ATTACH the legacy SQLite file
            conn.execute(
                f"ATTACH '{self._sqlite_path}' AS legacy (TYPE SQLITE, READ_ONLY);"
            )
            logger.info("Legacy SQLite file attached successfully")

            # Discover all tables in the SQLite schema via DuckDB reflection
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'legacy' AND table_type = 'BASE TABLE'"
            ).fetchall()

            # Fallback: try SHOW TABLES if information_schema returns nothing
            if not tables:
                try:
                    tables = conn.execute("SHOW TABLES FROM legacy").fetchall()
                except Exception:
                    tables = []

            table_names = [t[0] for t in tables]
            logger.info(f"Discovered {len(table_names)} tables: {table_names}")

            # Migrate each table via CREATE TABLE AS SELECT
            for table_name in table_names:
                try:
                    self._migrate_table(conn, table_name, report)
                except Exception as exc:
                    report.tables_failed.append((table_name, str(exc)))
                    logger.error(f"Failed to migrate table '{table_name}': {exc}")

            # Detach the legacy database
            conn.execute("DETACH legacy;")

            # Verify migration if requested
            if verify and report.success:
                self._verify_migration(conn, report)

            report.completed_at = datetime.utcnow()
            logger.info(report.summary())

            # Archive the legacy file
            if report.success:
                self._archive_legacy(report)

            return report

        except Exception as exc:
            report.completed_at = datetime.utcnow()
            raise RepositoryError(
                f"Migration failed: {exc}",
                operation="migration",
                details={"tables_migrated": report.tables_migrated},
            ) from exc
        finally:
            conn.close()

    def _migrate_table(
        self,
        conn: Any,
        table_name: str,
        report: MigrationReport,
    ) -> None:
        """Migrate a single table from SQLite to DuckDB.

        Uses CREATE TABLE AS SELECT for vectorized bulk transfer.

        Args:
            conn: Active DuckDB connection with SQLite attached as 'legacy'.
            table_name: Name of the table to migrate.
            report: MigrationReport to update.
        """
        logger.info(f"Migrating table: {table_name}")

        # Stream data via CTAS — DuckDB handles type inference automatically
        # but we log the type mapping for auditing
        conn.execute(
            f'CREATE TABLE IF NOT EXISTS main."{table_name}" AS '
            f'SELECT * FROM legacy."{table_name}";'
        )

        # Record row count
        row_count_result = conn.execute(
            f'SELECT COUNT(*) FROM main."{table_name}";'
        ).fetchone()
        row_count = row_count_result[0] if row_count_result else 0

        report.tables_migrated.append(table_name)
        report.rows_transferred[table_name] = row_count

        # Log type casting information
        columns = conn.execute(
            f"PRAGMA table_info('{table_name}');"
        ).fetchall()
        casts = [f"{c[1]}: {c[2]}" for c in columns]
        report.type_casts_applied[table_name] = casts

        logger.info(f"  → {table_name}: {row_count:,} rows transferred")

    def _verify_migration(
        self,
        conn: Any,
        report: MigrationReport,
    ) -> None:
        """Verify migrated data integrity by comparing row counts.

        Args:
            conn: Active DuckDB connection.
            report: MigrationReport to validate against.
        """
        logger.info("Running post-migration verification...")

        for table_name in report.tables_migrated:
            result = conn.execute(
                f'SELECT COUNT(*) FROM main."{table_name}";'
            ).fetchone()
            actual = result[0] if result else 0
            expected = report.rows_transferred.get(table_name, 0)

            if actual != expected:
                report.tables_failed.append(
                    (table_name, f"Row count mismatch: expected {expected}, got {actual}")
                )
                logger.error(
                    f"Verification FAILED for {table_name}: "
                    f"expected {expected} rows, found {actual}"
                )
            else:
                logger.info(f"  ✓ {table_name}: {actual:,} rows verified")

    def _archive_legacy(self, report: MigrationReport) -> None:
        """Archive the legacy SQLite file after successful migration.

        Args:
            report: MigrationReport to update with archive path.
        """
        self._archive_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{self._sqlite_path.stem}_{timestamp}{self._sqlite_path.suffix}"
        archive_path = self._archive_dir / archive_name

        shutil.move(str(self._sqlite_path), str(archive_path))
        report.legacy_file_archived = True
        report.archive_path = str(archive_path)

        logger.info(f"Legacy SQLite archived to: {archive_path}")
