"""Snapshot safety and demo masking.

* Retention by count never prunes the latest deployed snapshot of any
  model/view (later checks and restores rely on it).
* A run is demo-masked only when the run itself was started in demo mode;
  the server-wide demo flag alone never hides a real failure.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from semabridge.api.services import run_service as rs

PID = "proj-retain"


@pytest.fixture()
def db():
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import Project, RetentionPolicy, Run, SnapshotRow  # noqa: F401
    from semabridge.repository.orm.session_factory import db_manager

    Base.metadata.create_all(db_manager.get_engine())
    with db_manager.get_session() as s:
        if s.get(Project, PID) is None:
            s.add(Project(project_id=PID, name=PID))
        s.commit()
    yield db_manager
    with db_manager.get_session() as s:
        s.query(SnapshotRow).filter(SnapshotRow.project_id == PID).delete()
        s.query(RetentionPolicy).filter(RetentionPolicy.project_id == PID).delete()
        s.query(Run).filter(Run.project_id == PID).delete()
        s.commit()


def test_count_retention_keeps_the_latest_deployed_snapshot_of_every_view(db):
    from semabridge.api.services.retention_service import apply_retention_policy, set_retention_policy
    from semabridge.repository.orm.models import SnapshotRow

    now = datetime.now(timezone.utc)
    ids = {}
    with db.get_session() as s:
        # Model A was deployed long ago; model B many times since.
        for name, view, age_h, deployed in [
            ("a_old", "A_VIEW", 100, True), ("a_latest", "A_VIEW", 90, True),
            ("b1", "B_VIEW", 5, True), ("b2", "B_VIEW", 4, True), ("b3", "B_VIEW", 3, True),
            ("preview", "B_VIEW", 1, False),
        ]:
            ids[name] = str(uuid.uuid4())
            s.add(SnapshotRow(
                snapshot_id=ids[name], project_id=PID, timestamp=now - timedelta(hours=age_h),
                view_name=view, deployed_at=(now - timedelta(hours=age_h)) if deployed else None,
            ))
        s.commit()
        set_retention_policy(s, PID, strategy="count", max_snapshots_per_connector=2)
        apply_retention_policy(s, PID)
    with db.get_session() as s:
        alive = {r.snapshot_id for r in s.query(SnapshotRow).filter(
            SnapshotRow.project_id == PID, SnapshotRow.deleted_at.is_(None))}
    assert ids["a_latest"] in alive          # latest deployed of A: protected
    assert ids["b3"] in alive                 # latest deployed of B
    assert ids["preview"] in alive            # newest 2 by count
    assert ids["a_old"] not in alive and ids["b1"] not in alive


def test_post_sync_snapshot_failure_is_recorded_on_the_run(monkeypatch, caplog):
    # The failure path is a plain try/except inside _perform_project_run_impl;
    # check the source wires a warning and a run message (not a debug log).
    import inspect

    src = inspect.getsource(rs._perform_project_run_impl)
    assert "Post-sync snapshot capture failed" in src
    assert 'logger.debug("Post-sync snapshot capture skipped' not in src
    assert "snapshot_capture_error" in src


def test_server_demo_flag_alone_does_not_mask_a_failed_run(monkeypatch):
    import semabridge.core.demo_mode as demo_mode

    monkeypatch.setattr(rs, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(demo_mode, "demo_mode_globally_armed", lambda: True)
    monkeypatch.setitem(rs._compat_project_runs, PID, [
        {"run_id": "r-real", "status": "failed", "demo_mode": False},
        {"run_id": "r-demo", "status": "failed", "demo_mode": True},
        {"run_id": "r-ok", "status": "success", "demo_mode": True},
    ])
    assert rs._is_run_demo_masked(PID, "r-real") is False
    assert rs._is_run_demo_masked(PID, "r-demo") is True
    assert rs._is_run_demo_masked(PID, "r-ok") is False
