"""Durable job queue: atomic claims, crash re-queue, attempts limit,
cancellation, and project runs routed through the queue."""
from datetime import timedelta

import pytest

from semabridge.core import job_queue as jq


@pytest.fixture
def clean_jobs():
    from semabridge.repository.orm.job_models import RunJob
    from semabridge.repository.orm.session_factory import db_manager

    jq.ensure_table()
    with db_manager.get_session() as s:
        s.query(RunJob).delete()
        s.commit()
    yield
    with db_manager.get_session() as s:
        s.query(RunJob).delete()
        s.commit()


def test_execution_mode_defaults(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("SEMABRIDGE_ENV", raising=False)
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "local")
    assert jq.execution_mode() == "inline"
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "azure")
    assert jq.execution_mode() == "queue"
    assert jq.embedded_worker_count() == 0
    monkeypatch.setenv("SEMABRIDGE_EXECUTION_MODE", "inline")
    assert jq.execution_mode() == "inline"


def test_a_job_is_claimed_once(clean_jobs):
    jid = jq.enqueue("noop", {"x": 1}, project_id="p1")
    first = jq.claim_next("w1")
    assert first.job_id == jid and first.payload == {"x": 1} and first.attempts == 1
    assert jq.claim_next("w2") is None


def test_oldest_first(clean_jobs):
    a = jq.enqueue("noop", {}, job_id="a")
    jq.enqueue("noop", {}, job_id="b")
    assert jq.claim_next("w").job_id == a


def test_handler_runs_and_job_is_done(clean_jobs):
    seen = []
    jq.register_handler("record", lambda payload, ctx: seen.append((payload["v"], ctx.is_retry)))
    jid = jq.enqueue("record", {"v": 7})
    assert jq.run_one("w1") == "done"
    assert seen == [(7, False)]
    assert jq.job_status(jid)["status"] == "done"


def test_handler_failure_is_recorded(clean_jobs):
    jq.register_handler("boom", lambda p, c: (_ for _ in ()).throw(ValueError("bad config")))
    jid = jq.enqueue("boom", {})
    assert jq.run_one("w1") == "failed"
    assert "ValueError: bad config" in jq.job_status(jid)["last_error"]


def test_crashed_worker_job_is_requeued_then_failed_after_max_attempts(clean_jobs):
    jid = jq.enqueue("noop", {}, max_attempts=2)
    jq.claim_next("dead-worker")
    later = jq._now() + timedelta(hours=1)
    assert jq.requeue_stale(now=later) == {"requeued": 1, "failed": 0}
    assert jq.job_status(jid)["status"] == "queued"

    second = jq.claim_next("w2")
    assert second.attempts == 2
    assert jq.requeue_stale(now=later + timedelta(hours=1)) == {"requeued": 0, "failed": 1}
    assert jq.job_status(jid)["status"] == "failed"


def test_live_worker_is_not_requeued(clean_jobs):
    jq.enqueue("noop", {})
    jq.claim_next("w1")
    assert jq.requeue_stale() == {"requeued": 0, "failed": 0}


def test_cancel_queued_and_running(clean_jobs):
    queued = jq.enqueue("noop", {}, job_id="q")
    assert jq.request_cancel(queued)
    assert jq.job_status("q")["status"] == "cancelled"
    assert jq.claim_next("w") is None

    jq.register_handler("cooperative", lambda p, ctx: (jq.request_cancel(ctx.job.job_id), ctx.raise_if_cancelled()))
    jq.enqueue("cooperative", {}, job_id="r")
    assert jq.run_one("w") == "cancelled"
    assert jq.request_cancel("r") is False


def test_unknown_kind_fails_cleanly(clean_jobs):
    jq.enqueue("does-not-exist", {}, job_id="u")
    assert jq.run_one("w") == "failed"
    assert "No handler" in jq.job_status("u")["last_error"]


def test_project_run_is_enqueued_in_queue_mode(clean_jobs, monkeypatch):
    from semabridge.api.services import run_service as rs

    monkeypatch.setenv("SEMABRIDGE_EXECUTION_MODE", "queue")
    monkeypatch.setattr(rs, "_persist_run", lambda run: None)
    executed = []

    async def fake_perform(run, cfg, started):
        executed.append((run["run_id"], cfg))
        return run

    monkeypatch.setattr(rs, "_perform_project_run", fake_perform)
    monkeypatch.setattr(rs, "_acquire_project_lease", lambda pid, rid: True)
    run = {"run_id": "run-q1", "id": "run-q1", "project_id": "p-q", "status": "queued", "logs": []}
    rs._run_project_background(run, "cfg-yaml", 1700000000.0)

    assert jq.job_status("run-q1")["status"] == "queued"
    assert executed == []
    assert jq.run_one("w") == "done"
    assert executed == [("run-q1", "cfg-yaml")]


def test_resumed_run_that_lost_its_lease_is_skipped(clean_jobs, monkeypatch):
    from semabridge.api.services import run_service as rs

    monkeypatch.setattr(rs, "_persist_run", lambda run: None)
    skipped = []
    monkeypatch.setattr(rs, "_acquire_project_lease", lambda pid, rid: False)
    monkeypatch.setattr(rs, "_mark_run_skipped", lambda run, started, lease: skipped.append(run["run_id"]))
    jq.enqueue(rs.JOB_PROJECT_RUN, {"run": {"run_id": "r2", "project_id": "p"}, "project_cfg": "", "started": 1.0}, job_id="r2")
    assert jq.run_one("w") == "done"
    assert skipped == ["r2"]
