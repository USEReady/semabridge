"""Every way of starting a sync runs the same checks, and nothing is
deployed or removed without the user knowing.

* run-now / retry / deploy / single-project job trigger: 409s the UI dialog
  shows (duplicate, duplicate_check_failed, removed_fields incl.
  unverifiable models);
* schedules and background triggers (nobody to answer): the same checks; a
  finding records the run as failed with what to do, nothing is deployed;
* UPSERT -> COPY promotion comes from deployed snapshots (not the in-memory
  run history) and happens before the removed-fields check;
* the removed-fields check reads, per model, the dry run the gate accepted
  (and only those snapshot rows); a deployed model with no usable dry run
  cannot be verified and is refused;
* the duplicate check fails closed and also sees the same file dry-run or
  being synced by another project that has not deployed it yet.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from semabridge.api.services import duplicate_sync_service as dss
from semabridge.api.services import run_service as rs
from semabridge.core import run_coordination as rc

PID = "proj-safe"
OTHER = "proj-safe-other"
CONFIG = "target: {type: snowflake, database: DB, schema: SCH}\n"
PLANNED = [{"model_label": "Sales", "model_key": "SALES", "view_name": "SALES", "content_hash": "h-sales"}]
REMOVED = [{"model": "Sales", "view_name": "SALES", "count": 2,
            "removed": [{"kind": "column", "name": "Sales.Region"}, {"kind": "metric", "name": "Old"}]}]
DUPLICATE = [{"model": "Sales", "view_name": "SALES", "other_project_id": OTHER, "other_project_name": "Finance",
              "other_view_name": "SALES", "target": "DB.SCH", "reasons": ["same_view"], "overwrites": True}]


@pytest.fixture()
def db():
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile  # noqa: F401
    from semabridge.repository.orm.models import Project, Run, RunLease  # noqa: F401
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.snapshot_models import SnapshotRow  # noqa: F401

    Base.metadata.create_all(db_manager.get_engine())
    with db_manager.get_session() as s:
        for pid in (PID, OTHER):
            if s.get(Project, pid) is None:
                s.add(Project(project_id=pid, name=pid))
        s.commit()
    yield db_manager
    with db_manager.get_session() as s:
        s.query(SnapshotRow).filter(SnapshotRow.project_id.in_([PID, OTHER])).delete()
        s.query(Run).filter(Run.project_id.in_([PID, OTHER])).delete()
        s.query(RunLease).filter(RunLease.project_id.in_([PID, OTHER])).delete()
        for job in s.query(DryRunJob).filter(DryRunJob.project_id.in_([PID, OTHER])).all():
            s.delete(job)
        s.commit()


def _checks(monkeypatch, *, duplicates=None, removed=None, never_deployed=None, dup_error=None):
    calls = {"removed": 0, "dup": 0}

    def find_dups(*a):
        calls["dup"] += 1
        if dup_error:
            raise dss.SyncCheckError(dup_error)
        return list(duplicates or [])

    def find_removed(*a):
        calls["removed"] += 1
        return list(removed or [])

    monkeypatch.setattr(dss, "find_duplicate_deployments", find_dups)
    monkeypatch.setattr(dss, "find_removed_fields", find_removed)
    monkeypatch.setattr(dss, "models_never_deployed", lambda *a: list(never_deployed or []))
    return calls


# --- non-interactive: schedules and background triggers ---------------------------

def _scheduled(monkeypatch, sync_mode="copy"):
    saved = []

    async def ready(*a, **k):
        return "preview-x"

    async def must_not_deploy(*a, **k):
        raise AssertionError("a refused scheduled run must not deploy")

    monkeypatch.setattr(rs, "_persist_run", lambda run: saved.append(dict(run)))
    monkeypatch.setattr(rs, "_assert_sync_ready", ready)
    monkeypatch.setattr(rs, "_perform_project_run", must_not_deploy)
    run = {"run_id": f"run-{uuid.uuid4()}", "project_id": PID, "status": "queued", "logs": [],
           "sync_mode": sync_mode}
    run["id"] = run["run_id"]
    return run, saved


def test_scheduled_copy_with_removed_fields_is_failed_not_deployed(db, monkeypatch):
    _checks(monkeypatch, removed=REMOVED)
    run, saved = _scheduled(monkeypatch)
    result = asyncio.run(rs._gated_project_run(run, CONFIG, time.time()))
    assert result["status"] == "failed"
    assert result["message"].startswith("Sync not started: 2 field(s) would be removed by a Copy sync")
    assert "switch to Upsert" in result["message"]
    assert saved[-1]["status"] == "failed"
    # The lease is released: the next run can start.
    assert rc.lease_holder(rc.project_lease_key(PID)) is None


def test_scheduled_run_of_a_duplicate_is_failed_with_the_other_project(db, monkeypatch):
    _checks(monkeypatch, duplicates=DUPLICATE)
    run, _saved = _scheduled(monkeypatch)
    result = asyncio.run(rs._gated_project_run(run, CONFIG, time.time()))
    assert result["status"] == "failed"
    assert "Sales already synced by project Finance" in result["message"]


def test_scheduled_run_fails_closed_when_the_duplicate_check_cannot_run(db, monkeypatch):
    _checks(monkeypatch, dup_error="db down")
    run, _saved = _scheduled(monkeypatch)
    result = asyncio.run(rs._gated_project_run(run, CONFIG, time.time()))
    assert result["status"] == "failed" and "duplicate check could not run" in result["message"]


def test_scheduled_upsert_never_asks_about_removed_fields(db, monkeypatch):
    calls = _checks(monkeypatch, removed=REMOVED)
    run, _saved = _scheduled(monkeypatch, sync_mode="upsert")
    deployed = []

    async def perform(r, cfg, started):
        deployed.append(r["sync_mode"])
        r["status"] = "success"
        return r

    monkeypatch.setattr(rs, "_perform_project_run", perform)
    result = asyncio.run(rs._gated_project_run(run, CONFIG, time.time()))
    assert result["status"] == "success" and deployed == ["upsert"]
    assert calls["removed"] == 0


# --- interactive: run-now ---------------------------------------------------------

class _Started(Exception):
    pass


def _interactive(monkeypatch):
    async def ready(*a, **k):
        return "preview-x"

    def create_run(*a, **k):
        raise _Started(k.get("sync_mode"))

    monkeypatch.setattr(rs, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(rs, "_prepare_sync_config", lambda pid: CONFIG)
    monkeypatch.setattr(rs, "_assert_sync_ready", ready)
    monkeypatch.setattr(rs, "_create_project_run", create_run)
    import sys
    pri = sys.modules.get("semabridge.api.services.project_runs_impl")
    if pri is not None and hasattr(pri, "_create_project_run"):
        monkeypatch.setattr(pri, "_create_project_run", create_run)


def test_duplicate_check_failure_is_a_409_the_user_can_acknowledge(db, monkeypatch):
    _interactive(monkeypatch)
    _checks(monkeypatch, dup_error="db down")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rs.run_project_now_compat(PID, object(), {"sync_mode": "upsert"}))
    assert exc.value.status_code == 409
    assert exc.value.detail["status"] == "duplicate_check_failed"
    with pytest.raises(_Started):
        asyncio.run(rs.run_project_now_compat(PID, object(), {"sync_mode": "upsert", "acknowledge_duplicates": True}))


def test_upsert_of_a_never_deployed_model_is_promoted_before_the_removed_fields_check(db, monkeypatch):
    _interactive(monkeypatch)
    calls = _checks(monkeypatch, removed=REMOVED, never_deployed=["Sales"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rs.run_project_now_compat(PID, object(), {"sync_mode": "upsert"}))
    assert exc.value.detail["status"] == "removed_fields"
    assert exc.value.detail["promoted_from_upsert"] is True
    assert calls["removed"] == 1
    # Confirmed: the run is created as COPY (the promotion is not silent).
    with pytest.raises(_Started) as started:
        asyncio.run(rs.run_project_now_compat(
            PID, object(), {"sync_mode": "upsert", "acknowledge_removed_fields": True},
        ))
    assert started.value.args[0] == "copy"


def test_upsert_of_deployed_models_is_not_promoted_even_without_run_history(db, monkeypatch):
    _interactive(monkeypatch)
    calls = _checks(monkeypatch, removed=REMOVED, never_deployed=[])
    rs._compat_project_runs.pop(PID, None)  # no in-memory history at all
    with pytest.raises(_Started) as started:
        asyncio.run(rs.run_project_now_compat(PID, object(), {"sync_mode": "upsert"}))
    assert started.value.args[0] == "upsert"
    assert calls["removed"] == 0


def test_create_project_run_promotion_uses_deployed_snapshots(db, monkeypatch):
    monkeypatch.setattr(dss, "models_never_deployed", lambda *a: [])
    assert rs._promote_initial_upsert(PID, CONFIG, "upsert") == ("upsert", [])
    monkeypatch.setattr(dss, "models_never_deployed", lambda *a: ["Sales"])
    assert rs._promote_initial_upsert(PID, CONFIG, "upsert") == ("copy", ["Sales"])
    assert rs._promote_initial_upsert(PID, CONFIG, "copy") == ("copy", [])


def test_unverifiable_models_are_refused_with_their_names(db, monkeypatch):
    _interactive(monkeypatch)
    unverifiable = [{"model": "Finance", "view_name": "FINANCE", "count": 0, "removed": [], "unverifiable": True}]
    _checks(monkeypatch, removed=unverifiable)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rs.run_project_now_compat(PID, object(), {"sync_mode": "copy"}))
    detail = exc.value.detail
    assert detail["status"] == "removed_fields"
    assert [m["model"] for m in detail["unverifiable"]] == ["Finance"] and detail["models"] == []
    assert "Re-run the dry run" in detail["message"]


# --- removed-fields check: the gate's dry run, per model ---------------------------

def _snapshot(s, project_id, view_name, *, deployed):
    from semabridge.repository.orm.snapshot_models import SnapshotRow

    s.add(SnapshotRow(
        snapshot_id=str(uuid.uuid4()), project_id=project_id, timestamp=datetime.now(timezone.utc),
        view_name=view_name, target_database="DB", target_schema="SCH",
        deployed_at=datetime.now(timezone.utc) if deployed else None,
    ))


def test_removed_fields_uses_the_accepted_dry_run_and_flags_models_without_one(db, monkeypatch):
    planned = PLANNED + [{"model_label": "Fin", "model_key": "FIN", "view_name": "FIN", "content_hash": "h-fin"}]
    with db.get_session() as s:
        _snapshot(s, PID, "SALES", deployed=True)
        _snapshot(s, PID, "FIN", deployed=True)
        _snapshot(s, "preview-accepted", "SALES", deployed=False)
        _snapshot(s, "preview-unrelated", "SALES", deployed=False)  # another project's preview
        s.commit()
    seen_ids = set()

    def blob_of(ids, rows, **kw):
        seen_ids.update(str(r.project_id) for r in rows)
        if kw.get("deployed_only"):
            return {"metrics": [{"unique_name": "Total"}, {"unique_name": "Old"}]}
        return {"metrics": [{"unique_name": "Total"}]} if ids == ["preview-accepted"] else None

    monkeypatch.setattr(dss, "_planned_models", lambda cfg: planned)
    monkeypatch.setattr(dss, "accepted_previews", lambda pid, cfg: {"Sales": "preview-accepted", "Fin": None})
    monkeypatch.setattr(dss, "_latest_blob_of", blob_of)
    found = dss.find_removed_fields(PID, CONFIG)
    by_model = {m["model"]: m for m in found}
    assert by_model["Sales"]["removed"] == [{"kind": "metric", "name": "Old"}]
    assert by_model["Fin"]["unverifiable"] is True and by_model["Fin"]["count"] == 0
    # Only this project's rows and the accepted preview's are loaded.
    assert seen_ids == {PID, "preview-accepted"}
    with db.get_session() as s:
        from semabridge.repository.orm.snapshot_models import SnapshotRow

        s.query(SnapshotRow).filter(SnapshotRow.project_id.in_(["preview-accepted", "preview-unrelated"])).delete()
        s.commit()


def test_forced_single_model_sync_without_a_matching_dry_run_is_unverifiable(db, monkeypatch):
    # force=True skips the gate (no dry_run_project_id): the stale newest
    # blob is no longer used, the model cannot be verified.
    with db.get_session() as s:
        _snapshot(s, PID, "SALES", deployed=True)
        s.commit()
    monkeypatch.setattr(dss, "_planned_models", lambda cfg: PLANNED)
    monkeypatch.setattr(dss, "accepted_previews", lambda pid, cfg: {"Sales": None})
    found = dss.find_removed_fields(PID, CONFIG, None)
    assert found and found[0]["unverifiable"] is True


def test_accepted_dry_run_previews_follow_the_gate_content_hash(db, monkeypatch, tmp_path):
    from semabridge.api.services import dry_run_job_service as djs
    from semabridge.api.services import sync_execution_service as ses
    from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile

    jobs = [{"model_label": "Sales", "source_ref": str(tmp_path / "Sales.pbix"), "pbix_path": str(tmp_path / "Sales.pbix")}]
    monkeypatch.setattr(djs, "_gate_jobs", lambda cfg: (jobs, "snowflake"))
    monkeypatch.setattr(ses, "file_content_sha256", lambda path: "h-now")
    now = datetime.now(timezone.utc)
    with db.get_session() as s:
        for i, (content_hash, preview) in enumerate([("h-now", "preview-good"), ("h-old", "preview-stale")]):
            job_id = str(uuid.uuid4())
            s.add(DryRunJob(job_id=job_id, project_id=PID, status="success", created_at=now.replace(microsecond=i),
                            source_config_json="{}", target_config_json="{}"))
            s.add(DryRunJobFile(file_id=str(uuid.uuid4()), job_id=job_id, file_index=0,
                                pbix_path=jobs[0]["pbix_path"], display_name="Sales", label="Sales",
                                source_ref=jobs[0]["source_ref"], status="success",
                                content_hash=content_hash, preview_project_id=preview))
        s.commit()
    # The newest job read another file (h-old); the gate's choice is h-now.
    assert djs.accepted_dry_run_previews(PID, {}) == {"Sales": "preview-good"}
    monkeypatch.setattr(ses, "file_content_sha256", lambda path: "h-other")
    assert djs.accepted_dry_run_previews(PID, {}) == {"Sales": None}


# --- duplicate check ------------------------------------------------------------------

def test_duplicate_check_fails_closed_when_strict(db, monkeypatch):
    monkeypatch.setattr(dss, "_planned_models", lambda cfg: PLANNED)

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(dss, "_file_hashes_by_project", boom)
    with pytest.raises(dss.SyncCheckError):
        dss.find_duplicate_deployments(PID, CONFIG, True)
    assert dss.find_duplicate_deployments(PID, CONFIG) == []


def _other_dry_run(db, content_hash="h-sales", database="DB", schema="SCH"):
    from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile

    job_id = str(uuid.uuid4())
    with db.get_session() as s:
        s.add(DryRunJob(job_id=job_id, project_id=OTHER, status="success", created_at=datetime.now(timezone.utc),
                        source_config_json="{}",
                        target_config_json=json.dumps({"database": database, "schema": schema})))
        s.add(DryRunJobFile(file_id=str(uuid.uuid4()), job_id=job_id, file_index=0, pbix_path="x.pbix",
                            display_name="Sales", status="success", content_hash=content_hash,
                            preview_project_id="preview-o"))
        s.commit()


def test_same_file_not_yet_deployed_by_another_project_is_a_duplicate(db, monkeypatch):
    from semabridge.api.services import project_shared

    monkeypatch.setattr(dss, "_planned_models", lambda cfg: PLANNED)
    monkeypatch.setattr(dss, "accepted_previews", lambda pid, cfg: {})
    monkeypatch.setitem(project_shared._compat_projects, OTHER, {"name": "Finance"})
    _other_dry_run(db)

    found = dss.find_duplicate_deployments(PID, CONFIG, True)
    assert [(f["other_project_id"], f["reasons"]) for f in found] == [(OTHER, ["same_file_dry_run"])]

    # The other project is syncing it right now.
    rc.acquire_lease(rc.project_lease_key(OTHER), run_id="run-other", lease_type="project", project_id=OTHER)
    try:
        found = dss.find_duplicate_deployments(PID, CONFIG, True)
    finally:
        rc.release_leases(run_id="run-other")
    assert found[0]["reasons"] == ["same_file_in_progress"]
    assert found[0]["other_project_name"] == "Finance"


def test_same_file_for_another_target_is_not_a_duplicate(db, monkeypatch):
    from semabridge.api.services import project_shared

    monkeypatch.setattr(dss, "_planned_models", lambda cfg: PLANNED)
    monkeypatch.setattr(dss, "accepted_previews", lambda pid, cfg: {})
    monkeypatch.setitem(project_shared._compat_projects, OTHER, {"name": "Finance"})
    _other_dry_run(db, schema="ELSEWHERE")
    assert dss.find_duplicate_deployments(PID, CONFIG, True) == []
