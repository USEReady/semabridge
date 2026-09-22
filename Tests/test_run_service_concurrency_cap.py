"""Regression tests for the single shared concurrency cap on project syncs
in run_service.py (_RUN_SEMAPHORE / _MAX_CONCURRENT_RUNS).

Context: scheduler_service.py used to hold its own separate
asyncio.Semaphore that only capped APScheduler-triggered runs.
schedule_service.py's trigger_job_compat ("Run Now" on the Jobs page, and
its old "fall back to every project" bug) went through a completely
different dispatch path with no cap at all -- a single click there
produced 310+ concurrent project runs (see
incident_run_now_fires_every_project in project memory). The fix
consolidates to one threading.Semaphore in run_service.py, acquired inside
_perform_project_run around the actual sync_models() call -- the one
function every dispatch path (scheduler, Jobs page trigger-all, a single
project's own Run Now/deploy) converges on. These tests exercise real
threads (mirroring how _run_project_background/scheduler_service.py's
_run_callback_in_thread actually invoke _perform_project_run), since a
threading.Semaphore's blocking acquire would deadlock a single-threaded
asyncio.gather-based test.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from semabridge.api.services import run_service as rs
from semabridge.api.services import core_domain_service


def _make_tracking_sync_models(max_observed: list[int], completed: list[str]):
    lock = threading.Lock()
    state = {"current": 0}

    async def _fake_sync_models(payload: dict) -> dict:
        project_id = payload.get("project_id")
        with lock:
            state["current"] += 1
            max_observed[0] = max(max_observed[0], state["current"])
        try:
            # Force a real overlap window so a broken cap would be caught.
            await asyncio.sleep(0.05)
        finally:
            with lock:
                state["current"] -= 1
        with lock:
            completed.append(project_id)
        return {"status": "success", "summary": {}, "results": []}

    return _fake_sync_models


def _run_perform_project_run_in_thread(project_id: str) -> threading.Thread:
    run = {"project_id": project_id, "id": f"run-{project_id}", "run_id": f"run-{project_id}"}

    def _run_loop() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(rs._perform_project_run(run, "project_name: test\n", 0.0))
        finally:
            loop.close()

    thread = threading.Thread(target=_run_loop)
    thread.start()
    return thread


@pytest.fixture(autouse=True)
def _isolate_side_effects(monkeypatch):
    # None of these test project_ids are registered in _compat_projects, so
    # the status-propagation/email blocks inside _perform_project_run
    # already no-op on their own -- only _compat_save_store() (unconditional
    # in the finally block) needs silencing to avoid repeatedly rewriting
    # the real on-disk compat store from concurrent test threads.
    monkeypatch.setattr(rs, "_compat_save_store", lambda: None)


def test_concurrent_syncs_never_exceed_the_cap(monkeypatch):
    monkeypatch.setattr(rs, "_MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setattr(rs, "_RUN_SEMAPHORE", threading.Semaphore(2))

    max_observed = [0]
    completed: list[str] = []
    monkeypatch.setattr(core_domain_service, "sync_models", _make_tracking_sync_models(max_observed, completed))

    project_ids = [f"proj-{i}" for i in range(5)]
    threads = [_run_perform_project_run_in_thread(pid) for pid in project_ids]
    for t in threads:
        t.join(timeout=5)

    assert max_observed[0] <= 2, "cap of 2 was exceeded -- syncs ran fully concurrently"
    assert sorted(completed) == sorted(project_ids), "every sync must eventually complete, none dropped by the cap"


def test_a_run_queued_behind_the_cap_still_eventually_fires(monkeypatch):
    monkeypatch.setattr(rs, "_MAX_CONCURRENT_RUNS", 1)
    monkeypatch.setattr(rs, "_RUN_SEMAPHORE", threading.Semaphore(1))

    max_observed = [0]
    completed: list[str] = []
    monkeypatch.setattr(core_domain_service, "sync_models", _make_tracking_sync_models(max_observed, completed))

    project_ids = [f"proj-{i}" for i in range(4)]
    threads = [_run_perform_project_run_in_thread(pid) for pid in project_ids]
    for t in threads:
        t.join(timeout=5)

    assert max_observed[0] == 1, "cap of 1 should fully serialize syncs"
    assert sorted(completed) == sorted(project_ids)


def test_default_cap_is_two_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_MAX_CONCURRENT_RUNS", raising=False)
    import importlib
    reloaded = importlib.reload(rs)
    try:
        assert reloaded._MAX_CONCURRENT_RUNS == 2
    finally:
        importlib.reload(rs)


def test_env_var_controls_the_cap(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_MAX_CONCURRENT_RUNS", "5")
    import importlib
    reloaded = importlib.reload(rs)
    try:
        assert reloaded._MAX_CONCURRENT_RUNS == 5
    finally:
        monkeypatch.delenv("SEMABRIDGE_MAX_CONCURRENT_RUNS", raising=False)
        importlib.reload(rs)
