"""Test demo mode gating and no switchable mocks invariant.

With the demo flag off: every path is real (no mock rows, no demo events, real errors).
With the demo flag on: demo content is enabled and clearly marked as demo.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
def demo_off(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "false")
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "false")
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "false")


@pytest.fixture
def demo_on(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "true")
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "true")
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "true")


# ── Demo Flag OFF (Real Paths Only) ───────────────────────────────────────────

def test_certify_runs_the_real_path_with_demo_off(demo_off, monkeypatch):
    import semabridge.api.routers.ai_router as ai
    import semabridge.api.routers.reconciliation_router as rr

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
    out = rr.certify_metric_end_to_end(req, SimpleNamespace(state=SimpleNamespace(user_id=None)))

    assert "snowflake" in calls and "dax" in calls
    assert out["dax_backend"] == "rest_api"
    assert out["verification"]["matched_rows"] == 1


def test_run_dax_failure_is_an_error_with_demo_off(demo_off, monkeypatch):
    import semabridge.api.routers.reconciliation_router as rr
    from semabridge.reconciliation.dax_executor import DaxExecutionError

    monkeypatch.setattr(rr, "execute_dax", lambda *a, **k: (_ for _ in ()).throw(DaxExecutionError("no msmdsrv")))
    with pytest.raises(HTTPException) as e:
        rr.execute_dax_endpoint(rr.RunDaxRequest(query="EVALUATE {1}", backend="pbi_desktop"))
    assert e.value.status_code == 400 and "no msmdsrv" in e.value.detail


def test_drift_refresh_drops_mock_events_with_demo_off(demo_off, monkeypatch, tmp_path):
    from semabridge.api.routers import drift_router
    from semabridge.governance import drift_service

    state = tmp_path / "drift_state.json"
    legacy_demo = {"id": "drift-demo-1", "project_id": "p1", "metric_name": "X", "status": "pending", "mock": True}
    real = {"id": "real-1", "project_id": "p1", "metric_name": "Y", "status": "pending", "proposed_snowflake_definition": "SUM(Y)"}
    state.write_text(json.dumps({"schedules": [], "events": [legacy_demo, real]}))
    monkeypatch.setattr(drift_service, "_state_path", lambda: state)
    monkeypatch.setattr(drift_router, "run_drift_check", lambda **kw: {"drift_detected": 0, "snowflake_connected": False})

    out = drift_router.refresh_drift_events(SimpleNamespace(state=SimpleNamespace(user_id=None)), project_id="p1")
    assert [e["id"] for e in out["events"]] == ["real-1"]


def test_blast_radius_has_no_mock_reports_with_demo_off(demo_off):
    from semabridge.governance.blast_radius import BlastRadiusSimulator

    # In demo mode off, even if mock_mode=True is requested, demo reports are omitted
    simulator = BlastRadiusSimulator(mock_mode=True)
    res = simulator.simulate(target_name="TOTAL_UNITS", target_type="METRIC")
    assert not any(getattr(r, "source", None) == "demo" for r in res.affected_bi_reports)


# ── Demo Flag ON (Demo Content Clearly Marked) ───────────────────────────────

def test_certify_with_demo_on_returns_mock_parity(demo_on, monkeypatch):
    import semabridge.api.routers.reconciliation_router as rr

    monkeypatch.setattr(rr, "_validate_certify_request", lambda req, tok: None)
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
    out = rr.certify_metric_end_to_end(req, SimpleNamespace(state=SimpleNamespace(user_id=None)))
    assert out["dax_backend"] == "mock_fabric_analysis"
    assert out["verification"]["status"] == "CERTIFIED"


def test_run_dax_with_demo_on_returns_mock_rows_marked(demo_on, monkeypatch):
    import semabridge.api.routers.reconciliation_router as rr
    from semabridge.reconciliation.dax_executor import DaxExecutionError

    monkeypatch.setattr(rr, "execute_dax", lambda *a, **k: (_ for _ in ()).throw(DaxExecutionError("no msmdsrv")))
    res = rr.execute_dax_endpoint(rr.RunDaxRequest(query="EVALUATE {1}", backend="pbi_desktop"))
    assert res["success"] is True
    assert res["backend"] == "mock_fabric_analysis"
    assert res["metadata"] == {"mock": True}


def test_drift_seed_mock_events_clearly_marked(demo_on, monkeypatch, tmp_path):
    from semabridge.governance import drift_service

    state = tmp_path / "drift_state.json"
    state.write_text(json.dumps({"schedules": [], "events": []}))
    monkeypatch.setattr(drift_service, "_state_path", lambda: state)

    events = drift_service.seed_mock_drift_events(project_id="p1")
    assert len(events) > 0
    assert all(e.get("mock") is True for e in events)


def test_blast_radius_includes_demo_reports_marked_as_demo(demo_on):
    from semabridge.governance.blast_radius import BlastRadiusSimulator

    simulator = BlastRadiusSimulator(mock_mode=True)
    res = simulator.simulate(target_name="TOTAL_UNITS", target_type="METRIC")
    demo_reports = [r for r in res.affected_bi_reports if getattr(r, "source", None) == "demo"]
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

