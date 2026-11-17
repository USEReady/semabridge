"""The stale-run sweep is heartbeat based: a run that is still working is
never failed, however long it takes; a run orphaned by a crash/restart is
marked "interrupted" (not failed) once its heartbeat is older than the lease.

Real case behind this: a Snowflake sync whose post-deploy checks took ~14
minutes was shown as "failed: Run timed out" after a fixed 5 minutes.
"""
from datetime import datetime, timedelta, timezone

import pytest

from semabridge.api.services import run_service as rs
from semabridge.core import run_coordination as rc


@pytest.fixture()
def runs_table():
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import Run, Project  # noqa: F401
    from semabridge.repository.orm.session_factory import db_manager

    Base.metadata.create_all(db_manager.get_engine())
    yield db_manager
    with db_manager.get_session() as s:
        s.query(Run).filter(Run.project_id == "proj-sweep").delete()
        s.commit()


def _add_run(db, run_id, *, started_min, heartbeat_min=None, parent=None, kind="project"):
    from semabridge.repository.orm.models import Project, Run

    now = datetime.now(timezone.utc)
    with db.get_session() as s:
        if s.get(Project, "proj-sweep") is None:
            s.add(Project(project_id="proj-sweep", name="sweep"))
        s.add(Run(
            run_id=run_id, project_id="proj-sweep", status="running", run_kind=kind,
            started_at=now - timedelta(minutes=started_min), parent_run_id=parent,
            heartbeat_at=(now - timedelta(minutes=heartbeat_min)) if heartbeat_min is not None else None,
        ))
        s.commit()


def _status(db, run_id):
    from semabridge.repository.orm.models import Run

    with db.get_session() as s:
        row = s.get(Run, run_id)
        return row.status, row.error_message


def test_long_running_run_with_fresh_heartbeat_is_never_timed_out(runs_table):
    _add_run(runs_table, "r-long", started_min=60, heartbeat_min=0.2)
    rc.sweep_stale_runs()
    assert _status(runs_table, "r-long")[0] == "running"


def test_orphaned_run_is_interrupted_not_failed(runs_table):
    _add_run(runs_table, "r-dead", started_min=30, heartbeat_min=10)
    _add_run(runs_table, "r-dead-model", started_min=30, heartbeat_min=10, parent="r-dead", kind="model")
    rc.sweep_stale_runs()
    status, message = _status(runs_table, "r-dead")
    assert status == "interrupted"
    assert "interrupted" in message.lower()
    assert _status(runs_table, "r-dead-model")[0] == "interrupted"


def test_run_live_in_this_process_is_spared_even_with_an_old_heartbeat(runs_table):
    _add_run(runs_table, "r-live", started_min=30, heartbeat_min=10)
    _add_run(runs_table, "r-live-model", started_min=30, heartbeat_min=10, parent="r-live", kind="model")
    rc.register_live_run("r-live")
    try:
        rc.sweep_stale_runs()
    finally:
        rc.unregister_live_run("r-live")
    assert _status(runs_table, "r-live")[0] == "running"
    assert _status(runs_table, "r-live-model")[0] == "running"


def test_heartbeat_refreshes_run_and_its_model_rows(runs_table):
    from semabridge.repository.orm.models import Run

    _add_run(runs_table, "r-hb", started_min=30, heartbeat_min=10)
    _add_run(runs_table, "r-hb-model", started_min=30, heartbeat_min=10, parent="r-hb", kind="model")
    rc.heartbeat_once(["r-hb"])
    rc.sweep_stale_runs()
    with runs_table.get_session() as s:
        assert {r.status for r in s.query(Run).filter(Run.run_id.in_(["r-hb", "r-hb-model"]))} == {"running"}


def test_store_copy_follows_its_interrupted_db_row(runs_table, monkeypatch):
    _add_run(runs_table, "r-store", started_min=30, heartbeat_min=10)
    store_run = {"run_id": "r-store", "id": "r-store", "status": "running",
                 "started_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()}
    monkeypatch.setitem(rs._compat_project_runs, "proj-sweep", [store_run])
    rs._sweep_stale_runs(force=True)
    assert store_run["status"] == "interrupted"


def test_perform_project_run_heartbeats_while_running_and_stops_after(monkeypatch):
    import asyncio

    seen = {}

    async def fake_impl(run, cfg, started):
        seen["active"] = rs._is_run_active(run["run_id"])
        raise RuntimeError("boom")

    monkeypatch.setattr(rs, "_perform_project_run_impl", fake_impl)
    try:
        asyncio.run(rs._perform_project_run({"run_id": "run-x", "project_id": "proj-hb"}, "", 0.0))
    except RuntimeError:
        pass
    assert seen["active"] is True
    assert rs._is_run_active("run-x") is False
