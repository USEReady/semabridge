"""Offline unit & API tests for Databricks Drift Monitoring and Semantic Layer target-awareness."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.api.routers import drift_router


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(drift_router.router)
    return TestClient(app)


def _fake_dbx_config(
    host: str = "adb-123456.azuredatabricks.net",
    token: str = "dapi-fake-token",
    catalog: str = "analytics_cat",
    schema_name: str = "semantic_sch",
):
    return SimpleNamespace(
        host=host,
        token=token,
        access_token=None,
        client_id=None,
        client_secret=None,
        catalog=catalog,
        schema_name=schema_name,
    )


def _fake_expected_views(
    view_name: str = "sales_metric_view",
    columns: Dict[str, str] | None = None,
) -> Dict[str, Dict[str, Any]]:
    cols = columns or {
        "TOTAL_REVENUE": "SUM(revenue)",
        "TOTAL_UNITS": "SUM(units)",
    }
    return {
        view_name.lower(): {
            "view_name": view_name,
            "domain": "Sales",
            "domain_id": "dom-sales",
            "model_id": "snap-1",
            "columns": {
                k.upper(): {"name": k, "expression": v, "kind": "metric"}
                for k, v in cols.items()
            },
        }
    }


def _fake_response(status_code: int = 200, payload: Dict[str, Any] | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    resp.text = text
    return resp


@pytest.fixture(autouse=True)
def _isolated_drift_state(tmp_path, monkeypatch):
    state_file = tmp_path / "drift_state.json"
    monkeypatch.setenv("SEMABRIDGE_DRIFT_STATE_PATH", str(state_file))
    # Clear any in-DB drift events for test projects
    drift_router.clear_drift_events()
    yield
    drift_router.clear_drift_events()


def test_databricks_drift_check_no_drift(monkeypatch):
    captured: Dict[str, Any] = {}

    def fake_get(url, headers=None, params=None, timeout=None, verify=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["verify"] = verify
        return _fake_response(
            200,
            {
                "tables": [
                    {
                        "name": "sales_metric_view",
                        "full_name": "analytics_cat.semantic_sch.sales_metric_view",
                        "table_type": "METRIC_VIEW",
                        "columns": [
                            {"name": "TOTAL_REVENUE", "type_text": "DOUBLE"},
                            {"name": "TOTAL_UNITS", "type_text": "BIGINT"},
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(drift_router, "_resolve_project_target_type", lambda pid, explicit=None: "databricks")
    monkeypatch.setattr(drift_router, "_extract_expected_databricks_views", lambda pid, domain=None: _fake_expected_views())
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda pid, conn, owner_id=None: _fake_dbx_config())
    monkeypatch.setattr("semabridge.utils.tls.requests_verify", lambda ctx="": True)
    monkeypatch.setattr("requests.get", fake_get)

    client = _build_client()
    res = client.post("/api/drift/check", json={"project_id": "proj-dbx-clean"})
    assert res.status_code == 200
    body = res.json()

    assert body["target_type"] == "databricks"
    assert body["databricks_connected"] is True
    assert body["snowflake_connected"] is False
    assert body["catalog"] == "analytics_cat"
    assert body["schema"] == "semantic_sch"
    assert body["drift_detected"] == 0
    assert body["events"] == []
    assert captured["url"] == "https://adb-123456.azuredatabricks.net/api/2.1/unity-catalog/tables"
    assert captured["params"] == {"catalog_name": "analytics_cat", "schema_name": "semantic_sch"}
    assert captured["headers"] == {"Authorization": "Bearer dapi-fake-token"}
    assert captured["verify"] is True


def test_databricks_drift_check_added_and_removed_columns(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None, verify=None):
        return _fake_response(
            200,
            {
                "tables": [
                    {
                        "name": "sales_metric_view",
                        "full_name": "analytics_cat.semantic_sch.sales_metric_view",
                        "table_type": "VIEW",
                        "columns": [
                            {"name": "TOTAL_REVENUE", "type_text": "DOUBLE"},
                            {"name": "ADHOC_EXTRA_COL", "type_text": "STRING"},
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(drift_router, "_resolve_project_target_type", lambda pid, explicit=None: "databricks")
    monkeypatch.setattr(
        drift_router,
        "_extract_expected_databricks_views",
        lambda pid, domain=None: _fake_expected_views(
            columns={"TOTAL_REVENUE": "SUM(revenue)", "MARGIN_PCT": "SUM(margin)/SUM(revenue)"}
        ),
    )
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda pid, conn, owner_id=None: _fake_dbx_config())
    monkeypatch.setattr("semabridge.utils.tls.requests_verify", lambda ctx="": True)
    monkeypatch.setattr("requests.get", fake_get)

    client = _build_client()
    res = client.post("/api/drift/check", json={"project_id": "proj-dbx-cols"})
    assert res.status_code == 200
    body = res.json()

    assert body["target_type"] == "databricks"
    assert body["databricks_connected"] is True
    assert body["drift_detected"] == 2
    by_metric = {e["metric_name"]: e for e in body["events"]}
    assert set(by_metric.keys()) == {"MARGIN_PCT", "ADHOC_EXTRA_COL"}
    assert by_metric["MARGIN_PCT"]["drift_type"] == "MISSING_IN_DATABRICKS"
    assert "COLUMN_REMOVED" in by_metric["MARGIN_PCT"]["badges"]
    assert by_metric["MARGIN_PCT"]["target_type"] == "databricks"
    assert by_metric["ADHOC_EXTRA_COL"]["drift_type"] == "EXTRA_IN_DATABRICKS"
    assert "COLUMN_ADDED" in by_metric["ADHOC_EXTRA_COL"]["badges"]
    assert by_metric["ADHOC_EXTRA_COL"]["target_type"] == "databricks"

    # Verify events are persisted and returned by GET /api/drift/events
    ev_res = client.get("/api/drift/events", params={"project_id": "proj-dbx-cols"})
    assert ev_res.status_code == 200
    assert ev_res.json()["count"] == 2


def test_databricks_drift_check_missing_view(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None, verify=None):
        return _fake_response(200, {"tables": []})

    monkeypatch.setattr(drift_router, "_resolve_project_target_type", lambda pid, explicit=None: "databricks")
    monkeypatch.setattr(drift_router, "_extract_expected_databricks_views", lambda pid, domain=None: _fake_expected_views())
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda pid, conn, owner_id=None: _fake_dbx_config())
    monkeypatch.setattr("semabridge.utils.tls.requests_verify", lambda ctx="": True)
    monkeypatch.setattr("requests.get", fake_get)

    client = _build_client()
    res = client.post("/api/drift/check", json={"project_id": "proj-dbx-missing"})
    assert res.status_code == 200
    body = res.json()

    assert body["target_type"] == "databricks"
    assert body["databricks_connected"] is True
    assert body["drift_detected"] == 1
    ev = body["events"][0]
    assert ev["drift_type"] == "MISSING_IN_DATABRICKS"
    assert "VIEW_MISSING" in ev["badges"]
    assert ev["semantic_view"] == "analytics_cat.semantic_sch.sales_metric_view"
    assert ev["target_type"] == "databricks"


def test_databricks_drift_check_offline_graceful_errors(monkeypatch):
    monkeypatch.setattr(drift_router, "_resolve_project_target_type", lambda pid, explicit=None: "databricks")
    monkeypatch.setattr(drift_router, "_extract_expected_databricks_views", lambda pid, domain=None: _fake_expected_views())
    monkeypatch.setattr("semabridge.utils.tls.requests_verify", lambda ctx="": True)

    client = _build_client()

    # Case 1: Missing Databricks credentials/config
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda pid, conn, owner_id=None: None)
    res1 = client.post("/api/drift/check", json={"project_id": "proj-dbx-nocreds"})
    assert res1.status_code == 200
    b1 = res1.json()
    assert b1["target_type"] == "databricks"
    assert b1["databricks_connected"] is False
    assert "No Databricks host/token" in b1["error"]

    # Case 2: Unity Catalog REST endpoint returns HTTP 503
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda pid, conn, owner_id=None: _fake_dbx_config())
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: _fake_response(503, text="Service Unavailable"),
    )
    res2 = client.post("/api/drift/check", json={"project_id": "proj-dbx-503"})
    assert res2.status_code == 200
    b2 = res2.json()
    assert b2["target_type"] == "databricks"
    assert b2["databricks_connected"] is False
    assert "HTTP 503" in b2["error"]

    # Case 3: Network connection failure
    def _boom(*args, **kwargs):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr("requests.get", _boom)
    res3 = client.post("/api/drift/check", json={"project_id": "proj-dbx-neterr"})
    assert res3.status_code == 200
    b3 = res3.json()
    assert b3["target_type"] == "databricks"
    assert b3["databricks_connected"] is False
    assert "network unreachable" in b3["error"]


def test_resolve_project_target_type_sources(monkeypatch):
    # Explicit override
    assert drift_router._resolve_project_target_type("p1", explicit="databricks") == "databricks"

    # From project YAML helper
    monkeypatch.setattr(
        "semabridge.api.services.mapping_service._compat_project_target_type",
        lambda pid: "databricks" if pid == "proj-yaml-dbx" else "",
    )
    assert drift_router._resolve_project_target_type("proj-yaml-dbx") == "databricks"

    # Fallback to project metadata
    monkeypatch.setattr("semabridge.api.services.project_shared._compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(
        "semabridge.api.services.project_shared._compat_projects",
        {"proj-meta-dbx": {"target_type": "databricks"}},
    )
    assert drift_router._resolve_project_target_type("proj-meta-dbx") == "databricks"

    # Default to snowflake when unknown
    assert drift_router._resolve_project_target_type("proj-unknown") == "snowflake"
