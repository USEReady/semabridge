"""Run execution reliability: single run identity, DB-backed leases, global
model capacity, partial status + retry, transparent demo masking."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi import HTTPException

from semabridge.api.services import run_service as rs
from semabridge.core import run_coordination as rc
from semabridge.core.run_helpers import mask_project_for_display, mask_run_for_display, resolve_run_status


@pytest.fixture()
def db():
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import Project, Run, RunLease  # noqa: F401
    from semabridge.repository.orm.session_factory import db_manager

    Base.metadata.create_all(db_manager.get_engine())
    with db_manager.get_session() as s:
        if s.get(Project, "proj-rel") is None:
            s.add(Project(project_id="proj-rel", name="rel"))
        s.commit()
    yield db_manager
    with db_manager.get_session() as s:
        s.query(Run).filter(Run.project_id == "proj-rel").delete()
        s.query(RunLease).filter(RunLease.project_id == "proj-rel").delete()
        s.commit()


# --- leases -------------------------------------------------------------------

def test_project_lease_is_exclusive_reentrant_and_released(db):
    key = rc.project_lease_key("proj-rel")
    assert rc.acquire_lease(key, run_id="run-a", lease_type="project", project_id="proj-rel")
    assert rc.acquire_lease(key, run_id="run-a", lease_type="project", project_id="proj-rel")  # same holder
    busy = rc.acquire_lease(key, run_id="run-b", lease_type="project", project_id="proj-rel")
    assert not busy and busy.run_id == "run-a"
    rc.release_leases(run_id="run-a")
    assert rc.acquire_lease(key, run_id="run-b", lease_type="project", project_id="proj-rel")
    rc.release_leases(run_id="run-b")


def test_expired_lease_of_a_dead_holder_can_be_taken_over(db, monkeypatch):
    from datetime import timedelta

    from semabridge.repository.orm.models import RunLease

    key = rc.target_lease_key("DB", "SCH", "VIEW_X")
    assert rc.acquire_lease(key, run_id="dead", project_id="proj-rel")
    with db.get_session() as s:
        row = s.get(RunLease, key)
        row.expires_at = rc.utcnow() - timedelta(seconds=1)
        s.commit()
    assert rc.acquire_lease(key, run_id="alive", project_id="proj-rel")
    rc.release_leases(run_id="alive")


def test_heartbeat_renews_the_leases_of_live_runs(db):
    from semabridge.repository.orm.models import RunLease

    key = rc.target_lease_key("DB", "SCH", "VIEW_HB")
    rc.acquire_lease(key, run_id="run-hb", project_id="proj-rel")
    with db.get_session() as s:
        before = s.get(RunLease, key).expires_at
    time.sleep(0.01)
    rc.heartbeat_once(["run-hb"])
    with db.get_session() as s:
        assert s.get(RunLease, key).expires_at > before
    rc.release_leases(run_id="run-hb")


def test_emitter_object_leases_conflict_across_holders(db):
    with rc.deploy_holder("run-1", "run-1:m1", "proj-rel"):
        keys = rc.acquire_target_objects("DB", "SCH", ["SV_A", "NS_FACT_ENRICHED"])
    assert keys == ["target:DB.SCH.NS_FACT_ENRICHED", "target:DB.SCH.SV_A"]
    with rc.deploy_holder("run-2", "run-2:m1", "proj-rel"):
        with pytest.raises(rc.LeaseBusyError) as err:
            rc.acquire_target_objects("db", "sch", ["ns_fact_enriched"])
    assert "CONFLICT" in str(err.value)
    rc.release_leases(holder="run-1:m1")


# --- entry points ---------------------------------------------------------------

def test_run_now_returns_409_while_the_project_is_already_syncing(db, monkeypatch):
    lease = rc.acquire_lease(rc.project_lease_key("proj-rel"), run_id="run-busy", lease_type="project",
                             project_id="proj-rel")
    assert lease
    monkeypatch.setattr(rs, "_compat_ensure_loaded", lambda: None)
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(rs.run_project_now_compat("proj-rel", object(), {"sync_mode": "copy"}))
        assert exc.value.status_code == 409
        assert exc.value.detail["status"] == "already_running"
        assert "already running" in exc.value.detail["message"]
        assert exc.value.detail["run_id"] == "run-busy"
    finally:
        rc.release_leases(run_id="run-busy")


def test_scheduled_run_of_a_busy_project_is_recorded_as_skipped(db, monkeypatch):
    saved = []
    monkeypatch.setattr(rs, "_persist_run", lambda run: saved.append(dict(run)))
    monkeypatch.setattr(rs, "_assert_sync_ready", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no gate")))
    rc.acquire_lease(rc.project_lease_key("proj-rel"), run_id="run-busy", lease_type="project", project_id="proj-rel")
    try:
        run = {"run_id": "run-sched", "id": "run-sched", "project_id": "proj-rel", "status": "queued", "logs": []}
        result = asyncio.run(rs._gated_project_run(run, "", time.time()))
    finally:
        rc.release_leases(run_id="run-busy")
    assert result["status"] == "skipped"
    assert "already running" in result["message"]
    assert saved and saved[-1]["status"] == "skipped"


def test_project_run_row_is_authoritative_for_listing(db, monkeypatch):
    from semabridge.api.services.run_records import list_project_runs, save_project_run

    run = {"run_id": "6d2c-uuid-run", "id": "6d2c-uuid-run", "project_id": "proj-rel", "status": "queued",
           "started_at": "2026-11-17T08:00:00Z", "schedule": "Manual", "results": [{"big": "blob"}]}
    assert save_project_run(run)
    run.update(status="partial", message="Completed with failures: 1 of 2 model(s) failed (B).",
               completed_at="2026-11-17T08:05:00Z")
    save_project_run(run)
    listed = list_project_runs("proj-rel")
    assert [r["run_id"] for r in listed] == ["6d2c-uuid-run"]
    assert listed[0]["status"] == "partial" and listed[0]["run_kind"] == "project"
    assert "results" not in listed[0]  # blobs stay out of the row


# --- capacity -------------------------------------------------------------------

def test_global_model_slots_cap_concurrent_model_executions(monkeypatch):
    monkeypatch.setattr(rc, "_model_slots", threading.BoundedSemaphore(2))
    active, peak = [0], [0]
    lock = threading.Lock()

    def work():
        with rc.model_slot():
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            with rc.model_slot():  # nested use in the same thread never waits
                time.sleep(0.03)
            with lock:
                active[0] -= 1

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert peak[0] == 2


def test_first_model_slot_flips_a_queued_run_to_running(monkeypatch):
    monkeypatch.setattr(rs, "save_project_run", lambda run: True)
    run = {"run_id": "q1", "status": "queued"}
    token = rc.model_started_callback.set(lambda: rs._mark_run_started(run))
    try:
        with rc.model_slot():
            pass
    finally:
        rc.model_started_callback.reset(token)
    assert run["status"] == "running"


# --- failure semantics ------------------------------------------------------------

def test_partial_is_its_own_status():
    assert resolve_run_status({"status": "partial"}) == "partial"
    assert resolve_run_status({"status": "success"}) == "success"
    assert resolve_run_status({"status": "conflict"}) == "failed"


def test_retry_targets_only_the_failed_models(db, monkeypatch):
    captured = {}

    async def fake_start(project_id, background_tasks, payload, **kwargs):
        captured.update(kwargs, project_id=project_id)
        return {"run_id": "new-run", "status": "queued"}

    monkeypatch.setattr(rs, "_start_project_run_request", fake_start)
    monkeypatch.setattr(rs, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setitem(rs._compat_project_runs, "proj-rel", [{
        "run_id": "old-run", "id": "old-run", "project_id": "proj-rel", "status": "partial",
        "results": [{"model": "A", "status": "success"}, {"model": "B", "status": "failed"},
                    {"model": "C", "status": "conflict"}],
    }])
    result = asyncio.run(rs.retry_failed_models_compat("proj-rel", "old-run", object(), {}))
    assert result["models"] == ["B", "C"]
    assert captured["scope_models"] == ["B", "C"] and captured["retry_of_run_id"] == "old-run"


def test_scope_config_keeps_the_project_and_lists_the_models():
    import yaml

    cfg = rs._scope_config_to_models("project_id: p\nsource:\n  type: pbix\n", ["B"])
    assert yaml.safe_load(cfg)["sync_scope_models"] == ["B"]


# --- demo masking -------------------------------------------------------------------

def test_demo_masking_keeps_the_real_outcome_visible():
    run = {"status": "partial", "demo_mode": True, "message": "Completed with failures",
           "results": [{"model": "B", "status": "failed"}]}
    shown = mask_run_for_display(run)
    assert shown["status"] == "success" and shown["demo_masked"] is True
    assert shown["true_status"] == "partial" and shown["true_message"] == "Completed with failures"
    assert shown["results"][0]["true_status"] == "failed"
    assert run["status"] == "partial"  # stored record untouched


def test_projects_grid_masks_only_at_display_time():
    project = {"status": "failed", "last_run_demo_mode": True}
    shown = mask_project_for_display(project)
    assert shown["status"] == "active" and shown["true_status"] == "failed" and shown["demo_masked"]
    assert project["status"] == "failed"
    assert mask_project_for_display({"status": "failed"})["status"] == "failed"


# --- persistence leftovers -------------------------------------------------------------

def test_store_timestamps_are_timezone_aware():
    from datetime import datetime

    from semabridge.api.services.project_shared import _compat_now_iso

    value = _compat_now_iso()
    assert value.endswith("Z")
    assert datetime.fromisoformat(value).tzinfo is not None


def test_scheduler_leadership_without_postgres_is_single_process_leader():
    assert rc.SchedulerLeadership().is_leader() is True
