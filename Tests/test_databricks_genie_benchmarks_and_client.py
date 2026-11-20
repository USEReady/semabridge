"""Offline tests for Genie Space Benchmarks, Evaluation Runs, Manual-Edit Guard,
and unified DatabricksRestClient routing across Genie, Drift, and Ontology routers.

All HTTP calls are intercepted by fakes; zero live Databricks calls occur.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.ai.databricks_genie_service import (
    build_genie_space_spec,
    extract_genie_checksum,
    generate_genie_space,
    run_genie_evaluation,
)
from semabridge.ai.project_connection import ProjectScope
from semabridge.api.routers import domain_ontology_router, drift_router
from semabridge.api.routers.ai_router import router as ai_router
from semabridge.api.services.databricks_connection_service import DatabricksRestClient
from semabridge.ontology.models import (
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainOntology,
)


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: dict | None = None,
        text: str = "",
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)
        self.content = self.text.encode("utf-8")
        self.headers = headers or {}

    def json(self):
        return self._payload


def _sample_domain_and_scope():
    dom = DomainOntology(
        id="dom-sales",
        domain="Sales",
        source_project_id="proj-dbx",
        semantic_view_name="main.analytics.sales_metrics",
        concepts=[
            DomainConcept(
                name="Orders",
                domain="Sales",
                attributes=[
                    DomainAttribute(name="Region", data_type="VARCHAR"),
                ],
            )
        ],
        metrics=[
            DomainMetric(
                name="Total Revenue",
                logical_name="total_revenue",
                label="Total Revenue",
                domain="Sales",
                concept="Orders",
                description="Sum of order revenue",
                aggregation_type="SUM",
                sql_expression="SUM(Amount)",
                semantic_view_name="main.analytics.sales_metrics",
            )
        ],
    )
    scope = ProjectScope(
        project_id="proj-dbx",
        semantic_view="main.analytics.sales_metrics",
        model_name="SalesModel",
        allowed_domains=["Sales"],
        domain_names=["Sales"],
        domain_views={"Sales": "main.analytics.sales_metrics"},
    )
    return dom, scope


def _fake_dbx_cfg(token: str = "dapi-super-secret-token-9999"):
    return SimpleNamespace(
        host="adb-test-123.azuredatabricks.net",
        auth_type="pat",
        token=token,
        access_token=None,
        client_id=None,
        client_secret=None,
        warehouse_id="wh-eval-01",
        catalog="analytics_cat",
        schema_name="semantic_sch",
    )


# ---------------------------------------------------------------------------
# 1. Genie Space Benchmarks + Deterministic Checksum
# ---------------------------------------------------------------------------


def test_genie_spec_includes_benchmarks_and_deterministic_checksum():
    dom, scope = _sample_domain_and_scope()
    truth_cases = [
        {
            "id": "truth-1",
            "project_id": "proj-dbx",
            "question": "What is total revenue by region?",
            "expected_sql": "SELECT `Region`, MEASURE(`total_revenue`) FROM `main`.`analytics`.`sales_metrics` GROUP BY `Region`",
            "status": "approved",
        },
        {
            "id": "truth-2",
            "project_id": "proj-dbx",
            "question": "Rejected question",
            "expected_sql": "SELECT 1",
            "status": "rejected",
        },
    ]
    truth_runs = [
        {
            "run_id": "run-1",
            "project_id": "proj-dbx",
            "results": [
                {
                    "case_id": "truth-1",
                    "question": "What is total revenue by region?",
                    "expected_answer": "West: 4200.50",
                    "passed": True,
                }
            ],
        }
    ]
    verified_queries = [
        {
            "id": "vq-1",
            "name": "Total Revenue",
            "question": "What is overall revenue?",
            "sql": "SELECT MEASURE(`total_revenue`) FROM `main`.`analytics`.`sales_metrics`",
        },
        # Duplicate question should not overwrite the BI Truth benchmark
        {
            "id": "vq-2",
            "name": "Duplicate Region",
            "question": "What is total revenue by region?",
            "sql": "SELECT 1",
        },
    ]

    with (
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom]),
        patch("semabridge.ai.verified_queries_service.list_verified_queries_for_project", return_value=verified_queries),
        patch("semabridge.ai.bi_truth_service.list_truth_cases", return_value=truth_cases),
        patch("semabridge.ai.bi_truth_service.list_truth_runs", return_value=truth_runs),
    ):
        spec1 = build_genie_space_spec("proj-dbx", space_title="Sales Genie")
        spec2 = build_genie_space_spec("proj-dbx", space_title="Sales Genie")

    assert spec1["semabridge_checksum"] == spec2["semabridge_checksum"]
    assert len(spec1["semabridge_checksum"]) == 12
    assert f"[semabridge-checksum:{spec1['semabridge_checksum']}]" in spec1["description"]
    assert extract_genie_checksum(spec1["description"]) == spec1["semabridge_checksum"]

    benchmarks = spec1["benchmarks"]
    assert len(benchmarks) == 2
    assert benchmarks[0] == {
        "question": "What is total revenue by region?",
        "sql": "SELECT `Region`, MEASURE(`total_revenue`) FROM `main`.`analytics`.`sales_metrics` GROUP BY `Region`",
        "expected_answer": "West: 4200.50",
        "source": "bi_truth",
    }
    assert benchmarks[1] == {
        "question": "What is overall revenue?",
        "sql": "SELECT MEASURE(`total_revenue`) FROM `main`.`analytics`.`sales_metrics`",
        "expected_answer": None,
        "source": "verified_query",
    }


# ---------------------------------------------------------------------------
# 2. Manual-Edit Guard on Genie Space Updates + force_overwrite
# ---------------------------------------------------------------------------


def test_genie_manual_edit_conflict_and_force_overwrite():
    dom, scope = _sample_domain_and_scope()
    db_cfg = _fake_dbx_cfg()
    calls: list[dict] = []

    def _fake_get(url, headers=None, params=None, timeout=None, verify=None):
        calls.append({"method": "GET", "url": url})
        return _FakeResponse(
            200,
            {
                "space_id": "sp-existing-42",
                "description": "Human edited this Genie space directly in Databricks UI",
            },
        )

    def _fake_patch(url, headers=None, json=None, timeout=None, verify=None, **kwargs):
        calls.append({"method": "PATCH", "url": url, "json": json})
        return _FakeResponse(200, {"space_id": "sp-existing-42"})

    app = FastAPI()
    app.include_router(ai_router)
    client = TestClient(app)

    with (
        patch("semabridge.api.routers.ai_router._require_project_access"),
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom]),
        patch("semabridge.ai.verified_queries_service.list_verified_queries_for_project", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_cases", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_runs", return_value=[]),
        patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value=db_cfg),
        patch("semabridge.ai.databricks_genie_service.requests_verify", return_value=True),
        patch("semabridge.ai.databricks_genie_service.requests.get", side_effect=_fake_get),
        patch("semabridge.ai.databricks_genie_service.requests.patch", side_effect=_fake_patch),
    ):
        # First attempt without force_overwrite -> conflict_manual_edit, NO PATCH
        resp_conflict = client.post(
            "/api/ai/databricks/genie-space",
            json={
                "project_id": "proj-dbx",
                "space_title": "Sales Genie",
                "deploy": True,
                "space_id": "sp-existing-42",
                "force_overwrite": False,
            },
        )
        assert resp_conflict.status_code == 200
        body = resp_conflict.json()
        assert body["status"] == "conflict_manual_edit"
        assert body["space_id"] == "sp-existing-42"
        assert "force_overwrite=true" in body["message"]
        assert "spec" in body
        assert [c["method"] for c in calls] == ["GET"]
        assert calls[0]["url"] == "https://adb-test-123.azuredatabricks.net/api/2.0/genie/spaces/sp-existing-42"

        # Second attempt with force_overwrite=True -> PATCH succeeds
        resp_force = client.post(
            "/api/ai/databricks/genie-space",
            json={
                "project_id": "proj-dbx",
                "space_title": "Sales Genie",
                "deploy": True,
                "space_id": "sp-existing-42",
                "force_overwrite": True,
            },
        )
        assert resp_force.status_code == 200
        forced = resp_force.json()
        assert forced["status"] == "deployed"
        assert forced["space_id"] == "sp-existing-42"
        assert [c["method"] for c in calls] == ["GET", "PATCH"]
        assert calls[1]["url"] == "https://adb-test-123.azuredatabricks.net/api/2.0/genie/spaces/sp-existing-42"


def test_genie_update_succeeds_without_force_when_checksum_matches():
    dom, scope = _sample_domain_and_scope()
    db_cfg = _fake_dbx_cfg()

    with (
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom]),
        patch("semabridge.ai.verified_queries_service.list_verified_queries_for_project", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_cases", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_runs", return_value=[]),
    ):
        expected_spec = build_genie_space_spec("proj-dbx", space_title="Sales Genie")

    methods: list[str] = []

    def _fake_get(url, headers=None, params=None, timeout=None, verify=None):
        methods.append("GET")
        return _FakeResponse(
            200,
            {
                "space_id": "sp-match-1",
                "description": expected_spec["description"],
            },
        )

    def _fake_patch(url, headers=None, json=None, timeout=None, verify=None, **kwargs):
        methods.append("PATCH")
        return _FakeResponse(200, {"space_id": "sp-match-1"})

    with (
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom]),
        patch("semabridge.ai.verified_queries_service.list_verified_queries_for_project", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_cases", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_runs", return_value=[]),
        patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value=db_cfg),
        patch("semabridge.ai.databricks_genie_service.requests_verify", return_value=True),
        patch("semabridge.ai.databricks_genie_service.requests.get", side_effect=_fake_get),
        patch("semabridge.ai.databricks_genie_service.requests.patch", side_effect=_fake_patch),
    ):
        res = generate_genie_space(
            "proj-dbx",
            space_title="Sales Genie",
            deploy=True,
            space_id="sp-match-1",
            force_overwrite=False,
        )

    assert res["status"] == "deployed"
    assert res["space_id"] == "sp-match-1"
    assert methods == ["GET", "PATCH"]


# ---------------------------------------------------------------------------
# 3. Genie Space Evaluation Runner + Endpoint
# ---------------------------------------------------------------------------


def test_genie_evaluation_endpoint_and_runner():
    dom, scope = _sample_domain_and_scope()
    db_cfg = _fake_dbx_cfg()
    posted: list[dict] = []

    def _fake_post(url, headers=None, json=None, timeout=None, verify=None, **kwargs):
        posted.append({"url": url, "json": json, "headers": headers, "verify": verify})
        return _FakeResponse(
            200,
            {
                "status": "completed",
                "eval_run_id": "eval-run-888",
                "results": [
                    {"question": "What is total revenue?", "passed": True, "status": "PASSED"},
                    {"question": "What is revenue by region?", "passed": False, "status": "FAILED"},
                ],
            },
        )

    app = FastAPI()
    app.include_router(ai_router)
    client = TestClient(app)

    with (
        patch("semabridge.api.routers.ai_router._require_project_access"),
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom]),
        patch("semabridge.ai.verified_queries_service.list_verified_queries_for_project", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_cases", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_runs", return_value=[]),
        patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value=db_cfg),
        patch("semabridge.ai.databricks_genie_service.requests_verify", return_value="/etc/ssl/ca.pem"),
        patch("semabridge.ai.databricks_genie_service.requests.post", side_effect=_fake_post),
    ):
        resp = client.post(
            "/api/ai/databricks/genie-space/evaluate",
            json={"project_id": "proj-dbx", "space_id": "sp-eval-77"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["space_id"] == "sp-eval-77"
    assert body["eval_run_id"] == "eval-run-888"
    assert body["total_benchmarks"] == 2
    assert body["passed"] == 1
    assert body["score_pct"] == 50.0
    assert len(body["results"]) == 2
    assert len(posted) == 1
    assert posted[0]["url"] == "https://adb-test-123.azuredatabricks.net/api/2.0/genie/spaces/sp-eval-77/benchmarks/eval"
    assert posted[0]["verify"] == "/etc/ssl/ca.pem"


# ---------------------------------------------------------------------------
# 4. Unified DatabricksRestClient usage in Genie, Drift, and Ontology routers
# ---------------------------------------------------------------------------


def test_genie_uses_databricks_rest_client_and_redacts_secrets():
    dom, scope = _sample_domain_and_scope()
    secret_tok = "dapi-very-secret-token-abc123"
    db_cfg = _fake_dbx_cfg(token=secret_tok)

    def _fake_401_post(url, headers=None, json=None, timeout=None, verify=None, **kwargs):
        return _FakeResponse(401, text=f"Unauthorized for token {secret_tok}")

    with (
        patch("semabridge.ai.project_connection.resolve_project_scope", return_value=scope),
        patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom]),
        patch("semabridge.ai.verified_queries_service.list_verified_queries_for_project", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_cases", return_value=[]),
        patch("semabridge.ai.bi_truth_service.list_truth_runs", return_value=[]),
        patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value=db_cfg),
        patch("semabridge.ai.databricks_genie_service.requests_verify", return_value=True),
        patch("semabridge.ai.databricks_genie_service.requests.post", side_effect=_fake_401_post),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            run_genie_evaluation("proj-dbx", "sp-1")

    err_msg = str(exc_info.value)
    assert "401" in err_msg
    assert "Authentication failed" in err_msg
    assert secret_tok not in err_msg
    assert "[REDACTED]" in err_msg


def test_drift_router_routes_unity_catalog_through_databricks_rest_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DRIFT_STATE_PATH", str(tmp_path / "drift_state.json"))
    drift_router.clear_drift_events()

    used_clients: list[DatabricksRestClient] = []
    orig_list_tables = DatabricksRestClient.list_tables

    def _spy_list_tables(self, catalog_name, schema_name, **kwargs):
        used_clients.append(self)
        return orig_list_tables(self, catalog_name, schema_name, **kwargs)

    def _fake_get(url, headers=None, params=None, timeout=None, verify=None):
        return _FakeResponse(
            200,
            {
                "tables": [
                    {
                        "name": "sales_mv",
                        "full_name": "analytics_cat.semantic_sch.sales_mv",
                        "table_type": "METRIC_VIEW",
                        "columns": [{"name": "TOTAL_REVENUE", "type_text": "DOUBLE"}],
                    }
                ]
            },
        )

    monkeypatch.setattr(DatabricksRestClient, "list_tables", _spy_list_tables)
    monkeypatch.setattr(
        drift_router,
        "_extract_expected_databricks_views",
        lambda pid, domain=None: {
            "sales_mv": {
                "view_name": "sales_mv",
                "domain": "Sales",
                "domain_id": "dom-sales",
                "model_id": "snap-1",
                "columns": {"TOTAL_REVENUE": {"name": "TOTAL_REVENUE", "expression": "SUM(rev)", "kind": "metric"}},
            }
        },
    )
    monkeypatch.setattr(
        "semabridge.ai.project_connection.resolve_project_warehouse_config",
        lambda pid, conn, owner_id=None: _fake_dbx_cfg(),
    )
    monkeypatch.setattr("semabridge.utils.tls.requests_verify", lambda ctx="": True)
    monkeypatch.setattr("requests.get", _fake_get)

    res = drift_router._run_databricks_drift_check(project_id="proj-dbx")
    assert res["databricks_connected"] is True
    assert res["drift_detected"] == 0
    assert len(used_clients) == 1
    assert isinstance(used_clients[0], DatabricksRestClient)


def test_domain_ontology_router_routes_unity_catalog_through_databricks_rest_client(monkeypatch):
    client_calls: list[str] = []
    orig_list_catalogs = DatabricksRestClient.list_catalogs
    orig_list_schemas = DatabricksRestClient.list_schemas
    orig_list_tables = DatabricksRestClient.list_tables

    def _spy_catalogs(self, **kwargs):
        client_calls.append("list_catalogs")
        return orig_list_catalogs(self, **kwargs)

    def _spy_schemas(self, catalog_name, **kwargs):
        client_calls.append(f"list_schemas:{catalog_name}")
        return orig_list_schemas(self, catalog_name, **kwargs)

    def _spy_tables(self, catalog_name, schema_name, **kwargs):
        client_calls.append(f"list_tables:{catalog_name}.{schema_name}")
        return orig_list_tables(self, catalog_name, schema_name, **kwargs)

    def _fake_get(url, headers=None, params=None, timeout=None, verify=None):
        if url.endswith("/api/2.1/unity-catalog/catalogs"):
            return _FakeResponse(200, {"catalogs": [{"name": "corp_cat"}]})
        if url.endswith("/api/2.1/unity-catalog/schemas"):
            return _FakeResponse(200, {"schemas": [{"name": "bi_sch"}]})
        if url.endswith("/api/2.1/unity-catalog/tables"):
            return _FakeResponse(
                200,
                {
                    "tables": [
                        {
                            "name": "sales_mv",
                            "full_name": "corp_cat.bi_sch.sales_mv",
                            "table_type": "METRIC_VIEW",
                            "columns": [{"name": "Total Revenue", "comment": "SUM(revenue)"}],
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(DatabricksRestClient, "list_catalogs", _spy_catalogs)
    monkeypatch.setattr(DatabricksRestClient, "list_schemas", _spy_schemas)
    monkeypatch.setattr(DatabricksRestClient, "list_tables", _spy_tables)
    monkeypatch.setattr(domain_ontology_router, "_review_access", lambda req, pid: None)
    monkeypatch.setattr(
        "semabridge.ai.project_connection.resolve_project_warehouse_config",
        lambda pid, conn, owner_id=None: SimpleNamespace(
            host="adb-ont.azuredatabricks.net",
            token="dapi-ont-token",
            catalog="",
            schema_name="",
        ),
    )
    monkeypatch.setattr(
        "semabridge.repository.credential_manager.CredentialManager.get_credentials",
        lambda self, provider, user_id=0: {},
    )
    monkeypatch.setattr("semabridge.utils.tls.requests_verify", lambda *args, **kwargs: True)
    monkeypatch.setattr("requests.get", _fake_get)

    app = FastAPI()
    app.include_router(domain_ontology_router.review_router)
    client = TestClient(app)

    resp = client.post(
        "/api/ontology/review/snowflake-similarity",
        json={
            "project_id": "proj-dbx",
            "target_type": "databricks",
            "metrics": [{"name": "Total Revenue", "label": "Total Revenue", "expression": "SUM(revenue)"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["views_scanned"] == 1
    assert body["matches"][0]["status"] == "EXACT_MATCH"
    assert body["matches"][0]["matched_view"] == "corp_cat.bi_sch.sales_mv"
    assert client_calls == [
        "list_catalogs",
        "list_schemas:corp_cat",
        "list_tables:corp_cat.bi_sch",
    ]
