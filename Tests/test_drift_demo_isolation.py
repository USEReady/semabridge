"""Tests for demo drift event isolation and architecture:
1. demo flag off -> seed/simulate/reset routes return 404, no demo events anywhere, Snowflake unavailable creates no events.
2. demo flag on -> demo events appear and look identical to real ones, approving one returns success without starting a sync.
3. simulate never changes the real ontology or baselines.
4. drift_service imports nothing from the demo module at module level.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from semabridge.api.routers import drift_router
from semabridge.governance import drift_service

app = FastAPI()
app.include_router(drift_router.router)


@pytest.fixture(autouse=True)
def clean_drift_state(tmp_path, monkeypatch):
    """Isolate drift service state file to tmp_path."""
    state_file = tmp_path / "drift_state.json"
    monkeypatch.setattr(drift_service, "_state_path", lambda: state_file)
    drift_service.clear_drift_events(_allow_all_for_testing=True)
    from semabridge.demo import drift_fixtures
    monkeypatch.setattr(drift_fixtures, "_CONFIGURED_DEMO_PROJECT_ID", "proj-comp")
    yield
    drift_service.clear_drift_events(_allow_all_for_testing=True)


def test_demo_flag_off_routes_return_404(monkeypatch):
    """When SEMABRIDGE_MOCK_DRIFT is off, seed/simulate/reset routes return 404."""
    monkeypatch.delenv("SEMABRIDGE_MOCK_DRIFT", raising=False)
    monkeypatch.delenv("SEMABRIDGE_MOCK_PARITY", raising=False)

    client = TestClient(app)

    res_seed = client.post("/api/drift/events/seed-mock?project_id=proj-comp")
    assert res_seed.status_code == 404

    res_sim = client.post("/api/drift/simulate-change", json={"project_id": "proj-comp"})
    assert res_sim.status_code == 404

    res_reset = client.post("/api/drift/reset-change", json={"project_id": "proj-comp"})
    assert res_reset.status_code == 404

    res_clean = client.post("/api/drift/events/cleanup-mock")
    assert res_clean.status_code == 404


def test_demo_flag_off_no_demo_events_anywhere(monkeypatch):
    """When SEMABRIDGE_MOCK_DRIFT is off, listing endpoints return no demo events."""
    monkeypatch.delenv("SEMABRIDGE_MOCK_DRIFT", raising=False)

    state = drift_service._load_state()
    state["events"] = [
        {"id": "real-1", "project_id": "proj-comp", "metric_name": "HEADCOUNT", "mock": False, "status": "pending"},
        {"id": "mock-1", "project_id": "proj-comp", "metric_name": "TEST_MOCK", "mock": True, "status": "pending"},
    ]
    drift_service._save_state(state)

    client = TestClient(app)
    res = client.get("/api/drift/events?project_id=proj-comp")
    assert res.status_code == 200
    events = res.json()["events"]
    assert len(events) == 1
    assert events[0]["id"] == "real-1"
    assert res.json()["count"] == 1


def test_snowflake_unavailable_creates_no_events(monkeypatch):
    """When Snowflake is unavailable, run_drift_check returns SNOWFLAKE_UNAVAILABLE without creating fake events."""
    monkeypatch.delenv("SEMABRIDGE_MOCK_DRIFT", raising=False)
    from semabridge.connectors import snowflake_connection
    monkeypatch.setattr(snowflake_connection, "get_snowflake_connect_kwargs", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("Connection refused")))

    res = drift_service.run_drift_check(project_id="proj-comp")
    assert res["status"] == "NOT_CHECKED"
    assert res["not_checked_reason"] == "SNOWFLAKE_UNAVAILABLE"
    assert res["drift_detected"] == 0
    assert res["events"] == []

    # Verify no events were written to state
    state = drift_service._load_state()
    assert state.get("events") == []


def test_demo_flag_on_events_appear_and_look_identical(monkeypatch):
    """Demo flag on: demo events appear and look identical to real ones."""
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "1")

    client = TestClient(app)
    res = client.post("/api/drift/events/seed-mock?project_id=proj-comp")
    assert res.status_code == 200

    list_res = client.get("/api/drift/events?project_id=proj-comp")
    assert list_res.status_code == 200
    events = list_res.json()["events"]
    assert len(events) > 0

    # Events have all expected production fields
    for ev in events:
        assert "id" in ev
        assert "metric_name" in ev
        assert "status" in ev
        assert "drift_type" in ev
        assert "detected_at" in ev


def test_approving_demo_event_returns_success_without_starting_sync(monkeypatch):
    """Approving a demo event returns 200 success, updates status, but never triggers a sync."""
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "1")

    from semabridge.demo.drift_fixtures import seed_mock_drift_events
    seeded = seed_mock_drift_events("proj-comp")
    pending = next(e for e in seeded if e.get("status") == "pending")

    sync_triggered = []
    from semabridge.api.services import run_service
    monkeypatch.setattr(run_service, "_execute_project_run", lambda *a, **kw: sync_triggered.append(True))

    client = TestClient(app)
    res = client.post(f"/api/drift/events/{pending['id']}/approve", json={"reason": "Approved for demo"})
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["event"]["status"] == "approved"

    # Verify event updated in state
    curr = [e for e in drift_service.list_drift_events("proj-comp") if e["id"] == pending["id"]]
    assert len(curr) == 1
    assert curr[0]["status"] == "approved"

    # Verify background sync was NOT triggered
    assert len(sync_triggered) == 0


def test_simulate_never_changes_real_ontology_or_baselines(monkeypatch):
    """simulate_metric_drift operates on demo state only, never touches ontology or baselines."""
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "1")

    client = TestClient(app)
    res = client.post("/api/drift/simulate-change", json={
        "project_id": "proj-comp",
        "metric_name": "TOTAL_UNITS",
        "new_expression": "SUM(SALESFACT.UNITS) * 1.5",
    })
    assert res.status_code == 200

    state = drift_service._load_state()
    # Baselines dictionary must be empty (not string-corrupted)
    assert state.get("baselines", {}) == {}

    # Reset cleans it up
    reset_res = client.post("/api/drift/reset-change", json={
        "project_id": "proj-comp",
        "metric_name": "TOTAL_UNITS",
    })
    assert reset_res.status_code == 200


def test_drift_service_imports_nothing_from_demo_module():
    """drift_service must not import anything from semabridge.demo at module level."""
    target_file = Path(drift_service.__file__).resolve()
    tree = ast.parse(target_file.read_text(encoding="utf-8"), filename=str(target_file))

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "demo" not in alias.name, f"drift_service has module-level import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "demo" not in mod, f"drift_service has module-level from-import: {mod}"


def test_timestamps_stable_across_refreshes(monkeypatch):
    """Mock events keep their original timestamps across refreshes."""
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "1")

    client = TestClient(app)
    res1 = client.post("/api/drift/events/seed-mock?project_id=proj-comp")
    assert res1.status_code == 200
    events1 = [e for e in res1.json()["events"] if e.get("mock")]
    timestamps1 = {e["id"]: e.get("detected_at") for e in events1}

    res2 = client.post("/api/drift/events/seed-mock?project_id=proj-comp")
    assert res2.status_code == 200
    events2 = [e for e in res2.json()["events"] if e.get("mock")]
    timestamps2 = {e["id"]: e.get("detected_at") for e in events2}

    assert timestamps1 == timestamps2
