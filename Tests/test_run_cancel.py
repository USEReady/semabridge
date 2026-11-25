"""Cancelling a run: the request is durable, the engine stops at the next step
boundary, the run ends as "cancelled", and a queued job never starts."""
import pytest

from semabridge.core import run_cancel


@pytest.fixture(autouse=True)
def _clean():
    from semabridge.repository import state_store

    state_store.ensure_table()
    yield
    for rid in ("r-1", "r-2", "r-3", "r-q"):
        state_store.apply_changes([("run_cancel", rid, None)])
        run_cancel.forget(rid)


def test_check_raises_only_inside_the_cancelled_runs_scope():
    run_cancel.request_cancel("r-1")
    run_cancel.check()  # no run in scope
    with run_cancel.scope("r-2"):
        run_cancel.check()  # another run
    with run_cancel.scope("r-1"):
        with pytest.raises(run_cancel.RunCancelled):
            run_cancel.check()


def test_request_is_visible_to_another_process():
    run_cancel.request_cancel("r-3")
    run_cancel.forget("r-3")  # as if this process never saw the request
    assert run_cancel.is_cancel_requested("r-3")


def test_engine_stops_at_the_next_step_boundary():
    from types import SimpleNamespace

    from semabridge.core.engine.engine import ExecutionEngine
    from semabridge.core.run_summary import StepStatus

    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._summary = None
    engine._current_step = 0
    recorded = []
    engine._record_step_inner = lambda n, st, msg=None, art=None: recorded.append(n)
    run_cancel.request_cancel("r-1")
    with run_cancel.scope("r-1"):
        with pytest.raises(run_cancel.RunCancelled):
            engine._record_step(3, StepStatus.SUCCESS, "Converted")
        engine._record_step(10, StepStatus.SUCCESS, "Finalized")  # finalize is never interrupted
        engine._record_step(4, StepStatus.FAILED, "x")  # a failure is recorded, not re-raised
    assert recorded == [3, 10, 4]


def test_cancel_endpoint_logic(monkeypatch):
    from semabridge.api.services import run_service as rs

    monkeypatch.setattr(rs, "list_project_runs", lambda pid: [
        {"run_id": "r-1", "status": "running"}, {"run_id": "r-2", "status": "success"}])
    out = rs.cancel_project_run("p1", "r-1", user_id="7")
    assert out["status"] == "cancelling"
    assert run_cancel.is_cancel_requested("r-1")
    assert rs.cancel_project_run("p1", "r-2")["status"] == "success"
    with pytest.raises(Exception):
        rs.cancel_project_run("p1", "missing")


def test_queued_job_is_cancelled_before_it_starts(monkeypatch):
    from semabridge.api.services import run_service as rs
    from semabridge.core import job_queue

    monkeypatch.setenv("SEMABRIDGE_EXECUTION_MODE", "queue")
    job_queue.ensure_table()
    job_queue.enqueue("project_run", {"run": {"run_id": "r-q"}}, job_id="r-q")
    monkeypatch.setattr(rs, "list_project_runs", lambda pid: [{"run_id": "r-q", "status": "queued"}])
    saved = []
    monkeypatch.setattr(rs, "save_project_run", lambda run: saved.append(run["status"]))
    assert rs.cancel_project_run("p1", "r-q")["status"] == "cancelled"
    assert saved == ["cancelled"]
    assert job_queue.claim_next("w") is None


def test_run_marked_cancelled_when_requested(monkeypatch):
    from semabridge.api.services import run_service as rs

    run_cancel.request_cancel("r-1")
    assert rs._cancel_requested({"run_id": "r-1"}) is True
    assert rs._cancel_requested({"run_id": "r-2"}) is False
