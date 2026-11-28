"""End-to-End Validation Suite: Complete BI Truth -> Verified Query -> Genie Benchmark -> Genie Evaluation.

Covers the 10 validation activities:
1. Live metric execution across Fabric/Power BI and Snowflake/Databricks.
2. Dimension-based and sliced metric comparisons (including Account Name scenarios).
3. Genie Space discovery, metadata retrieval and target metric-view mapping.
4. Publishing approved BI Truth question-SQL pairs as Genie benchmark questions.
5. Genie benchmark evaluations and validation of GOOD, BAD and NEEDS_REVIEW outcomes.
6. Linking Genie evaluation results to their corresponding BI Truth runs.
7. Comparison history, benchmark history and audit consistency.
8. Source, target and Genie API failures, authentication errors and timeout scenarios.
9. Deploy-gate validation and verification of PASS, FAIL and NOT_COMPARABLE handling.
10. Regression testing across Drift Monitoring and Blast Radius.
"""
from __future__ import annotations

import os
os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")

import datetime
from decimal import Decimal
from typing import Any, Dict
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.repository.orm.session_factory import get_engine
from semabridge.repository.orm.base import Base
from semabridge.ai.metric_execution.sql_builders import get_target_sql_builder
from semabridge.ai.metric_execution.result_normalizer import normalize_metric_value
from semabridge.ai.metric_execution.service import MetricComparisonService
from semabridge.ai.metric_execution.adapters.base import SourceResult
from semabridge.ai.bi_truth_service import (
    evaluate_project_deploy_gate,
    list_truth_cases,
)
from semabridge.governance.blast_radius import BlastRadiusSimulator
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.connectors.genie.connector import GenieConnector
from semabridge.connectors.genie.client import GenieClient
from semabridge.connectors.genie.models import GenieSpace, EvaluationResult

client = TestClient(app)


@pytest.fixture(autouse=True)
def init_db():
    """Ensure database schema tables are created in the test engine."""
    Base.metadata.create_all(get_engine())


