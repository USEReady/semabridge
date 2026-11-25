"""Shared state in the database: a second server process (or a restart) sees
what another process wrote, deletes propagate as tombstones, run detail blobs
survive, and only changed rows are written."""
from datetime import datetime, timezone

import pytest

from semabridge.api.services import project_shared as ps
from semabridge.repository import state_store


@pytest.fixture
def fresh_state(monkeypatch):
    """An empty shared-state table and an empty per-process cache."""
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.state_models import StateDocument

    state_store.ensure_table()
    with db_manager.get_session() as s:
        s.query(StateDocument).delete()
        s.commit()
    for name, value in {
        "_compat_projects": {}, "_compat_project_configs": {}, "_compat_project_runs": {},
        "_compat_project_snapshots": {}, "_compat_run_snapshots": {}, "_compat_folders": {},
        "_compat_mappings": {}, "_compat_project_schedules": {}, "_compat_deleted_project_ids": set(),
        "_state_digests": {}, "_state_last_refresh": None, "_state_last_refresh_check": 0.0,
    }.items():
        monkeypatch.setattr(ps, name, value)
    monkeypatch.setenv("SEMABRIDGE_STATE_REFRESH_SECONDS", "0")
    yield
    with db_manager.get_session() as s:
        s.query(StateDocument).delete()
        s.commit()


def _as_other_process(monkeypatch):
    """Forget this process's cache, as a second process / restart would."""
    for name in ("_compat_mappings", "_compat_project_schedules", "_compat_projects", "_compat_project_runs"):
        monkeypatch.setattr(ps, name, {})
    monkeypatch.setattr(ps, "_state_digests", {})


def test_only_changed_rows_are_written(fresh_state, monkeypatch):
    calls = []
    real = state_store.apply_changes
    monkeypatch.setattr(state_store, "apply_changes", lambda ch: calls.append(list(ch)) or real(ch))

    ps._compat_mappings["m1"] = {"project_id": "p1", "target_name": "A"}
    ps._compat_mappings["m2"] = {"project_id": "p1", "target_name": "B"}
    assert ps._compat_persist_to_state_db(ps._compat_build_store_payload())
    first = {(c, k) for c, k, _ in calls[-1]}
    assert ("mappings", "m1") in first and ("mappings", "m2") in first

    ps._compat_mappings["m2"]["target_name"] = "B2"
    assert ps._compat_persist_to_state_db(ps._compat_build_store_payload())
    assert [(c, k) for c, k, _ in calls[-1]] == [("mappings", "m2")]

    n = len(calls)
    assert ps._compat_persist_to_state_db(ps._compat_build_store_payload())
    assert len(calls) == n  # nothing changed, nothing written


def test_another_process_sees_writes_and_deletes(fresh_state, monkeypatch):
    ps._compat_mappings["m1"] = {"project_id": "p1", "target_name": "A"}
    ps._compat_project_schedules["p1"] = {"cron": "0 1 * * *"}
    assert ps._compat_persist_to_state_db(ps._compat_build_store_payload())

    _as_other_process(monkeypatch)
    assert ps._compat_load_from_state_db() is True
    assert ps._compat_mappings["m1"]["target_name"] == "A"
    assert ps._compat_project_schedules["p1"]["cron"] == "0 1 * * *"

    # Delete in this process -> tombstone; a third process no longer sees it.
    ps._compat_mappings.pop("m1")
    assert ps._compat_persist_to_state_db(ps._compat_build_store_payload())
    _as_other_process(monkeypatch)
    ps._compat_load_from_state_db()
    assert "m1" not in ps._compat_mappings


def test_refresh_pulls_rows_written_by_another_worker(fresh_state, monkeypatch):
    ps._compat_mappings["m1"] = {"target_name": "A"}
    ps._compat_persist_to_state_db(ps._compat_build_store_payload())
    ps._state_last_refresh = datetime.now(timezone.utc)

    # Another worker changes m1 and deletes nothing.
    monkeypatch.setattr(state_store, "_worker_id", lambda: "other-host:1")
    state_store.apply_changes([("mappings", "m1", {"target_name": "FROM_OTHER"}), ("mappings", "m9", {"target_name": "NEW"})])

    applied = ps._compat_refresh_from_state_db(force=True)
    assert applied == 2
    assert ps._compat_mappings["m1"]["target_name"] == "FROM_OTHER"
    assert ps._compat_mappings["m9"]["target_name"] == "NEW"


def test_run_details_survive_and_are_not_tombstoned_by_absence(fresh_state, monkeypatch):
    ps._compat_project_runs["p1"] = [{"run_id": "r1", "status": "success", "summary": {"target_warnings": ["w"]}, "logs": ["l1"]}]
    ps._compat_persist_to_state_db(ps._compat_build_store_payload())

    # Restart: memory holds the run without blobs (stripped list) -> details stay.
    _as_other_process(monkeypatch)
    ps._compat_load_from_state_db()
    assert "summary" not in ps._compat_project_runs["p1"][0]
    ps._compat_persist_to_state_db(ps._compat_build_store_payload())
    assert ps.load_run_details(["r1"]) == {"r1": {"summary": {"target_warnings": ["w"]}, "logs": ["l1"]}}


def test_file_backend_switch_keeps_legacy_behaviour(fresh_state, monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_STATE_BACKEND", "file")
    ps._compat_mappings["m1"] = {"x": 1}
    assert ps._compat_persist_to_state_db(ps._compat_build_store_payload())
    assert state_store.load_keys("mappings", ["m1"]) == {}
    assert ps._state_json_mirror_enabled() is True


def test_json_mirror_is_off_on_hosted_profiles(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_STATE_JSON_MIRROR", raising=False)
    monkeypatch.delenv("SEMABRIDGE_STATE_BACKEND", raising=False)
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "azure")
    assert ps._state_json_mirror_enabled() is False
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "local")
    monkeypatch.delenv("SEMABRIDGE_ENV", raising=False)
    assert ps._state_json_mirror_enabled() is True
