"""
Integration Test — SQLAlchemy ORM Layer.

Verifies:
    1. DuckDB-backed session can create tables and persist SnapshotRow.
    2. Version and SyncRun rows round-trip correctly.
    3. ``get_session`` works with an explicit in-memory DuckDB URL.
    4. ``reset_engine`` cleanly disposes and allows re-init.

Run:
    pytest tests/integration/test_orm_storage.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from semabridge.storage.orm import (
    Base,
    SnapshotRow,
    VersionRow,
    SyncRunRow,
    get_engine,
    get_session,
    reset_engine,
)


# Use an in-memory DuckDB for test isolation
TEST_DB_URL = "duckdb:///:memory:"


@pytest.fixture(autouse=True)
def _fresh_engine():
    """Ensure each test gets a clean engine."""
    reset_engine()
    yield
    reset_engine()


class TestSnapshotRow:
    def test_create_and_read(self):
        with get_session(database_url=TEST_DB_URL) as session:
            row = SnapshotRow(
                snapshot_id=str(uuid.uuid4())[:12],
                tenant_id="tenant-1",
                model_id="model-A",
                timestamp=datetime.now(timezone.utc),
                sml_blob='{"datasets": []}',
                status="success",
                run_id="run-001",
            )
            session.add(row)
            session.commit()

            fetched = (
                session.query(SnapshotRow)
                .filter_by(tenant_id="tenant-1")
                .first()
            )
            assert fetched is not None
            assert fetched.model_id == "model-A"
            assert fetched.status == "success"

    def test_multiple_snapshots_ordered(self):
        with get_session(database_url=TEST_DB_URL) as session:
            for i in range(5):
                session.add(
                    SnapshotRow(
                        snapshot_id=f"snap-{i}",
                        tenant_id="t",
                        model_id="m",
                        timestamp=datetime(2026, 1, 1 + i, tzinfo=timezone.utc),
                        status="success",
                    )
                )
            session.commit()

            latest = (
                session.query(SnapshotRow)
                .filter_by(tenant_id="t", model_id="m")
                .order_by(SnapshotRow.timestamp.desc())
                .first()
            )
            assert latest.snapshot_id == "snap-4"


class TestVersionRow:
    def test_round_trip(self):
        with get_session(database_url=TEST_DB_URL) as session:
            session.add(
                VersionRow(
                    version_tag="v2.0",
                    tenant_id="t",
                    model_id="m",
                    snapshot_id="snap-x",
                    notes="Release notes here",
                )
            )
            session.commit()

            row = session.query(VersionRow).filter_by(version_tag="v2.0").first()
            assert row is not None
            assert row.notes == "Release notes here"


class TestSyncRunRow:
    def test_create(self):
        with get_session(database_url=TEST_DB_URL) as session:
            session.add(
                SyncRunRow(
                    run_id="run-abc",
                    tenant_id="t",
                    model_id="m",
                    status="completed",
                    orchestrator="temporal",
                    changes_detected=7,
                )
            )
            session.commit()

            row = session.query(SyncRunRow).filter_by(run_id="run-abc").first()
            assert row.orchestrator == "temporal"
            assert row.changes_detected == 7
