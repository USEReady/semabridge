"""Tests for FastAPI metric comparison endpoints."""
import os
os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")

import pytest
from fastapi.testclient import TestClient
from semabridge.api.main import app
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.session_factory import get_session_factory


@pytest.fixture(autouse=True)
def setup_tables():
    factory = get_session_factory()
    engine = factory.kw.get("bind")
    if engine is not None:
        Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_metric_comparison_mock_api(client):
    payload = {
        "project_id": "api_test_project",
        "metric_name": "Total Sales",
        "source_platform": "mock",
        "target_platform": "snowflake",
        "target_view": "TEST_DB.TEST_SCHEMA.TEST_VIEW",
        "source_config": {"fixtures_dir": "Tests/fixtures/baselines"},
        "target_config": {},
        "tolerance_pct": 0.01,
        "mock_mode": True,
    }
    resp = client.post("/api/bi-truth/metric-comparison/run", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "verdict" in data
    assert data["metric_name"] == "Total Sales"
    assert "source" in data
    assert "target" in data


def test_metric_comparison_config_endpoints(client):
    # GET default config
    res_get = client.get("/api/bi-truth/metric-comparison/config?project_id=p1")
    assert res_get.status_code == 200
    data = res_get.json()
    assert "effective" in data

    # PUT updated config
    update_payload = {
        "project_id": "p1",
        "metric_name": "CLE",
        "tolerance_pct": 0.05,
        "absolute_floor": 0.02,
        "null_policy": "NULL_EQUALS_ZERO",
    }
    res_put = client.put("/api/bi-truth/metric-comparison/config", json=update_payload)
    assert res_put.status_code == 200
    updated = res_put.json()
    assert updated["tolerance_pct"] == 0.05
    assert updated["null_policy"] == "NULL_EQUALS_ZERO"

    # GET metric-level config to verify override
    res_get_metric = client.get("/api/bi-truth/metric-comparison/config?project_id=p1&metric_name=CLE")
    assert res_get_metric.status_code == 200
    metric_data = res_get_metric.json()
    assert metric_data["effective"]["tolerance_pct"] == 0.05
    assert metric_data["effective"]["null_policy"] == "NULL_EQUALS_ZERO"


def test_metric_comparison_health_and_metrics_endpoints(client):
    # GET health
    res_health = client.get("/api/bi-truth/metric-comparison/health")
    assert res_health.status_code == 200
    health_data = res_health.json()
    assert health_data["status"] in ("healthy", "degraded")
    assert health_data["service"] == "metric-comparator"
    assert "adapters" in health_data

    # GET metrics
    res_metrics = client.get("/api/bi-truth/metric-comparison/metrics?project_id=p1")
    assert res_metrics.status_code == 200
    metrics_data = res_metrics.json()
    assert metrics_data["project_id"] == "p1"
    assert "metrics" in metrics_data


def test_metric_comparison_history_endpoints(client):
    # Run a comparison first with alias fields
    run_payload = {
        "project_id": "history_proj",
        "metric_id": "Total Sales",
        "source_platform": "mock",
        "target_platform": "snowflake",
        "target_semantic_view": "DB.SCHEMA.VIEW",
        "source_config": {"fixtures_dir": "Tests/fixtures/baselines"},
        "tolerance_percentage": 0.02,
        "execution_mode": "MOCK",
    }
    resp = client.post("/api/bi-truth/metric-comparison/run", json=run_payload)
    assert resp.status_code == 200
    run_data = resp.json()
    run_id = run_data["run_id"]

    # GET history list
    res_hist = client.get("/api/bi-truth/metric-comparison/history?project_id=history_proj")
    assert res_hist.status_code == 200
    hist_data = res_hist.json()
    assert hist_data["count"] >= 1
    assert any(r["id"] == run_id for r in hist_data["runs"])

    # GET history by ID
    res_detail = client.get(f"/api/bi-truth/metric-comparison/history/{run_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["id"] == run_id
    assert detail["metric_name"] == "Total Sales"
    assert "generated_dax" in detail
    assert "generated_sql" in detail
