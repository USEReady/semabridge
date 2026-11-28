"""Comprehensive unit and integration tests for Databricks Genie Space Connector."""
import os
os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.connectors.factory import make_source_extractor, make_target_emitter
from semabridge.connectors.genie.benchmark_publisher import GenieBenchmarkPublisher
from semabridge.connectors.genie.client import GenieClient
from semabridge.connectors.genie.connector import GenieConnector
from semabridge.connectors.genie.evaluation import GenieEvaluationEngine
from semabridge.connectors.genie.models import GenieSpace, PublishResult, GenieHealthCheck
from semabridge.connectors.genie.space_discovery import SpaceDiscoveryService
from semabridge.connectors.genie.space_manager import GenieSpaceManager


@pytest.fixture
def mock_client():
    client = GenieClient(host="https://mock-workspace.cloud.databricks.com", token="mock-token")
    return client


def test_genie_models():
    data = {
        "id": "space_123",
        "title": "Finance Analytics Space",
        "description": "Genie space for finance metric views",
        "warehouse_id": "wh_456",
        "metric_views": ["catalog.schema.finance_metrics"],
        "tables": ["catalog.schema.orders"],
    }
    space = GenieSpace.from_api_dict(data)
    assert space.space_id == "space_123"
    assert space.title == "Finance Analytics Space"
    assert space.warehouse_id == "wh_456"
    assert "catalog.schema.finance_metrics" in space.metric_views
    assert "catalog.schema.orders" in space.tables


def test_space_discovery(mock_client):
    service = SpaceDiscoveryService(mock_client)
    sample_spaces = [
        {
            "id": "s1",
            "title": "Sales Space",
            "warehouse_id": "wh1",
            "metric_views": ["main.sales.sales_metrics"],
        },
        {
            "id": "s2",
            "title": "Marketing Space",
            "warehouse_id": "wh2",
            "metric_views": ["main.marketing.mkt_metrics"],
        },
    ]

    with patch.object(mock_client, "list_spaces", return_value=sample_spaces):
        spaces = service.list_spaces()
        assert len(spaces) == 2

        # Find matching space by metric view
        match = service.find_space_for_metric_view("sales_metrics")
        assert match is not None
        assert match.space_id == "s1"

        # Find matching space by warehouse
        match_wh = service.find_space_for_metric_view("other_view", warehouse_id="wh2")
        assert match_wh is not None
        assert match_wh.space_id == "s2"


def test_space_manager_mapping(mock_client):
    manager = GenieSpaceManager(mock_client)
    sample_space = {
        "id": "s1",
        "title": "Corp Space",
        "warehouse_id": "wh1",
        "metric_views": [],
    }

    with patch.object(mock_client, "get_space", return_value=sample_space):
        mapping = manager.map_space_to_metric_view("s1", "cat.sch.view")
        assert mapping["space_id"] == "s1"
        assert mapping["mapped"] is True
        assert "cat.sch.view" in mapping["all_metric_views"]


def test_benchmark_publisher(mock_client):
    publisher = GenieBenchmarkPublisher(mock_client)
    pairs = [
        {"case_id": "c1", "question": "Total Revenue", "sql": "SELECT MEASURE(rev) FROM v;"},
        {"case_id": "c2", "question": "Active Users", "sql": "SELECT MEASURE(users) FROM v;"},
    ]

    # Dry run test
    dry_res = publisher.publish_pairs("space_1", pairs, dry_run=True)
    assert dry_res.success is True
    assert dry_res.status == "dry_run"
    assert dry_res.injected_count == 2

    # Live publish test
    with patch.object(mock_client, "publish_benchmarks", return_value={"status": "OK"}):
        live_res = publisher.publish_pairs("space_1", pairs, dry_run=False)
        assert live_res.success is True
        assert live_res.status == "success"
        assert live_res.injected_count == 2


def test_genie_evaluation_engine(mock_client):
    engine = GenieEvaluationEngine(mock_client)
    benchmarks = [
        {"id": "b1", "question": "What is revenue?", "sql": "SELECT MEASURE(revenue) FROM view"},
        {"id": "b2", "question": "Invalid query", "sql": "INVALID SQL NOT SELECT"},
    ]

    with patch.object(mock_client, "start_evaluation", return_value={"evaluation_id": "eval_999"}):
        res = engine.run_evaluation("space_1", benchmarks)
        assert res.evaluation_id == "eval_999"
        assert res.total_benchmarks == 2
        assert res.passed_benchmarks == 1
        assert res.failed_benchmarks == 1
        assert res.accuracy_score == 50.0


def test_genie_connector_contract():
    config = {
        "host": "https://adb-mock.azuredatabricks.net",
        "token": "dapi12345",
    }
    connector = GenieConnector(config)
    connector.authenticate()

    with patch.object(
        connector._client,
        "list_spaces",
        return_value=[{"id": "sp1", "title": "Test Space", "warehouse_id": "wh1"}],
    ):
        discovery = connector.discover()
        assert discovery["platform"] == "GENIE"
        assert discovery["count"] == 1


def test_connector_factory_registration():
    config = {"host": "https://adb.net", "token": "tok"}
    src = make_source_extractor("genie", config)
    assert isinstance(src, GenieConnector)

    tgt = make_target_emitter("genie", config)
    assert isinstance(tgt, GenieConnector)


def test_genie_api_endpoints():
    client = TestClient(app)

    with patch(
        "semabridge.connectors.genie.connector.GenieConnector.health_check",
        return_value=GenieHealthCheck(connected=True, host="https://mock.net", spaces_count=3, has_write_scope=True, error=None, details={}),
    ):
        resp = client.get("/api/genie/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["spaces_count"] == 3

    with patch(
        "semabridge.connectors.genie.connector.GenieConnector.list_spaces",
        return_value=[GenieSpace(space_id="sp1", title="Mock Space")],
    ):
        resp_spaces = client.get("/api/genie/spaces")
        assert resp_spaces.status_code == 200
        assert resp_spaces.json()["count"] == 1

    with patch(
        "semabridge.connectors.genie.connector.GenieConnector.publish_benchmarks",
        return_value=PublishResult(success=True, space_id="sp1", injected_count=1, published=[], failed=[], skipped=[], reason="OK", status="success", published_at="2026-11-28"),
    ):
        pub_payload = {
            "space_id": "sp1",
            "pairs": [{"case_id": "c1", "question": "Q?", "sql": "SELECT 1"}],
            "dry_run": False,
        }
        resp_pub = client.post("/api/genie/publish", json=pub_payload)
        assert resp_pub.status_code == 200
        assert resp_pub.json()["success"] is True

        pub_payload2 = {
            "space_id": "sp1",
            "project_id": "proj-123",
            "questions": [{"question": "Q?", "expected_sql": "SELECT 1"}],
        }
        resp_pub2 = client.post("/api/genie/publish", json=pub_payload2)
        assert resp_pub2.status_code == 200
        assert resp_pub2.json()["success"] is True
