"""Production code never returns mock rows, whatever the environment says.

Replaces the old mock-parity tests: SEMABRIDGE_MOCK_PARITY / SEMABRIDGE_MOCK_DRIFT
(and SEMABRIDGE_DEMO_MODE for drift events) no longer switch anything on.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_MOCK_ENV = {"SEMABRIDGE_MOCK_PARITY": "true", "SEMABRIDGE_MOCK_DRIFT": "true", "SEMABRIDGE_DEMO_MODE": "true"}


@pytest.fixture
def mock_env(monkeypatch):
    for k, v in _MOCK_ENV.items():
        monkeypatch.setenv(k, v)


def test_certify_runs_the_real_path_with_mock_env_set(mock_env, monkeypatch):
    import semabridge.api.routers.ai_router as ai
    import semabridge.api.routers.reconciliation_router as rr

    calls = {}
    monkeypatch.setattr(rr, "_validate_certify_request", lambda req, tok: None)

    def _sf(req, user_id=None, owner_id=None):
        calls["snowflake"] = req.sql
        return ai.ExecuteQueryResponse(success=True, target="SNOWFLAKE", columns=["REGION", "TOTAL_UNITS"],
                                       rows=[["West", 7]], is_simulated=False)

    def _dax(query, **kw):
        calls["dax"] = query
        return SimpleNamespace(rows=[{"Region": "West", "Total Units": 7}], backend="rest_api")

    monkeypatch.setattr(ai, "execute_semantic_query", _sf)
    monkeypatch.setattr(rr, "execute_dax", _dax)
    monkeypatch.setattr(rr, "save_certification_record", lambda **kw: {"status": kw["status"]})

    req = rr.CertifyMetricRequest(metric_name="Total Units", view_metric="TOTAL_UNITS",
                                  target_table_or_view="DB.SCH.VIEW", target_kind="table",
                                  dimensions=["REGION"], pbi_dimension_columns=["Region"],
                                  dax_backend="rest_api")
    out = rr.certify_metric_end_to_end(req, SimpleNamespace(state=SimpleNamespace(user_id=None)))

    assert "snowflake" in calls and "dax" in calls  # both sides actually executed
    assert out["dax_backend"] == "rest_api"  # never "mock_fabric_analysis"
    assert out["verification"]["matched_rows"] == 1


def test_run_dax_failure_is_an_error_not_mock_rows(mock_env, monkeypatch):
    from fastapi import HTTPException

    import semabridge.api.routers.reconciliation_router as rr
    from semabridge.reconciliation.dax_executor import DaxExecutionError

    monkeypatch.setattr(rr, "execute_dax", lambda *a, **k: (_ for _ in ()).throw(DaxExecutionError("no msmdsrv")))
    with pytest.raises(HTTPException) as e:
        rr.execute_dax_endpoint(rr.RunDaxRequest(query="EVALUATE {1}", backend="pbi_desktop"))
    assert e.value.status_code == 400 and "no msmdsrv" in e.value.detail


def test_drift_refresh_and_check_never_add_demo_events(mock_env, monkeypatch, tmp_path):
    from semabridge.api.routers import drift_router
    from semabridge.governance import drift_service

    state = tmp_path / "drift_state.json"
    legacy_demo = {"id": "drift-comp-mkt-share", "project_id": "p1", "metric_name": "X", "status": "pending", "mock": True}
    real = {"id": "real-1", "project_id": "p1", "metric_name": "Y", "status": "pending", "proposed_snowflake_definition": "SUM(Y)"}
    state.write_text(json.dumps({"schedules": [], "events": [legacy_demo, real]}))
    monkeypatch.setattr(drift_service, "_state_path", lambda: state)
    monkeypatch.setattr(drift_router, "run_drift_check", lambda **kw: {"drift_detected": 0, "snowflake_connected": False})

    out = drift_router.refresh_drift_events(SimpleNamespace(state=SimpleNamespace(user_id=None)), project_id="p1")
    assert [e["id"] for e in out["events"]] == ["real-1"]  # legacy demo row dropped, nothing seeded
    assert not hasattr(drift_service, "seed_mock_drift_events")
    assert not hasattr(drift_service, "get_default_mock_drift_events")


def test_blast_radius_rejects_mock_mode_and_has_no_fake_reports():
    import semabridge.governance.blast_radius as br
    from semabridge.api.routers.governance_router import BlastRadiusRequest

    with pytest.raises(Exception):
        BlastRadiusRequest(target_name="TOTAL_UNITS", target_type="METRIC", mock_mode=True)
    assert not any(n.startswith("_MOCK") for n in vars(br))
    assert importlib.util.find_spec("semabridge.reconciliation.mock_parity") is None


def test_g05_rule_fails_on_env_switched_mocks():
    import sys

    sys.path.insert(0, str(Path(__file__).parent / "governance_tests"))
    from mock_rules import backend_hits, frontend_hits

    env_switched = 'def on():\n    return os.getenv("SEMABRIDGE_MOCK_PARITY", "") == "true"\n'
    producer = "rows = generate_mock_comparative_data(metric)\n_MOCK_BI_REPORTS = [\n"
    clean = 'token = os.getenv("FABRIC_ACCESS_TOKEN")\n"""mock in a docstring is fine"""\n'
    assert [h[1] for h in backend_hits(env_switched)] == ["F6a"]
    assert {h[2] for h in backend_hits(producer)} == {"generate_mock_comparative_data", "_MOCK_BI_REPORTS"}
    assert backend_hits(clean) == []
    assert [h[1] for h in frontend_hits("body: JSON.stringify({ mock_mode: true })")] == ["F6c"]
