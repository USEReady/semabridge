"""Tests verifying frontend-backend API contracts, permission checks, and safety rules for Ontology & AI."""

import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.ontology.store import ontology_store
from semabridge.ai.verified_queries_service import record_query_execution

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_demo_store(tmp_path):
    orig_domains, orig_dir = ontology_store.domains, ontology_store.storage_dir
    ontology_store.domains, ontology_store.storage_dir = {}, tmp_path
    ontology_store.seed_demo_domains()
    try:
        yield
    finally:
        ontology_store.domains, ontology_store.storage_dir = orig_domains, orig_dir
        ontology_store._refresh_index()


def test_nl_query_offline_draft_option():
    """1. NL-Query: When Snowflake Cortex is unavailable, fail closed unless allow_offline_draft=True is explicitly passed."""
    # When allow_offline_draft is False (default)
    res_closed = client.post(
        "/api/ai/nl-query",
        json={
            "prompt": "Show Total Revenue by Customer in 2024",
            "dialect": "SNOWFLAKE",
            "target": "SNOWFLAKE",
            "allow_offline_draft": False,
        },
    )
    assert res_closed.status_code == 200
    data_closed = res_closed.json()
    assert data_closed["generated_sql"] == ""
    assert "allow_offline_draft=true" in data_closed["explanation"].lower() or "cortex unavailable" in data_closed["explanation"].lower()

    # When allow_offline_draft is explicitly True
    res_draft = client.post(
        "/api/ai/nl-query",
        json={
            "prompt": "Show Total Revenue by Customer in 2024",
            "dialect": "SNOWFLAKE",
            "target": "SNOWFLAKE",
            "allow_offline_draft": True,
        },
    )
    assert res_draft.status_code == 200
    data_draft = res_draft.json()
    assert data_draft["generated_sql"] is not None
    assert "SELECT" in data_draft["generated_sql"].upper()
    assert "[Offline Draft - Local Engine]" in data_draft["explanation"]


