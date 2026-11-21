import os
import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.reconciliation.golden_verifier import GoldenQueryVerifier, VerificationResult
from semabridge.reconciliation.mock_parity import (
    generate_mock_comparative_data,
    is_demo_flag_active,
    is_mock_parity_enabled,
)


def test_mock_parity_flag_off_returns_no_rows_for_any_project(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "false")
    assert is_mock_parity_enabled() is False

    sql_rows, dax_rows = generate_mock_comparative_data(
        metric_name="COUNT_OF_BU",
        dimensions=["BU.REGION"],
        target_table_or_view="SEMABRIDGE.SEMAE",
        project_id="proj-hr",
    )
    assert sql_rows == []
    assert dax_rows == []


def test_mock_parity_no_generic_segment_fallback_for_real_projects(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "true")
    monkeypatch.delenv("SEMABRIDGE_DEMO_MODE", raising=False)
    monkeypatch.delenv("SEMABRIDGE_DEMO_PROJECT_ID", raising=False)

    # For a real project with unknown dimension (not MANUF/DATE/REGION/SEGMENT),
    # it must NOT fall back to generic "Segment Alpha..." mock rows
    sql_rows, dax_rows = generate_mock_comparative_data(
        metric_name="CUSTOM_METRIC",
        dimensions=["CUSTOM_TABLE.UNKNOWN_DIM"],
        target_table_or_view="SEMABRIDGE.SEMAE",
        project_id="proj-hr",
    )
    assert sql_rows == []
    assert dax_rows == []

    # But for a demo project, fallback is permitted
    monkeypatch.setenv("SEMABRIDGE_DEMO_PROJECT_ID", "demo-proj")
    assert is_demo_flag_active("demo-proj") is True
    d_sql_rows, d_dax_rows = generate_mock_comparative_data(
        metric_name="CUSTOM_METRIC",
        dimensions=["CUSTOM_TABLE.UNKNOWN_DIM"],
        target_table_or_view="SEMABRIDGE.SEMAE",
        project_id="demo-proj",
    )
    assert len(d_sql_rows) == 5
    assert len(d_dax_rows) == 5
    assert d_sql_rows[0]["CUSTOM_TABLE.UNKNOWN_DIM"] == "Segment Alpha"


def test_reconcile_data_no_dax_rows_returns_pbi_values_needed():
    verifier = GoldenQueryVerifier()
    sql_rows = [{"DIM": "A", "M": 100}, {"DIM": "B", "M": 200}]
    res = verifier.reconcile_data(
        metric_name="M",
        dax_rows=[],
        sql_rows=sql_rows,
        dimension_keys=["DIM"],
    )
    assert res.status == "PBI_VALUES_NEEDED"
    assert res.matched_rows == 0
    assert res.certification_badge == "Power BI values needed"
    assert res.message is not None
    assert "Power BI values needed" in res.message


def test_verify_endpoint_missing_pbi_returns_pbi_values_needed():
    client = TestClient(app)
    body = {
        "sql_results": [{"DIM": "A", "M": 100}],
        "dax_results": [],
        "metric_name": "M",
        "dimensions": ["DIM"],
    }
    res = client.post("/api/reconciliation/verify", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "PBI_VALUES_NEEDED"
    assert data["certification_badge"] == "Power BI values needed"
    assert "Power BI values needed" in (data.get("message") or "")


def test_mock_certify_never_certifies_and_labels_simulated(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_MOCK_PARITY", "true")

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
    assert verification["status"] == "SIMULATED"
    assert verification["certification_badge"] == "SIMULATED"
    # Never certify
    assert data.get("certification") is None


def test_update_metric_sql_rejects_self_referencing_expression():
    client = TestClient(app)

    # 1. Bare metric name
    res = client.post(
        "/api/reconciliation/update-metric-sql",
        json={
            "metric_name": "COUNT_OF_BU",
            "sql_expression": "COUNT_OF_BU",
            "project_id": "proj-hr",
        },
    )
    assert res.status_code == 400
    assert "self-referencing" in res.json().get("detail", "").lower()

    # 2. Concept-qualified metric name
    res2 = client.post(
        "/api/reconciliation/update-metric-sql",
        json={
            "metric_name": "COUNT_OF_BU",
            "sql_expression": "BU.COUNT_OF_BU",
            "project_id": "proj-hr",
        },
    )
    assert res2.status_code == 400
    assert "self-referencing" in res2.json().get("detail", "").lower()

    # 3. Valid calculation with functions / operators is NOT rejected by self-reference check
    res3 = client.post(
        "/api/reconciliation/update-metric-sql",
        json={
            "metric_name": "NON_EXISTENT_METRIC",
            "sql_expression": "COUNT(BU.BU)",
            "project_id": "proj-hr",
        },
    )
    assert res3.status_code in (400, 404)
    assert "self-referencing" not in res3.json().get("detail", "").lower()
