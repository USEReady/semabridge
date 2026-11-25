"""Drift: schedules survive restarts (shared state), scheduled checks run on
the project's own target, a changed Power BI model is a source-drift event
(once per change) that never pauses a scheduled sync and is closed by a
successful sync."""
import pytest

from semabridge.governance import drift_service as ds
from semabridge.repository import state_store


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    state_store.ensure_table()
    monkeypatch.setattr(ds, "_state_path", lambda: tmp_path / "drift_state.json")
    monkeypatch.setattr(ds, "_db_load_events", lambda: None)
    monkeypatch.setattr(ds, "_db_save_events", lambda *a, **k: False)
    monkeypatch.setattr(ds, "_register_job", lambda entry: None)
    for k in (state_store.load_collection("drift_schedules") or {}):
        state_store.apply_changes([("drift_schedules", k, None)])
    yield


def test_schedules_are_stored_and_restored(monkeypatch):
    entry = ds.create_schedule(project_id="p1", interval_minutes=30)
    registered = []
    monkeypatch.setattr(ds, "_register_job", lambda e: registered.append(e["id"]))
    assert ds.restore_schedules() == 1
    assert registered == [entry["id"]]
    assert [s["id"] for s in ds.list_schedules("p1")] == [entry["id"]]
    assert ds.delete_schedule(entry["id"]) is True
    assert ds.list_schedules("p1") == []


def test_check_dispatches_to_the_projects_target(monkeypatch):
    import semabridge.api.routers.drift_router as dr

    calls = []
    monkeypatch.setattr(dr, "_resolve_project_target_type", lambda pid, explicit=None: "databricks")
    monkeypatch.setattr(dr, "_run_databricks_drift_check", lambda project_id, owner_id=None: calls.append("dbx") or {"events": []})
    monkeypatch.setattr(ds, "run_drift_check", lambda **kw: calls.append("sf") or {})
    monkeypatch.setattr(ds, "check_source_drift", lambda pid: None)
    out = ds.run_project_drift_check("p1")
    assert calls == ["dbx"] and out["target_type"] == "databricks" and out["source_changed"] is False


def _fingerprints(monkeypatch, current, last):
    import semabridge.api.services.project_config_store as pcs
    import semabridge.api.services.run_records as rr
    import semabridge.api.services.run_service as rs

    monkeypatch.setattr(pcs, "get_project_config_text", lambda pid, **kw: "source: {type: pbix}")
    monkeypatch.setattr(rs, "_project_fingerprint", lambda pid, cfg: current)
    monkeypatch.setattr(rr, "list_project_runs", lambda pid: [{"status": "success", "source_fingerprint": last}])


def test_changed_power_bi_model_is_one_source_event(monkeypatch):
    _fingerprints(monkeypatch, "fp-new", "fp-old")
    ev = ds.check_source_drift("p1")
    assert ev["drift_type"] == "SOURCE_CHANGED" and ev["direction"] == "source"
    assert ds.check_source_drift("p1") is None  # same change: not duplicated
    assert ds.resolve_source_drift_events("p1") == 1
    assert ds.list_drift_events("p1", status="pending") == []


def test_unchanged_or_unknown_source_is_not_drift(monkeypatch):
    _fingerprints(monkeypatch, "fp-same", "fp-same")
    assert ds.check_source_drift("p1") is None
    _fingerprints(monkeypatch, None, "fp-old")
    assert ds.check_source_drift("p1") is None


def test_scheduled_job_runs_only_on_the_leader(monkeypatch):
    from semabridge.core import run_coordination

    monkeypatch.setattr(run_coordination.scheduler_leadership, "is_leader", lambda: False)
    assert ds.scheduled_drift_job("p1") == {"skipped": "not the scheduler leader"}
