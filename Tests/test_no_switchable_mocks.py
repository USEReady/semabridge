"""Test demo mode gating and no switchable mocks invariant.

With the demo flag off, assert real paths for /certify, drift refresh, and Blast Radius
(no mock rows, no demo events, no demo reports).
With it on, demo content is marked as demo.
One test each.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_certify_no_switchable_mocks(monkeypatch):
    """One test for /certify: real path with demo off, marked mock with demo on."""
    import semabridge.api.routers.ai_router as ai
    import semabridge.api.routers.reconciliation_router as rr
    from semabridge.reconciliation.dax_executor import DaxExecutionError

    # 1. Demo Flag OFF: assert real path (no mock rows)
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "false")
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "false")

    calls = {}
    monkeypatch.setattr(rr, "_validate_certify_request", lambda req, tok: None)

    def _sf(req, user_id=None, owner_id=None):
        calls["snowflake"] = req.sql
        return ai.ExecuteQueryResponse(
            success=True,
            target="SNOWFLAKE",
            columns=["REGION", "TOTAL_UNITS"],
            rows=[["West", 7]],
            is_simulated=False,
        )

    def _dax(query, **kw):
        calls["dax"] = query
        return SimpleNamespace(rows=[{"Region": "West", "Total Units": 7}], backend="rest_api")

    monkeypatch.setattr(ai, "execute_semantic_query", _sf)
    monkeypatch.setattr(rr, "execute_dax", _dax)
    monkeypatch.setattr(rr, "save_certification_record", lambda **kw: {"status": kw["status"]})

    req = rr.CertifyMetricRequest(
        metric_name="Total Units",
        view_metric="TOTAL_UNITS",
        target_table_or_view="DB.SCH.VIEW",
        target_kind="table",
        dimensions=["REGION"],
        pbi_dimension_columns=["Region"],
        dax_backend="rest_api",
    )
    cert_out = rr.certify_metric_end_to_end(req, SimpleNamespace(state=SimpleNamespace(user_id=None)))
    assert "snowflake" in calls and "dax" in calls
    assert cert_out["dax_backend"] == "rest_api"
    assert cert_out["verification"]["matched_rows"] == 1

    monkeypatch.setattr(rr, "execute_dax", lambda *a, **k: (_ for _ in ()).throw(DaxExecutionError("no msmdsrv")))
    with pytest.raises(HTTPException) as exc:
        rr.execute_dax_endpoint(rr.RunDaxRequest(query="EVALUATE {1}", backend="pbi_desktop"))
    assert exc.value.status_code == 400 and "no msmdsrv" in exc.value.detail

    # 2. Demo Flag ON: assert demo content is marked as demo
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "true")
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "true")

    cert_demo = rr.certify_metric_end_to_end(req, SimpleNamespace(state=SimpleNamespace(user_id=None)))
    assert cert_demo["dax_backend"] == "mock_fabric_analysis"
    assert cert_demo["verification"]["status"] == "CERTIFIED"

    dax_res = rr.execute_dax_endpoint(rr.RunDaxRequest(query="EVALUATE {1}", backend="pbi_desktop"))
    assert dax_res["success"] is True
    assert dax_res["backend"] == "mock_fabric_analysis"
    assert dax_res["metadata"] == {"mock": True}


def test_drift_refresh_no_switchable_mocks(monkeypatch, tmp_path):
    """One test for drift refresh: real path with demo off, marked demo events with demo on."""
    from semabridge.api.routers import drift_router
    from semabridge.governance import drift_service

    state = tmp_path / "drift_state.json"
    legacy_demo = {"id": "drift-demo-1", "project_id": "p1", "metric_name": "X", "status": "pending", "mock": True}
    real = {"id": "real-1", "project_id": "p1", "metric_name": "Y", "status": "pending", "proposed_snowflake_definition": "SUM(Y)"}
    state.write_text(json.dumps({"schedules": [], "events": [legacy_demo, real]}))
    monkeypatch.setattr(drift_service, "_state_path", lambda: state)
    # Read events from this test's JSON file only, never from drift rows other tests left in the shared DB.
    monkeypatch.setattr(drift_service, "_db_load_events", lambda: None)
    monkeypatch.setattr(drift_service, "_db_save_events", lambda *a, **k: False)
    monkeypatch.setattr(drift_router, "run_drift_check", lambda **kw: {"drift_detected": 0, "snowflake_connected": False})

    # 1. Demo Flag OFF: drops mock events, no demo events
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "false")
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "false")

    out_off = drift_router.refresh_drift_events(SimpleNamespace(state=SimpleNamespace(user_id=None)), project_id="p1")
    assert [e["id"] for e in out_off["events"]] == ["real-1"]

    # 2. Demo Flag ON: seeds mock events clearly marked with mock: True
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "true")
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "true")

    events = drift_service.seed_mock_drift_events(project_id="p1")
    demo_events = [e for e in events if e.get("id") != "real-1"]
    assert len(demo_events) > 0
    assert all(e.get("mock") is True for e in demo_events)


def test_blast_radius_no_switchable_mocks(monkeypatch):
    """One test for Blast Radius: no mock reports with demo off, marked demo reports with demo on."""
    from semabridge.governance.blast_radius import BlastRadiusSimulator

    # 1. Demo Flag OFF: no mock reports even if mock_mode=True
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "false")

    sim_off = BlastRadiusSimulator(mock_mode=True)
    res_off = sim_off.simulate(target_name="TOTAL_UNITS", target_type="METRIC")
    assert not any(getattr(r, "source", None) == "demo" for r in res_off.affected_bi_reports)

    # 2. Demo Flag ON: mock reports included and clearly marked as demo
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "true")

    sim_on = BlastRadiusSimulator(mock_mode=True)
    res_on = sim_on.simulate(target_name="TOTAL_UNITS", target_type="METRIC")
    demo_reports = [r for r in res_on.affected_bi_reports if getattr(r, "source", None) == "demo"]
    assert len(demo_reports) > 0
    assert any("[DEMO]" in r.details for r in demo_reports)


# ── G05 Scan Rules Verification ──────────────────────────────────────────────

def test_g05_rule_fails_on_unsanctioned_mocks():
    import sys

    sys.path.insert(0, str(Path(__file__).parent / "governance_tests"))
    from mock_rules import backend_hits, frontend_hits

    # Unsanctioned mock switch should fail F6a
    unsanctioned_env = 'def on():\n    return os.getenv("SEMABRIDGE_UNSANCTIONED_MOCK", "") == "true"\n'
    assert [h[1] for h in backend_hits(unsanctioned_env)] == ["F6a"]

    # Registered demo switch should pass
    registered_env = 'def on():\n    return os.getenv("SEMABRIDGE_MOCK_PARITY", "") == "true"\n'
    assert backend_hits(registered_env) == []

    # Unsanctioned mock producer should fail F6b
    unsanctioned_producer = "def make_fake_data():\n    return [1, 2, 3]\n"
    assert [h[1] for h in backend_hits(unsanctioned_producer)] == ["F6b"]

    # Allowed demo producers should pass
    sanctioned_producer = "rows = generate_mock_comparative_data(metric)\n_MOCK_BI_REPORTS = [\n"
    assert backend_hits(sanctioned_producer) == []

    # Clean code has zero hits
    clean = 'token = os.getenv("FABRIC_ACCESS_TOKEN")\n"""mock in a docstring is fine"""\n'
    assert backend_hits(clean) == []

    # Unsanctioned frontend mock flag should fail F6c
    assert [h[1] for h in frontend_hits("body: JSON.stringify({ fake_mode: true })")] == ["F6c"]
