"""Drift approval / clear flow on a fixture event (formerly driven by mock drift)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.governance import drift_service

FIXTURE_EVENT = {
    "id": "drift-fixture-1",
    "project_id": "proj-fixture",
    "domain": "FIXTURE",
    "metric_name": "TOTAL_UNITS",
    "drift_type": "EXPRESSION_MISMATCH",
    "bi_definition": "SUM(F.UNITS)",
    "snowflake_definition": "SUM(F.QTY)",
    "proposed_snowflake_definition": "SUM(F.UNITS)",
    "status": "pending",
    "resolution_reason": "",
    "detected_at": "2026-01-01T00:00:00+00:00",
}


def test_pending_drift_can_be_approved_and_cleared(monkeypatch, tmp_path):
    state = tmp_path / "drift_state.json"
    state.write_text(json.dumps({"schedules": [], "events": [FIXTURE_EVENT]}))
    monkeypatch.setattr(drift_service, "_state_path", lambda: state)

    assert [e["id"] for e in drift_service.list_drift_events(project_id="proj-fixture", status="pending")] == ["drift-fixture-1"]
    client = TestClient(app)
    res = client.post("/api/drift/events/drift-fixture-1/approve", json={"reason": "Approved for sync"})
    assert res.status_code == 200 and res.json()["event"]["status"] == "approved"
    res = client.delete("/api/drift/events?project_id=proj-fixture")
    assert res.status_code == 200 and res.json()["success"] is True
    assert drift_service.list_drift_events(project_id="proj-fixture") == []
