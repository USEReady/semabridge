"""Two server processes sharing one database (uvicorn --workers 2, or two
hosts): Postgres is the authority, each process's memory only a cache.

A "process" here is a separate set of in-memory compat caches, its own
compat-store JSON file, its own Config/projects directory (a host) and its
own in-process run semaphore; both share one SQLite file database. Switching
process swaps those in, so nothing can travel between the two except through
the database.
"""
from __future__ import annotations

import asyncio
import copy
import threading
import time

import pytest
import yaml

import semabridge.api.services.project_shared as ps
from semabridge.api.services import project_projects_impl as ppi
from semabridge.api.services import run_service as rs
from semabridge.core import run_coordination as rc
from semabridge.domain.exceptions import NotFoundError
from semabridge.governance import drift_service

_CACHES = (
    "_compat_projects", "_compat_project_configs", "_compat_project_runs", "_compat_project_snapshots",
    "_compat_snapshot_groups", "_compat_run_snapshots", "_compat_folders", "_compat_mappings",
    "_compat_project_schedules",
)


def _reset_engine() -> None:
    from semabridge.core.db_resolver import clear_db_config_cache
    from semabridge.repository.orm.session_factory import db_manager

    db_manager.reset()
    clear_db_config_cache()


@pytest.fixture
def shared_db(tmp_path, monkeypatch):
    """A fresh SQLite file database both "processes" use."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.session_factory import db_manager
    import semabridge.repository.orm.models  # noqa: F401

    url = f"sqlite:///{tmp_path / 'shared.db'}"
    monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", url)
    # Global slot leases are PostgreSQL-only by default; this shared file
    # database (own connection per session, below) stands in for it.
    monkeypatch.setenv("SEMABRIDGE_GLOBAL_SLOTS", "on")
    # Separate processes have separate connections and transactions: one
    # connection per session (the app's SQLite StaticPool would share one
    # connection -- and its transaction -- between all threads).
    monkeypatch.setattr(
        db_manager, "create_db_engine",
        lambda u: create_engine(u, future=True, poolclass=NullPool,
                                connect_args={"check_same_thread": False, "timeout": 30}),
    )
    _reset_engine()

    Base.metadata.create_all(bind=db_manager.get_engine())
    yield tmp_path
    monkeypatch.undo()
    _reset_engine()


class FakeProcess:
    """One server process: private caches, store file, host config dir."""

    def __init__(self, name: str, root, monkeypatch):
        self.name = name
        self.monkeypatch = monkeypatch
        self.projects_dir = root / name / "Config" / "projects"
        self.projects_dir.mkdir(parents=True)
        self.store_path = root / name / "compat_store.json"
        self.caches = {attr: copy.deepcopy(type(getattr(ps, attr))()) for attr in _CACHES}
        self.deleted: set = set()
        self.bootstrapped = False
        self.run_semaphore = threading.Semaphore(rs._MAX_CONCURRENT_RUNS)
        self.run_slots = rc.SlotPool("run", lambda: rs._MAX_CONCURRENT_RUNS, lambda: self.run_semaphore)

    def activate(self) -> "FakeProcess":
        mp = self.monkeypatch
        pdir = self.projects_dir
        mp.setenv("SEMABRIDGE_COMPAT_STORE_PATH", str(self.store_path))
        for module in (ps, ppi):
            mp.setattr(module, "_compat_projects_dirs", lambda: [pdir], raising=False)
        mp.setattr(ps, "_compat_projects_dir", lambda: pdir)
        mp.setattr(ps, "_compat_project_yaml_path", lambda pid: pdir / f"{pid}.yaml")
        mp.setattr(ps, "_compat_project_yaml_paths", lambda pid: [pdir / f"{pid}.yaml"])
        mp.setattr(ppi, "_compat_project_yaml_path", lambda pid: pdir / f"{pid}.yaml")
        mp.setattr(ppi, "_compat_project_yaml_paths", lambda pid: [pdir / f"{pid}.yaml"])
        mp.setattr(ps, "_compat_load_repo_yaml_text", lambda: "")
        mp.setattr(ppi, "_compat_load_repo_yaml_text", lambda: "")
        for attr, value in self.caches.items():
            live = getattr(ps, attr)
            live.clear()
            live.update(copy.deepcopy(value))
        ps._compat_deleted_project_ids.clear()
        ps._compat_deleted_project_ids.update(self.deleted)
        mp.setattr(ps, "_compat_bootstrapped", self.bootstrapped)
        mp.setattr(ps, "_compat_store_loaded", self.bootstrapped)
        return self

    def deactivate(self) -> None:
        self.caches = {attr: copy.deepcopy(getattr(ps, attr)) for attr in _CACHES}
        self.deleted = set(ps._compat_deleted_project_ids)
        self.bootstrapped = ps._compat_bootstrapped


@pytest.fixture
def processes(shared_db, monkeypatch):
    saved = {attr: copy.deepcopy(getattr(ps, attr)) for attr in _CACHES}
    saved_deleted = set(ps._compat_deleted_project_ids)
    a = FakeProcess("a", shared_db, monkeypatch)
    b = FakeProcess("b", shared_db, monkeypatch)
    current = {"p": None}

    def switch(proc: FakeProcess) -> FakeProcess:
        if current["p"] is not None:
            current["p"].deactivate()
        current["p"] = proc.activate()
        return proc

    yield a, b, switch
    for attr, value in saved.items():
        getattr(ps, attr).clear()
        getattr(ps, attr).update(value)
    ps._compat_deleted_project_ids.clear()
    ps._compat_deleted_project_ids.update(saved_deleted)


def _create(name: str) -> dict:
    return asyncio.run(ppi.create_project_compat({
        "name": name, "user_id": "7", "source": {"type": "fabric"}, "targets": [{"type": "snowflake"}],
    }))


def _list_ids() -> list:
    return [p["id"] for p in asyncio.run(ppi.list_projects_compat())]


def test_project_created_on_one_process_is_readable_on_the_other(processes):
    a, b, switch = processes
    switch(a)
    created = _create(f"Shared {time.time_ns()}")
    pid = created["id"]

    switch(b)
    assert pid not in ps._compat_projects  # B never saw the create in memory
    got = asyncio.run(ppi.get_project_compat(pid))
    assert got["id"] == pid and got["owner_user_id"] == "7"
    assert pid in _list_ids()
    cfg = asyncio.run(ppi.get_project_config_compat(pid, prefer_repo=False))
    assert yaml.safe_load(cfg["config_yaml"])["project_id"] == pid


def test_deleted_project_stays_deleted_on_every_process(processes):
    a, b, switch = processes
    switch(a)
    pid = _create(f"Doomed {time.time_ns()}")["id"]
    switch(b)
    asyncio.run(ppi.get_project_compat(pid))  # B has it cached now
    # Host B still has a project YAML on disk (as after a file sync).
    (b.projects_dir / f"{pid}.yaml").write_text(f"project_id: {pid}\nproject_name: Doomed\n")

    switch(a)
    asyncio.run(ppi.delete_project_compat(pid))
    assert pid not in _list_ids()

    switch(b)
    assert pid in ps._compat_projects  # stale cache entry on B ...
    assert pid not in _list_ids()      # ... but the database decides
    with pytest.raises(NotFoundError):
        asyncio.run(ppi.get_project_compat(pid))
    from semabridge.api.services.project_config_store import get_project_config_text

    assert get_project_config_text(pid) == ""  # B's leftover file is not re-imported

    # A restarted process B (empty caches, its JSON store gone) agrees.
    b.caches = {attr: type(getattr(ps, attr))() for attr in _CACHES}
    b.deleted, b.bootstrapped = set(), False
    switch(a)
    switch(b)
    assert pid not in _list_ids()


def test_project_metadata_written_on_one_process_drives_the_other(processes):
    a, b, switch = processes
    switch(a)
    pid = _create(f"Meta {time.time_ns()}")["id"]
    switch(b)
    asyncio.run(ppi.get_project_compat(pid))  # cached on B before the change
    switch(a)
    asyncio.run(ppi.patch_project_compat(pid, {"sync_mode": "upsert", "status": "active"}))

    switch(b)
    assert ps._compat_projects[pid].get("sync_mode") != "upsert"  # B's cache is stale
    assert rs._effective_sync_mode(pid, "project_name: x\n", {}) == "upsert"
    assert ps._compat_project_sync_mode(pid) == "upsert"
    listed = {p["id"]: p for p in asyncio.run(ppi.list_projects_compat())}
    assert listed[pid]["status"] == "active"


def test_run_cap_holds_across_processes(processes, monkeypatch):
    a, b, _switch = processes
    monkeypatch.setattr(rs, "_MAX_CONCURRENT_RUNS", 2)
    a_tokens = [a.run_slots.try_acquire(), a.run_slots.try_acquire()]
    assert all(a_tokens)
    # Process B has its own free semaphore, but the global cap is reached.
    assert b.run_slots.try_acquire() is None

    started = threading.Event()
    holder = {}

    def queued_run_on_b():
        holder["token"] = b.run_slots.acquire()  # waits; never fails
        started.set()

    monkeypatch.setenv("SEMABRIDGE_SLOT_POLL_SECONDS", "0.05")
    t = threading.Thread(target=queued_run_on_b)
    t.start()
    assert not started.wait(0.4)
    a.run_slots.release(a_tokens.pop())
    assert started.wait(5)
    t.join(5)
    assert b.run_slots.held_globally() == 2
    a.run_slots.release(a_tokens.pop())
    b.run_slots.release(holder["token"])
    assert b.run_slots.held_globally() == 0


def test_concurrent_runs_on_two_processes_never_exceed_the_cap(processes, monkeypatch):
    a, b, _switch = processes
    monkeypatch.setattr(rs, "_MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setenv("SEMABRIDGE_SLOT_POLL_SECONDS", "0.05")
    lock = threading.Lock()
    state = {"now": 0, "peak": 0, "done": 0}

    def run(pool):
        token = pool.acquire()
        try:
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.1)
        finally:
            with lock:
                state["now"] -= 1
                state["done"] += 1
            pool.release(token)

    threads = [threading.Thread(target=run, args=(p,)) for p in (a.run_slots, b.run_slots) * 3]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert state["done"] == 6
    assert state["peak"] <= 2


def test_model_slots_are_global(processes):
    taken = [rc.model_slots.try_acquire() for _ in range(rc.max_concurrent_models())]
    try:
        assert all(taken)
        other_process = rc.SlotPool("model", rc.max_concurrent_models,
                                    lambda sem=threading.BoundedSemaphore(rc.max_concurrent_models()): sem)
        assert other_process.try_acquire() is None
    finally:
        for token in taken:
            rc.model_slots.release(token)


def test_drift_events_are_shared(processes, monkeypatch, tmp_path):
    a, b, switch = processes
    switch(a)
    monkeypatch.setattr(drift_service, "_state_path", lambda: tmp_path / "a_drift.json")
    drift_service.seed_mock_drift_events("proj-mp")
    seeded = drift_service.list_drift_events("proj-mp")
    assert seeded

    switch(b)  # another host: its own (empty) drift file
    monkeypatch.setattr(drift_service, "_state_path", lambda: tmp_path / "b_drift.json")
    assert {e["id"] for e in drift_service.list_drift_events("proj-mp")} == {e["id"] for e in seeded}
    pending = next(e for e in seeded if e.get("status") == "pending")
    drift_service.resolve_drift_event(pending["id"], "ignore", "not ours")

    switch(a)
    monkeypatch.setattr(drift_service, "_state_path", lambda: tmp_path / "a_drift.json")
    again = {e["id"]: e for e in drift_service.list_drift_events("proj-mp")}
    assert again[pending["id"]]["status"] == "ignored"
    assert drift_service.clear_drift_events("proj-mp") == len(seeded)


def test_old_drift_json_is_imported_once_into_an_empty_table(processes, monkeypatch, tmp_path):
    import json

    state_file = tmp_path / "legacy_drift.json"
    monkeypatch.setattr(drift_service, "_state_path", lambda: state_file)
    drift_service.clear_drift_events()
    state_file.write_text(json.dumps({"schedules": [], "events": [
        {"id": "legacy-1", "project_id": "proj-old", "metric_name": "M", "status": "pending",
         "proposed_snowflake_definition": "SUM(X)"},
    ]}))
    assert [e["id"] for e in drift_service.list_drift_events("proj-old")] == ["legacy-1"]
    drift_service.clear_drift_events("proj-old")
    # Cleared in the database: the legacy file is not imported a second time.
    assert drift_service.list_drift_events("proj-old") == []