def test_save_verified_query_requires_execution_and_confirmation():
    """2. Verified Query Save: Requires both successful execution against warehouse AND explicit confirmed=True."""
    sql = "SELECT SUM(REVENUE) AS TOTAL_REV FROM SALES"

    # A. Missing confirmed=True -> HTTP 400
    res_unconfirmed = client.post(
        "/api/ai/save-verified-query",
        json={
            "question": "Total Revenue",
            "sql": sql,
            "confirmed": False,
            "project_id": "test_proj",
        },
    )
    assert res_unconfirmed.status_code == 400
    assert "Explicit user confirmation is required" in res_unconfirmed.json()["detail"]

    # B. Confirmed=True, but query has not been executed -> HTTP 400
    res_unexecuted = client.post(
        "/api/ai/save-verified-query",
        json={
            "question": "Total Revenue",
            "sql": sql,
            "confirmed": True,
            "project_id": "test_proj",
        },
    )
    assert res_unexecuted.status_code == 400
    assert "must first be executed successfully" in res_unexecuted.json()["detail"]

    # C. Real execution recorded + Confirmed=True -> HTTP 200 Success
    record_query_execution(
        question="Total Revenue",
        sql=sql,
        view_name="SALES",
        project_id="test_proj",
        success=True,
        row_count=5,
    )

    res_ok = client.post(
        "/api/ai/save-verified-query",
        json={
            "question": "Total Revenue",
            "sql": sql,
            "confirmed": True,
            "project_id": "test_proj",
        },
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["success"] is True


def test_save_verified_query_owner_admin_permissions():
    """3. Verified Query Save: Denies non-owner / non-admin users with 403 Forbidden when auth is enabled."""
    sql = "SELECT COUNT(*) FROM EMPLOYEES"
    record_query_execution(
        question="Count Employees",
        sql=sql,
        view_name="EMPLOYEES",
        project_id="secure_proj",
        success=True,
        row_count=10,
    )

    with patch.dict(os.environ, {"AUTH_ENABLED": "true", "JWT_SECRET_KEY": "testsecret12345678901234567890"}):
        from semabridge.auth.tokens import create_access_token
        token = create_access_token({"sub": "user_2"})
        csrf_token = "test_csrf_token"
        with patch("semabridge.api.services.project_ownership_service.is_project_owned_by_user", return_value=False):
            res_forbidden = client.post(
                "/api/ai/save-verified-query",
                json={
                    "question": "Count Employees",
                    "sql": sql,
                    "confirmed": True,
                    "project_id": "secure_proj",
                },
                cookies={"csrf_token": csrf_token},
                headers={
                    "x-csrf-token": csrf_token,
                    "Authorization": f"Bearer {token}",
                },
            )
            assert res_forbidden.status_code == 403
            assert "Forbidden" in res_forbidden.json()["detail"]


def test_get_domain_yaml_compatibility_fields():
    """4. Single Domain Detail: Returns both 'yaml' and 'yaml_content' for backwards compatibility."""
    res = client.get("/api/ontology/domains/Finance")
    assert res.status_code == 200
    data = res.json()
    assert "yaml" in data
    assert "yaml_content" in data
    assert data["yaml"] == data["yaml_content"]
    assert "domain: Finance" in data["yaml"]


def test_tmdl_rls_translation_contract():
    """5. TMDL to Snowflake RLS: Translates [Region] = 'EMEA' into valid ROW ACCESS POLICY DDL."""
    res = client.post(
        "/api/security/rls/tmdl-to-snowflake",
        json={
            "role": {
                "name": "EMEA_Managers",
                "table_permissions": [
                    {
                        "table_name": "DIM_CUSTOMER",
                        "dax_filter_expression": '[Region] = "EMEA"',
                    }
                ],
            },
            "target_database": "ANALYTICS_DB",
            "target_schema": "SEMANTIC",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "translated"
    assert "CREATE OR REPLACE ROW ACCESS POLICY ANALYTICS_DB.SEMANTIC.RAP_DIM_CUSTOMER_EMEA_MANAGERS" in data["ddl"]
    assert "REGION = 'EMEA'" in data["ddl"]


def test_tmdl_rls_translation_compound_and_or():
    """6. RLS Translator: Supports AND (&&) / OR (||) combinations and rejects unsupported functions."""
    # A. Compound AND comparison
    res = client.post(
        "/api/security/rls/tmdl-to-snowflake",
        json={
            "role": {
                "name": "EMEA_Active",
                "table_permissions": [
                    {
                        "table_name": "DIM_CUSTOMER",
                        "dax_filter_expression": '[Region] = "EMEA" && [Status] = "ACTIVE"',
                    }
                ],
            },
            "target_database": "ANALYTICS_DB",
            "target_schema": "SEMANTIC",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "translated"
    assert "REGION VARCHAR, STATUS VARCHAR" in data["ddl"]
    assert "REGION = 'EMEA' AND STATUS = 'ACTIVE'" in data["ddl"]

    # B. Unsupported function fails closed -> 'not translated'
    res_unsupported = client.post(
        "/api/security/rls/tmdl-to-snowflake",
        json={
            "role": {
                "name": "User_Principal_Role",
                "table_permissions": [
                    {
                        "table_name": "DIM_CUSTOMER",
                        "dax_filter_expression": '[UserEmail] = USERPRINCIPALNAME()',
                    }
                ],
            },
            "target_database": "ANALYTICS_DB",
            "target_schema": "SEMANTIC",
        },
    )
    assert res_unsupported.status_code == 200
    data_unsupported = res_unsupported.json()
    assert data_unsupported["status"] == "not translated"
    assert "unsupported function" in data_unsupported["reason"].lower()


def test_save_verified_query_zero_rows_success():
    """7. Verified Query Save: Query execution with 0 rows succeeds and can be saved when confirmed=True."""
    sql = "SELECT SUM(REVENUE) AS TOTAL_REV FROM SALES WHERE 1=0"
    record_query_execution(
        question="Empty Sales Result",
        sql=sql,
        view_name="SALES",
        project_id="test_zero_row_proj",
        success=True,
        row_count=0,
    )

    # With confirmed=True and row_count=0, save succeeds
    res_ok = client.post(
        "/api/ai/save-verified-query",
        json={
            "question": "Empty Sales Result",
            "sql": sql,
            "confirmed": True,
            "project_id": "test_zero_row_proj",
        },
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["success"] is True
