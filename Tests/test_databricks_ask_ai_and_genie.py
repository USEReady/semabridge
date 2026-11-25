"""Offline tests for Databricks Ask AI, Genie Space generation, and BI Truth verification.

All HTTP calls are intercepted by fakes; zero live Databricks/Snowflake calls occur.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.ai.bi_truth_service import (
    add_manual_truth_case,
    clear_truth_cases,
    run_truth_suite,
)
from semabridge.ai.databricks_genie_service import (
    generate_genie_space,
    resolve_databricks_token,
)
from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.project_connection import ProjectScope
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.api.routers.ai_router import (
    ExecuteQueryRequest,
    _execute_databricks,
    router as ai_router,
)
from semabridge.ontology.models import (
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    DomainPolicy,
    FederatedEnterpriseOntology,
)


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


def _build_sales_ontology(view_name: str = "main.analytics.sales_metrics") -> FederatedEnterpriseOntology:
    concept = DomainConcept(
        name="Orders",
        domain="Sales",
        description="Sales orders fact concept",
        attributes=[
            DomainAttribute(name="Region", data_type="VARCHAR", description="Sales region"),
            DomainAttribute(name="Order Date", data_type="DATE", description="Date of order"),
            DomainAttribute(name="Amount", data_type="DECIMAL", description="Order amount"),
        ],
    )
    metric = DomainMetric(
        name="Total Revenue",
        logical_name="total_revenue",
        label="Total Revenue",
        domain="Sales",
        concept="Orders",
        description="Sum of order revenue",
        aggregation_type="SUM",
        sql_expression="SUM(Amount)",
        semantic_view_name=view_name,
    )
    policy = DomainPolicy(
        name="Active Only",
        domain="Sales",
        applies_to=["Orders"],
        filter_expression="status = 'ACTIVE'",
    )
    dom = DomainOntology(
        id="dom-sales",
        domain="Sales",
        source_project_id="proj-dbx",
        semantic_view_name=view_name,
        concepts=[concept],
        metrics=[metric],
        policies=[policy],
    )
    fed = FederatedEnterpriseOntology(
        domains={"Sales": dom},
        concepts={"Orders": concept},
        metrics={"Total Revenue": metric},
        policies={"Active Only": policy},
    )
    return fed


# ---------------------------------------------------------------------------
# 1. NLQueryEngine: Databricks Unity Catalog Metric View SQL
# ---------------------------------------------------------------------------


def test_nl_query_engine_databricks_generates_measure_and_backtick_view():
    fed = _build_sales_ontology("main.analytics.sales_metrics")
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    engine = NLQueryEngine(idx, fed)

    plan = engine.resolve_query(
        "What is total revenue by region?",
        dialect="DATABRICKS",
        semantic_view="main.analytics.sales_metrics",
        allowed_domains=["Sales"],
    )

    assert plan.answerable is True
    assert plan.is_draft is False
    assert "SEMANTIC_VIEW(" not in plan.generated_sql
    assert "MEASURE(`total_revenue`)" in plan.generated_sql
    assert "FROM `main`.`analytics`.`sales_metrics`" in plan.generated_sql
    assert "GROUP BY `Region`" in plan.generated_sql
    assert "Databricks AI/BI" in plan.generated_sql
    assert "Databricks AI/BI" in plan.explanation
    assert "Snowflake Cortex" not in plan.generated_sql
    assert "Snowflake Cortex" not in plan.explanation


def test_nl_query_engine_databricks_discovers_view_from_domain_metadata():
    fed = _build_sales_ontology("corp_cat.bi_schema.revenue_mv")
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    engine = NLQueryEngine(idx, fed)

    plan = engine.resolve_query(
        "Show total revenue",
        dialect="DATABRICKS",
        allowed_domains=["Sales"],
    )

    assert plan.answerable is True
    assert "MEASURE(`total_revenue`)" in plan.generated_sql
    assert "FROM `corp_cat`.`bi_schema`.`revenue_mv`" in plan.generated_sql
    assert "SEMANTIC_VIEW(" not in plan.generated_sql


# ---------------------------------------------------------------------------
# 2. Ask AI _execute_databricks: requests_verify, warehouse auto-discovery, PAT/OAuth/SP
# ---------------------------------------------------------------------------


def test_execute_databricks_auto_discovers_warehouse_and_uses_requests_verify():
    db_cfg = SimpleNamespace(
        host="https://adb-12345.azuredatabricks.net/",
        auth_type="pat",
        token="dapi-secret-pat",
        access_token=None,
        client_id=None,
        client_secret=None,
        warehouse_id="",
    )
    captured: list[dict] = []

    def _fake_post(url, json=None, headers=None, timeout=None, verify=None, **kwargs):
        captured.append({"url": url, "json": json, "headers": headers, "verify": verify})
        return _FakeResponse(
            200,
            {
                "status": {"state": "SUCCEEDED"},
                "manifest": {"schema": {"columns": [{"name": "Region"}, {"name": "total_revenue"}]}},
                "result": {"data_array": [["West", "4200.50"], ["East", "3100.00"]]},
            },
        )

    req = ExecuteQueryRequest(
        sql="SELECT `Region`, MEASURE(`total_revenue`) FROM `main`.`analytics`.`sales_metrics` GROUP BY `Region`",
        target="DATABRICKS",
        project_id="proj-dbx",
    )

    with (
        patch("semabridge.api.routers.ai_router.resolve_project_warehouse_config", return_value=db_cfg),
        patch("semabridge.core.engine.deployment.databricks._auto_discover_warehouse_id", return_value="wh-auto-99") as mock_wh,
        patch("semabridge.utils.tls.requests_verify", return_value="/etc/ssl/custom-ca.pem"),
        patch("semabridge.ai.databricks_genie_service.requests_verify", return_value="/etc/ssl/custom-ca.pem"),
        patch("requests.post", side_effect=_fake_post),
        patch("semabridge.api.routers.ai_router._record"),
    ):
        res = _execute_databricks(req, req.sql, req.sql, user_id="u1", owner_id="1")

    assert res.success is True
    assert res.target == "DATABRICKS"
    assert res.columns == ["Region", "total_revenue"]
    assert res.row_count == 2
    assert mock_wh.called
    assert len(captured) == 1
    assert captured[0]["url"] == "https://adb-12345.azuredatabricks.net/api/2.0/sql/statements"
    assert captured[0]["json"]["warehouse_id"] == "wh-auto-99"
    assert captured[0]["headers"]["Authorization"] == "Bearer dapi-secret-pat"
    assert captured[0]["verify"] == "/etc/ssl/custom-ca.pem"


def test_resolve_databricks_token_supports_pat_oauth_and_service_principal():
    # PAT
    pat_cfg = SimpleNamespace(host="ws.databricks.com", auth_type="pat", token="pat-tok-1")
    assert resolve_databricks_token(pat_cfg, verify=True) == "pat-tok-1"

    # OAuth
    oauth_cfg = SimpleNamespace(host="ws.databricks.com", auth_type="oauth", token=None, access_token="oauth-tok-2")
    assert resolve_databricks_token(oauth_cfg, verify=True) == "oauth-tok-2"

    # Service Principal
    sp_cfg = SimpleNamespace(
        host="ws.databricks.com",
        auth_type="service_principal",
        token=None,
        access_token=None,
        client_id="sp-client",
        client_secret="sp-secret",
    )
    oidc_calls: list[dict] = []

    def _fake_oidc_post(url, data=None, auth=None, timeout=None, verify=None, **kwargs):
        oidc_calls.append({"url": url, "data": data, "auth": auth, "verify": verify})
        return _FakeResponse(200, {"access_token": "sp-minted-tok-3"})

    with patch("semabridge.ai.databricks_genie_service.requests.post", side_effect=_fake_oidc_post):
        tok = resolve_databricks_token(sp_cfg, verify="/tmp/ca.pem")

    assert tok == "sp-minted-tok-3"
    assert len(oidc_calls) == 1
    assert oidc_calls[0]["url"] == "https://ws.databricks.com/oidc/v1/token"
    assert oidc_calls[0]["auth"] == ("sp-client", "sp-secret")
    assert oidc_calls[0]["verify"] == "/tmp/ca.pem"


# ---------------------------------------------------------------------------
# 3. Genie Space service + POST /api/ai/databricks/genie-space
# ---------------------------------------------------------------------------


def test_genie_space_preview_makes_zero_network_calls():
    fed = _build_sales_ontology("main.analytics.sales_metrics")
    scope = ProjectScope(
        project_id="proj-dbx",
        semantic_view="main.analytics.sales_metrics",
        model_name="SalesModel",
        allowed_domains=["Sales"],
        domain_names=["Sales"],
        domain_views={"Sales": "main.analytics.sales_metrics"},
    )
    verified = [
        {
            "id": "vq-1",
            "name": "Revenue by Region",
            "question": "What is revenue by region?",
            "sql": "SELECT `Region`, MEASURE(`total_revenue`) FROM `main`.`analytics`.`sales_metrics` GROUP BY `Region`",
        }
    ]

    with (
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[fed.domains["Sales"]]),
        patch("semabridge.ai.verified_queries_service.list_verified_queries_for_project", return_value=verified),
        patch("requests.post", side_effect=AssertionError("Network POST must not be called in preview mode")),
        patch("requests.get", side_effect=AssertionError("Network GET must not be called in preview mode")),
    ):
        result = generate_genie_space("proj-dbx", space_title="Executive Sales Genie", deploy=False)

    assert result["status"] == "preview"
    assert result["space_id"] is None
    spec = result["spec"]
    assert spec["title"] == "Executive Sales Genie"
    assert spec["table_identifiers"] == ["main.analytics.sales_metrics"]
    assert "What is revenue by region?" in spec["sample_questions"]
    assert len(spec["instructions"]) >= 2
    assert any("MEASURE(`total_revenue`)" in s["sql"] for s in spec["sql_snippets"])


def test_genie_space_deploy_and_api_endpoint():
    fed = _build_sales_ontology("main.analytics.sales_metrics")
    scope = ProjectScope(
        project_id="proj-dbx",
        semantic_view="main.analytics.sales_metrics",
        model_name="SalesModel",
        allowed_domains=["Sales"],
        domain_names=["Sales"],
        domain_views={"Sales": "main.analytics.sales_metrics"},
    )
    db_cfg = SimpleNamespace(
        host="adb-999.azuredatabricks.net",
        auth_type="pat",
        token="dapi-genie-token",
        warehouse_id="",
    )
    posted: list[dict] = []

    def _fake_post(url, json=None, headers=None, timeout=None, verify=None, **kwargs):
        posted.append({"url": url, "json": json, "headers": headers, "verify": verify})
        return _FakeResponse(200, {"space_id": "genie-space-777"})

    app = FastAPI()
    app.include_router(ai_router)
    client = TestClient(app)

    with (
        patch("semabridge.api.routers.ai_router._require_project_access"),
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[fed.domains["Sales"]]),
        patch("semabridge.ai.verified_queries_service.list_verified_queries_for_project", return_value=[]),
        patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value=db_cfg),
        patch("semabridge.core.engine.deployment.databricks._auto_discover_warehouse_id", return_value="wh-genie-1"),
        patch("semabridge.ai.databricks_genie_service.requests_verify", return_value="/etc/ssl/ca.crt"),
        patch("semabridge.ai.databricks_genie_service.requests.post", side_effect=_fake_post),
    ):
        resp = client.post(
            "/api/ai/databricks/genie-space",
            json={"project_id": "proj-dbx", "space_title": "Sales Genie", "deploy": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "deployed"
    assert body["space_id"] == "genie-space-777"
    assert body["space_url"] == "https://adb-999.azuredatabricks.net/genie/rooms/genie-space-777"
    assert body["spec"]["table_identifiers"] == ["main.analytics.sales_metrics"]
    assert len(posted) == 1
    assert posted[0]["url"] == "https://adb-999.azuredatabricks.net/api/2.0/genie/spaces"
    assert posted[0]["json"]["warehouse_id"] == "wh-genie-1"
    assert posted[0]["verify"] == "/etc/ssl/ca.crt"


# ---------------------------------------------------------------------------
# 4. BI Truth / Golden Query Verifier for Databricks projects
# ---------------------------------------------------------------------------


def test_bi_truth_executes_against_databricks_sql_warehouse(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    clear_truth_cases("proj-dbx")

    add_manual_truth_case(
        project_id="proj-dbx",
        domain="Sales",
        question="What is total revenue by region?",
        metric_name="Total Revenue",
        expected_sql="SELECT `Region`, MEASURE(`total_revenue`) FROM `main`.`analytics`.`sales_metrics` GROUP BY `Region`",
        status="approved",
    )

    fed = _build_sales_ontology("main.analytics.sales_metrics")
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    scope = ProjectScope(
        project_id="proj-dbx",
        semantic_view="main.analytics.sales_metrics",
        model_name="SalesModel",
        allowed_domains=["Sales"],
        domain_names=["Sales"],
        domain_views={"Sales": "main.analytics.sales_metrics"},
    )
    db_cfg = SimpleNamespace(
        host="adb-truth.azuredatabricks.net",
        auth_type="pat",
        token="dapi-truth-token",
        warehouse_id="",
    )
    sql_calls: list[dict] = []

    def _fake_sql_post(url, json=None, headers=None, timeout=None, verify=None, **kwargs):
        sql_calls.append({"url": url, "json": json, "headers": headers, "verify": verify})
        return _FakeResponse(
            200,
            {
                "status": {"state": "SUCCEEDED"},
                "manifest": {"schema": {"columns": [{"name": "Region"}, {"name": "total_revenue"}]}},
                "result": {"data_array": [["North", "1500.00"]]},
            },
        )

    with (
        patch("semabridge.ai.project_connection._project_meta", return_value={"id": "proj-dbx", "target_type": "databricks"}),
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value=db_cfg),
        patch("semabridge.ontology.store.ontology_store.get_federated_ontology", return_value=fed),
        patch("semabridge.ontology.store.ontology_store.search_index", idx),
        patch("semabridge.core.engine.deployment.databricks._auto_discover_warehouse_id", return_value="wh-truth-42"),
        patch("semabridge.ai.databricks_genie_service.requests_verify", return_value=True),
        patch("semabridge.ai.databricks_genie_service.requests.post", side_effect=_fake_sql_post),
    ):
        run = run_truth_suite(project_id="proj-dbx", owner_id="1")

    # The SQL ran on the warehouse, but the case has no Power BI (DAX) query,
    # so nothing was compared: "unverified", never counted as a pass.
    assert run["total"] == 1
    assert run["passed"] == 0
    assert run["accuracy_pct"] is None
    assert run["verdicts"]["unverified"] == 1
    res = run["results"][0]
    assert res["passed"] is False
    assert res["verdict"] == "unverified"
    assert "no Power BI (DAX) query" in res["verdict_detail"]
    assert res["mode"] == "databricks_sql"
    assert res["target"] == "DATABRICKS"
    assert res["row_count"] == 1
    assert res["error"] is None
    assert "MEASURE(`total_revenue`)" in res["generated_sql"]
    assert len(sql_calls) == 1
    assert sql_calls[0]["url"] == "https://adb-truth.azuredatabricks.net/api/2.0/sql/statements"
    assert sql_calls[0]["json"]["warehouse_id"] == "wh-truth-42"
    assert sql_calls[0]["verify"] is True