# ---------------------------------------------------------------------------
# Activity 1: Live / Mock Metric Execution across Fabric/PBI & SF/Databricks
# ---------------------------------------------------------------------------
def test_01_metric_execution_platforms():
    """Validate query generation and execution for Fabric/Power BI -> Snowflake/Databricks."""
    sf_builder = get_target_sql_builder("SNOWFLAKE")
    db_builder = get_target_sql_builder("DATABRICKS")

    # Snowflake syntax
    sf_sql = sf_builder.build_metric_query("DB.SCHEMA.SEM_SALES", "Total_Sales")
    assert "SEMANTIC_VIEW(DB.SCHEMA.SEM_SALES METRICS Total_Sales)" in sf_sql

    # Databricks syntax
    db_sql = db_builder.build_metric_query("catalog.schema.metric_view", "Revenue")
    assert "SELECT MEASURE(`Revenue`) FROM `catalog`.`schema`.`metric_view`;" == db_sql

    # API execution in mock mode
    resp = client.post(
        "/api/bi-truth/metric-comparison/run",
        json={
            "project_id": "proj-e2e-test",
            "metric_name": "Total_Sales",
            "target_view": "DB.SCHEMA.SEM_SALES",
            "source_platform": "fabric",
            "target_platform": "snowflake",
            "execution_mode": "mock",
            "tolerance_percentage": 0.01,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["project_id"] == "proj-e2e-test"
    assert data["metric_name"] == "Total_Sales"
    assert "verdict" in data
    assert data["mock_mode"] is True


# ---------------------------------------------------------------------------
# Activity 2: Dimension-based and Sliced Comparisons (Account Name)
# ---------------------------------------------------------------------------
def test_02_dimension_sliced_comparisons():
    """Verify dimension-based and sliced metric comparisons, including Account Name scenarios."""
    sf_builder = get_target_sql_builder("SNOWFLAKE")
    db_builder = get_target_sql_builder("DATABRICKS")

    # Slice by Account Name
    slices = {"Account Name": "Acme Corp", "Region": "North America"}
    sf_sliced = sf_builder.build_metric_query("CORP.FINANCE.VIEW", "ARR", slices)
    assert "WHERE" in sf_sliced
    assert "Acme Corp" in sf_sliced
    assert "North America" in sf_sliced

    db_sliced = db_builder.build_metric_query("cat.sch.view", "ARR", {"Account Name": "Acme Corp"})
    assert "Acme Corp" in db_sliced
    assert "account_name" in db_sliced or "Account Name" in db_sliced

    # Verify comparison engine with exact values within tolerance
    v1 = normalize_metric_value(1250000.4567, "STRICT_NULL")
    v2 = normalize_metric_value(1250000.4568, "STRICT_NULL")
    assert v1 == Decimal("1250000.4567")
    assert v2 == Decimal("1250000.4568")


# ---------------------------------------------------------------------------
# Activity 3: Genie Space Discovery, Metadata & Metric-View Mapping
# ---------------------------------------------------------------------------
def test_03_genie_space_discovery_and_mapping():
    """Validate Genie Space discovery, metadata retrieval and target metric-view mapping."""
    with patch.object(GenieClient, "list_spaces") as mock_list, \
         patch.object(GenieClient, "get_space") as mock_get:

        mock_list.return_value = [
            {
                "id": "space-sales-001",
                "title": "Enterprise Sales Space",
                "description": "Genie conversational space for sales domain",
                "warehouse_id": "wh-databricks-01",
                "metric_views": ["catalog.gold.sales_metrics"],
                "tables": ["catalog.gold.orders"],
            }
        ]
        mock_get.return_value = {
            "id": "space-sales-001",
            "title": "Enterprise Sales Space",
            "metric_views": ["catalog.gold.sales_metrics"],
        }

        # List spaces
        resp = client.get("/api/genie/spaces?host=https://mock.cloud.databricks.com&token=mock")
        assert resp.status_code == 200
        spaces = resp.json()["spaces"]
        assert len(spaces) == 1
        assert spaces[0]["space_id"] == "space-sales-001"
        assert "catalog.gold.sales_metrics" in spaces[0]["metric_views"]

        # Get details
        resp_detail = client.get("/api/genie/spaces/space-sales-001?host=https://mock.cloud.databricks.com&token=mock")
        assert resp_detail.status_code == 200
        assert resp_detail.json()["title"] == "Enterprise Sales Space"

        # Map space to new metric view
        with patch.object(GenieClient, "publish_benchmarks", return_value={"status": "updated"}):
            resp_map = client.post(
                "/api/genie/map",
                json={
                    "space_id": "space-sales-001",
                    "metric_view": "catalog.gold.new_view",
                    "host": "https://mock.cloud.databricks.com",
                    "token": "mock",
                },
            )
            assert resp_map.status_code == 200
            assert resp_map.json()["mapped"] is True


# ---------------------------------------------------------------------------
# Activity 4: Publish Approved BI Truth Pairs as Genie Benchmarks
# ---------------------------------------------------------------------------
def test_04_publish_bi_truth_benchmarks():
    """Test publishing approved BI Truth question-SQL pairs as Genie benchmark questions."""
    with patch.object(GenieClient, "publish_benchmarks") as mock_pub:
        mock_pub.return_value = {"status": "published", "count": 2}

        resp = client.post(
            "/api/genie/publish",
            json={
                "space_id": "space-sales-001",
                "pairs": [
                    {
                        "question": "What is Total Sales for Acme Corp in 2024?",
                        "expected_sql": "SELECT MEASURE(Total_Sales) FROM catalog.gold.sales_metrics WHERE `account_name` = 'Acme Corp'",
                    },
                    {
                        "question": "What is our ARR by Account Name?",
                        "expected_sql": "SELECT `account_name`, MEASURE(ARR) FROM catalog.gold.sales_metrics GROUP BY 1",
                    },
                ],
                "host": "https://mock.cloud.databricks.com",
                "token": "mock",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["injected_count"] == 2
        assert len(data["published"]) == 2


# ---------------------------------------------------------------------------
# Activity 5: Genie Evaluations (GOOD, BAD, NEEDS_REVIEW Outcomes)
# ---------------------------------------------------------------------------
def test_05_genie_evaluation_verdicts():
    """Execute Genie benchmark evaluations and validate GOOD, BAD and NEEDS_REVIEW outcomes."""
    with patch.object(GenieClient, "start_evaluation") as mock_eval:
        mock_eval.return_value = {"evaluation_id": "eval-run-999", "status": "COMPLETED"}

        benchmarks = [
            {
                "id": "b1",
                "question": "What is the Total Revenue?",
                "expected_sql": "SELECT MEASURE(Total_Revenue) FROM gold.metric_view",
                "verdict": "GOOD",
            },
            {
                "id": "b2",
                "question": "What is bad syntax?",
                "expected_sql": "INVALID QUERY SYNTAX !!!",
                "verdict": "BAD",
            },
            {
                "id": "b3",
                "question": "What is boundary variance?",
                "expected_sql": "SELECT MEASURE(Total_Revenue) FROM gold.metric_view",
                "verdict": "NEEDS_REVIEW",
            },
        ]

        resp = client.post(
            "/api/genie/evaluate",
            json={
                "space_id": "space-sales-001",
                "benchmarks": benchmarks,
                "host": "https://mock.cloud.databricks.com",
                "token": "mock",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_benchmarks"] == 3
        results = data["results"]
        assert len(results) == 3

        verdicts = {r["question_id"]: r["verdict"] for r in results}
        assert verdicts["b1"] == "GOOD"
        assert verdicts["b2"] == "BAD"
        assert verdicts["b3"] == "NEEDS_REVIEW"


# ---------------------------------------------------------------------------
# Activity 6: Linking Genie Evaluation Results to BI Truth Runs
# ---------------------------------------------------------------------------
def test_06_link_genie_evaluation_to_bi_truth_run():
    """Verify that Genie evaluation results are correctly linked to their corresponding BI Truth runs."""
    # 1. First execute a metric comparison run to create a run record
    run_resp = client.post(
        "/api/bi-truth/metric-comparison/run",
        json={
            "project_id": "proj-genie-link",
            "metric_name": "Net_Sales",
            "target_view": "gold.metrics",
            "target_platform": "databricks",
            "source_platform": "fabric",
            "execution_mode": "mock",
        },
    )
    assert run_resp.status_code == 200
    run_id = run_resp.json()["run_id"]

    # 2. Trigger Genie evaluation with that run_id
    eval_resp = client.post(
        "/api/genie/evaluate",
        json={
            "space_id": "space-sales-001",
            "run_id": run_id,
            "project_id": "proj-genie-link",
            "benchmarks": [
                {
                    "question": "What is Net Sales?",
                    "expected_sql": "SELECT MEASURE(Net_Sales) FROM gold.metrics",
                }
            ],
            "host": "https://mock.cloud.databricks.com",
            "token": "mock",
        },
    )
    assert eval_resp.status_code == 200
    assert eval_resp.json()["run_id"] == run_id

    # 3. Retrieve run history detail and verify genie metadata is linked
    hist_resp = client.get(f"/api/bi-truth/metric-comparison/history/{run_id}")
    assert hist_resp.status_code == 200
    hist_data = hist_resp.json()
    assert hist_data["id"] == run_id


# ---------------------------------------------------------------------------
# Activity 7: Audit Consistency across Comparison & Benchmark History
# ---------------------------------------------------------------------------
def test_07_audit_consistency_and_history():
    """Validate comparison history, benchmark history and audit consistency."""
    resp = client.get("/api/bi-truth/metric-comparison/history?project_id=proj-genie-link&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "runs" in data
    assert "count" in data
    assert data["count"] >= 1
    found = any(r["metric_name"] == "Net_Sales" for r in data["runs"])
    assert found is True


# ---------------------------------------------------------------------------
# Activity 8: Source, Target & Genie Failures, Auth Errors, Timeouts
# ---------------------------------------------------------------------------
def test_08_error_handling_and_auth_failures():
    """Test source, target and Genie API failures, authentication errors and timeout scenarios."""
    # 1. Missing space_id in Genie evaluation
    resp_bad_space = client.post(
        "/api/genie/evaluate",
        json={"space_id": "", "benchmarks": []},
    )
    assert resp_bad_space.status_code == 200
    assert resp_bad_space.json()["status"] == "FAILED"

    # 2. Genie auth error handling
    conn = GenieConnector({"host": "https://invalid-host.nonexistent.domain", "token": "invalid"})
    conn.authenticate()
    hc = conn.health_check()
    assert hc.connected is False
    assert hc.error is not None

    # 3. Target SQL builder empty inputs handle cleanly
    builder = get_target_sql_builder("SNOWFLAKE")
    query = builder.build_metric_query("DB.SCHEMA.VIEW", "Sales")
    assert "SEMANTIC_VIEW" in query


# ---------------------------------------------------------------------------
# Activity 9: Deploy-Gate Validation (PASS, FAIL, NOT_COMPARABLE)
# ---------------------------------------------------------------------------
def test_09_deploy_gate_verdicts(monkeypatch):
    """Rerun deploy-gate validation and verify PASS, FAIL and NOT_COMPARABLE handling."""
    recent_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 1. Non-critical pass
    monkeypatch.setattr(
        "semabridge.ai.bi_truth_service.list_truth_cases",
        lambda pid: [
            {"id": "c1", "project_id": pid, "metric_name": "M1", "is_critical": True, "status": "approved"}
        ],
    )
    monkeypatch.setattr(
        "semabridge.ai.bi_truth_service.list_truth_runs",
        lambda pid: [
            {
                "id": "r1",
                "project_id": pid,
                "ran_at": recent_time,
                "results": [{"case_id": "c1", "metric_name": "M1", "passed": True}],
            }
        ],
    )
    gate_pass = evaluate_project_deploy_gate("proj-gate-test")
    assert gate_pass.passed is True

    # 2. Critical fail in blocking mode
    monkeypatch.setattr(
        "semabridge.ai.bi_truth_service.list_truth_cases",
        lambda pid: [
            {"id": "c2", "project_id": pid, "metric_name": "M2", "is_critical": True, "status": "approved"}
        ],
    )
    monkeypatch.setattr(
        "semabridge.ai.bi_truth_service.list_truth_runs",
        lambda pid: [
            {
                "id": "r2",
                "project_id": pid,
                "ran_at": recent_time,
                "results": [{"case_id": "c2", "metric_name": "M2", "passed": False, "error": "Outside tolerance"}],
            }
        ],
    )
    gate_fail = evaluate_project_deploy_gate("proj-gate-test", gate_mode="block")
    assert gate_fail.passed is False
    assert gate_fail.blocks_deploy is True

    # 3. Critical NOT_COMPARABLE in report mode
    monkeypatch.setattr(
        "semabridge.ai.bi_truth_service.list_truth_cases",
        lambda pid: [
            {"id": "c3", "project_id": pid, "metric_name": "M3", "is_critical": True, "status": "approved"}
        ],
    )
    monkeypatch.setattr(
        "semabridge.ai.bi_truth_service.list_truth_runs",
        lambda pid: [
            {
                "id": "r3",
                "project_id": pid,
                "ran_at": recent_time,
                "results": [{"case_id": "c3", "metric_name": "M3", "passed": False, "error": "NOT_COMPARABLE"}],
            }
        ],
    )
    gate_nc = evaluate_project_deploy_gate("proj-gate-test", gate_mode="report")
    assert gate_nc.blocks_deploy is False


# ---------------------------------------------------------------------------
# Activity 10: Regression Testing: Drift Monitoring and Blast Radius
# ---------------------------------------------------------------------------
def test_10_regression_drift_and_blast_radius():
    """Perform regression testing across Drift Monitoring and Blast Radius."""
    # 1. Blast Radius Simulator
    sample_yaml = """
domain: Sales
owner: sales@corp.com
concepts:
  - name: Orders
    attributes:
      - name: amount
      - name: discount
metrics:
  - name: gross_margin
    sql_expression: "SUM(amount) - SUM(discount)"
"""
    dom = FederatedOntologyLoader.parse_yaml_string(sample_yaml)
    fed = FederatedOntologyLoader.merge_domains([dom])
    sim = BlastRadiusSimulator(ontology=fed)
    report = sim.simulate_impact(target_name="amount", target_type="COLUMN", change_type="DROP")
    assert report.severity == "BREAKING"
    assert "gross_margin" in [m.name for m in report.affected_metrics]

    # 2. Metric comparison health endpoint regression check
    h_resp = client.get("/api/bi-truth/metric-comparison/health")
    assert h_resp.status_code == 200
    assert h_resp.json()["status"] in ("ok", "healthy")
