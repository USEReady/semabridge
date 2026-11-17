"""Sync-start checks added for re-uploads and Copy syncs.

* same_file: the exact same PBIX (same content hash, whatever its upload
  prefix or name) already deployed by another project is a duplicate.
* removed_fields: a COPY sync rebuilds the view, so fields that are in the
  deployed view but no longer in the source would disappear -- the start is
  refused with 409 until the user confirms or switches the run to UPSERT
  (which keeps them).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from semabridge.api.services import duplicate_sync_service as dss
from semabridge.api.services import run_service as rs


@pytest.fixture()
def db():
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import Project, Run, RunLease  # noqa: F401
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.snapshot_models import SnapshotRow  # noqa: F401

    Base.metadata.create_all(db_manager.get_engine())
    with db_manager.get_session() as s:
        for pid in ("proj-chk", "proj-other"):
            if s.get(Project, pid) is None:
                s.add(Project(project_id=pid, name=pid))
        s.commit()
    yield db_manager
    with db_manager.get_session() as s:
        s.query(SnapshotRow).filter(SnapshotRow.project_id.in_(["proj-chk", "proj-other"])).delete()
        s.query(Run).filter(Run.project_id == "proj-chk").delete()
        s.query(RunLease).filter(RunLease.project_id == "proj-chk").delete()
        s.commit()


CONFIG = "target: {type: snowflake, database: DB, schema: SCH}\n"
PLANNED = [{"model_label": "Sales", "model_key": "SALES", "view_name": "SALES", "content_hash": "h-sales"}]


# --- same file ------------------------------------------------------------------

def test_same_pbix_deployed_by_another_project_is_a_duplicate(db, monkeypatch):
    from semabridge.repository.orm.snapshot_models import SnapshotRow

    with db.get_session() as s:
        s.add(SnapshotRow(
            snapshot_id=str(uuid.uuid4()), project_id="proj-other", timestamp=datetime.now(timezone.utc),
            view_name="OTHER_VIEW", target_database="DB", target_schema="SCH",
            deployed_at=datetime.now(timezone.utc),
        ))
        s.commit()
    monkeypatch.setattr(dss, "_planned_models", lambda cfg: PLANNED)
    monkeypatch.setattr(dss, "_file_hashes_by_project", lambda: {"proj-other": {"h-sales"}})
    findings = dss.find_duplicate_deployments("proj-chk", CONFIG)
    assert findings and findings[0]["other_project_id"] == "proj-other"
    assert "same_file" in findings[0]["reasons"]
    # Re-syncing your own project is never a duplicate.
    monkeypatch.setattr(dss, "_file_hashes_by_project", lambda: {"proj-chk": {"h-sales"}})
    assert not [f for f in dss.find_duplicate_deployments("proj-chk", CONFIG) if "same_file" in f["reasons"]]


# --- removed fields -------------------------------------------------------------

DEPLOYED = {
    "metrics": [{"unique_name": "TOTAL", "original_name": "Total"}, {"unique_name": "Old Metric"}],
    "datasets": [{"unique_name": "Sales", "columns": [{"unique_name": "Amount"}, {"unique_name": "Region"}]}],
}
INCOMING = {
    "metrics": [{"unique_name": "Total"}],
    "datasets": [{"unique_name": "Sales", "columns": [{"unique_name": "Amount"}]}],
}


def test_removed_fields_lists_what_the_deployed_view_would_lose(db, monkeypatch):
    monkeypatch.setattr(dss, "_planned_models", lambda cfg: PLANNED)
    monkeypatch.setattr(dss, "_latest_blob_of", lambda ids, rows, **kw: DEPLOYED if kw.get("deployed_only") else INCOMING)
    found = dss.find_removed_fields("proj-chk", CONFIG, dry_run_project_id="preview-x")
    assert found == [{
        "model": "Sales", "view_name": "SALES", "count": 2,
        "removed": [{"kind": "column", "name": "Sales.Region"}, {"kind": "metric", "name": "Old Metric"}],
    }]


def test_first_deployment_has_nothing_to_remove(db, monkeypatch):
    monkeypatch.setattr(dss, "_planned_models", lambda cfg: PLANNED)
    monkeypatch.setattr(dss, "_latest_blob_of", lambda ids, rows, **kw: None if kw.get("deployed_only") else INCOMING)
    assert dss.find_removed_fields("proj-chk", CONFIG, dry_run_project_id="preview-x") == []


def _gate(monkeypatch, removed):
    async def ready(*a, **k):
        return "preview-x"

    class Started(Exception):
        pass

    def create_run(*a, **k):
        raise Started(k.get("sync_mode"))

    monkeypatch.setattr(rs, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(rs, "_prepare_sync_config", lambda pid: "sync_mode: copy\n" + CONFIG)
    monkeypatch.setattr(rs, "_assert_sync_ready", ready)
    monkeypatch.setattr(rs, "_create_project_run", create_run)
    # The gate prefers project_runs_impl's creator when that module is loaded.
    import sys
    pri = sys.modules.get("semabridge.api.services.project_runs_impl")
    if pri is not None and hasattr(pri, "_create_project_run"):
        monkeypatch.setattr(pri, "_create_project_run", create_run)
    monkeypatch.setattr(dss, "find_duplicate_deployments", lambda *a: [])
    monkeypatch.setattr(dss, "find_removed_fields", lambda *a: removed)
    return Started


REMOVED = [{"model": "Sales", "view_name": "SALES", "count": 1, "removed": [{"kind": "column", "name": "Sales.Region"}]}]


def test_copy_sync_with_removed_fields_is_refused_until_confirmed(db, monkeypatch):
    started = _gate(monkeypatch, REMOVED)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rs.run_project_now_compat("proj-chk", object(), {"sync_mode": "copy"}))
    assert exc.value.status_code == 409
    assert exc.value.detail["status"] == "removed_fields"
    assert exc.value.detail["total"] == 1 and exc.value.detail["models"] == REMOVED
    # Confirmed: the copy sync starts.
    with pytest.raises(started):
        asyncio.run(rs.run_project_now_compat("proj-chk", object(), {"sync_mode": "copy", "acknowledge_removed_fields": True}))


def test_upsert_never_asks_because_it_keeps_the_fields(db, monkeypatch):
    started = _gate(monkeypatch, REMOVED)
    with pytest.raises(started):
        asyncio.run(rs.run_project_now_compat("proj-chk", object(), {"sync_mode": "upsert"}))
