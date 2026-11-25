"""Live data is the default: demo/mock data only on servers started with
SEMABRIDGE_DEMO_MODE=true, and ontology-rewriting drift simulation is
refused otherwise."""
import pytest
from fastapi import HTTPException

from semabridge.api.routers import drift_router
from semabridge.api.routers.system_router import demo_status


def test_demo_status_defaults_to_live(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_DEMO_MODE", raising=False)
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "true")
    status = demo_status()
    assert status == {"demo_available": False, "mock_parity": False, "mock_drift": False, "default_mode": "live"}


def test_demo_status_when_server_allows_it(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEMO_MODE", "true")
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "true")
    assert demo_status()["demo_available"] is True
    assert demo_status()["mock_parity"] is True


def test_drift_simulation_refused_without_demo_mode(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_DEMO_MODE", raising=False)
    req = drift_router.SimulateMetricChangeRequest(project_id="p", metric_name="m", new_expression="SUM(x)")
    with pytest.raises(HTTPException) as exc:
        drift_router.simulate_change(req)
    assert exc.value.status_code == 403


def test_blast_radius_mock_needs_server_demo_mode(monkeypatch):
    from semabridge.governance.blast_radius import BlastRadiusSimulator

    monkeypatch.delenv("SEMABRIDGE_DEMO_MODE", raising=False)
    report = BlastRadiusSimulator(mock_mode=True).simulate("Revenue", "metric", "rename")
    data = report if isinstance(report, dict) else report.__dict__
    assert not (data.get("affected_bi_reports") or [])
