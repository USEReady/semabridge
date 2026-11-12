"""The stale-run sweeper must not fail a run that is still executing.

Real case: a Snowflake sync whose post-deploy checks took ~14 minutes was
shown as "failed: Run timed out or was interrupted" after 5 minutes, then
flipped to success when it finished.
"""
from datetime import datetime, timedelta, timezone

from semabridge.api.services import run_service as rs


def _run(run_id, minutes_ago):
    started = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {"run_id": run_id, "id": run_id, "status": "running", "started_at": started}


def test_sweeper_skips_active_runs_and_fails_abandoned_ones(monkeypatch):
    live, dead = _run("run-live", 20), _run("run-dead", 20)
    monkeypatch.setitem(rs._compat_project_runs, "proj-sweep-test", [live, dead])
    monkeypatch.setattr(rs, "_compat_save_store", lambda: None)
    with rs._ACTIVE_RUN_LOCK:
        rs._ACTIVE_RUN_IDS.add("run-live")
    try:
        rs._sweep_stale_runs()
    finally:
        with rs._ACTIVE_RUN_LOCK:
            rs._ACTIVE_RUN_IDS.discard("run-live")
    assert live["status"] == "running"
    assert dead["status"] == "failed"


def test_perform_project_run_registers_and_unregisters(monkeypatch):
    import asyncio

    seen = {}

    async def fake_impl(run, cfg, started):
        seen["active"] = rs._is_run_active(run["run_id"])
        raise RuntimeError("boom")

    monkeypatch.setattr(rs, "_perform_project_run_impl", fake_impl)
    try:
        asyncio.run(rs._perform_project_run({"run_id": "run-x"}, "", 0.0))
    except RuntimeError:
        pass
    assert seen["active"] is True
    assert rs._is_run_active("run-x") is False
