import os
import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.governance.drift_service import (
    _load_state,
    _save_state,
    list_drift_events,
    resolve_drift_event,
    run_drift_check,
)
from semabridge.reconciliation.mock_parity import (
    generate_mock_comparative_data,
    is_mock_drift_enabled,
    is_mock_parity_enabled,
)


@pytest.fixture(autouse=True)
def clean_drift_events():
    state = _load_state()
    orig_events = state.get("events", [])
    yield
    state = _load_state()
    state["events"] = orig_events
    _save_state(state)


def test_mock_comparative_data_generator():
    sql_rows, dax_rows = generate_mock_comparative_data(
        metric_name="COUNT_OF_PRODUCT",
        dimensions=["PRODUCT.MANUF"],
        target_table_or_view="SEMABRIDGE.SEMAE",
    )
    assert len(sql_rows) == 10
    assert len(dax_rows) == 10
    # Values match slice by slice
    for s_row, d_row in zip(sql_rows, dax_rows):
        assert s_row["PRODUCT.MANUF"] == d_row["PRODUCT.MANUF"]
        assert s_row["COUNT_OF_PRODUCT"] == d_row["COUNT_OF_PRODUCT"]


def test_certify_endpoint_mock_enabled(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "true")
    assert is_mock_parity_enabled() is True

    client = TestClient(app)
    body = {
        "metric_name": "COUNT_OF_PRODUCT",
        "target_table_or_view": "SEMABRIDGE_WORKSPACE.COMPETITIVE_MARKETING_ANALYSIS",
        "target_kind": "table",
        "dimensions": ["PRODUCT.MANUF"],
        "project_id": "test_mock_proj",
    }
    res = client.post("/api/reconciliation/certify", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["dax_backend"] == "mock_fabric_analysis"
    verification = data["verification"]
    assert verification["status"] == "CERTIFIED"
    assert verification["matched_rows"] == 10
    assert verification["mismatched_rows"] == 0
    assert verification["max_percentage_delta"] == 0.0
    assert "Certified 100% Match" in verification["certification_badge"]
    assert len(verification["comparison_rows"]) == 10


def test_certify_endpoint_mock_disabled(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "false")
    assert is_mock_parity_enabled() is False

    client = TestClient(app)
    body = {
        "metric_name": "COUNT_OF_PRODUCT",
        "target_table_or_view": "SEMABRIDGE_WORKSPACE.COMPETITIVE_MARKETING_ANALYSIS",
        "target_kind": "table",
        "dimensions": ["PRODUCT.MANUF"],
        "project_id": "test_mock_proj",
    }
    # When mock is off, attempting live execution without live warehouse/PBI raises 400
    res = client.post("/api/reconciliation/certify", json=body)
    assert res.status_code == 400
    detail = res.json().get("detail", "")
    assert "Live Snowflake execution failed" in detail or "Power BI" in detail or "msmdsrv" in detail


def test_scheduled_run_drift_approval_guard(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "true")
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "true")
    assert is_mock_drift_enabled() is True

    # 1. Trigger drift check which generates pending drift event in mock mode
    drift_res = run_drift_check(project_id="tiral_comp")
    assert drift_res["drift_detected"] >= 1

    events = list_drift_events(project_id="tiral_comp", status="pending")
    assert len(events) >= 1
    ev_id = events[0]["id"]
    assert events[0]["status"] == "pending"
    assert events[0]["drift_type"] == "EXPRESSION_MISMATCH"

    # 2. Test approval via endpoint
    client = TestClient(app)
    appr_res = client.post(f"/api/drift/events/{ev_id}/approve", json={"reason": "Approved for sync"})
    assert appr_res.status_code == 200
    assert appr_res.json()["event"]["status"] == "approved"

    # 3. Test simulate metric change & reset endpoints
    sim_res = client.post("/api/drift/simulate-change", json={"project_id": "proj-tiral_comp", "metric_name": "TOTAL_UNITS"})
    assert sim_res.status_code == 200
    assert "TOTAL_UNITS" in sim_res.json()["metric_name"]
    assert "1.05" in sim_res.json()["modified_expression"]

    # 4. Test delete / clear drift events
    del_res = client.delete("/api/drift/events?project_id=proj-tiral_comp")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # 5. Test reset metric change
    reset_res = client.post("/api/drift/reset-change", json={"project_id": "proj-tiral_comp", "metric_name": "TOTAL_UNITS"})
    assert reset_res.status_code == 200
    assert reset_res.json()["success"] is True

