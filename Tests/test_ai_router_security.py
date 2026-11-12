"""Tests for /api/ai/execute-query SQL security guardrails and verified query enforcement."""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.api.routers.ai_router import (
    clear_verified_executions,
    is_query_verified_by_execution,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_verified_executions():
    clear_verified_executions()
    yield
    clear_verified_executions()


# ---------------------------------------------------------------------------
# 1. /api/ai/execute-query SQL Security Guardrail Tests
# ---------------------------------------------------------------------------


def test_select_passes_validation_and_reaches_connection_check():
    """Read-only SELECT statement passes security guard."""
    query = "SELECT total_sales, customer_region FROM sales_semantic_view WHERE fiscal_year = 2024"
    resp = client.post("/api/ai/execute-query", json={"sql": query, "target": "LOCAL"})
    # Guard allowed the SELECT statement; local engine returns no warehouse connection
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["is_simulated"] is False
    assert "not executed: no warehouse connection" in data["message"]
    assert data["rows"] == []
    assert data["columns"] == []


def test_with_cte_select_passes_validation():
    """CTE WITH ... SELECT statement passes security guard."""
    query = "WITH regional_cte AS (SELECT region, SUM(amount) AS tot FROM sales GROUP BY region) SELECT * FROM regional_cte"
    resp = client.post("/api/ai/execute-query", json={"sql": query, "target": "LOCAL"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["is_simulated"] is False
    assert "not executed: no warehouse connection" in data["message"]


@pytest.mark.parametrize(
    "forbidden_sql,expected_keyword",
    [
        ("CREATE TABLE x (a int)", "CREATE"),
        ("DELETE FROM customers WHERE id = 1", "DELETE"),
        ("INSERT INTO sales (amount) VALUES (100)", "INSERT"),
        ("UPDATE customers SET status = 'churned'", "UPDATE"),
        ("DROP TABLE transactions", "DROP"),
        ("MERGE INTO target_table USING source_table ON target_table.id = source_table.id WHEN MATCHED THEN UPDATE SET target_table.val = source_table.val", "MERGE"),
        ("CALL execute_maintenance_procedure()", "CALL"),
        ("SHOW TABLES", "SHOW"),
        ("ALTER TABLE users ADD COLUMN is_admin BOOLEAN", "ALTER"),
        ("TRUNCATE TABLE logs", "TRUNCATE"),
    ],
)
def test_forbidden_sql_statements_rejected_by_guard(forbidden_sql, expected_keyword):
    """Non-read-only statements are rejected with HTTP 400 and never sent to warehouse."""
    resp = client.post("/api/ai/execute-query", json={"sql": forbidden_sql, "target": "LOCAL"})
    assert resp.status_code == 400
    error_msg = resp.json()["detail"]
    assert "SQL execution guard blocked" in error_msg or "Only read-only SELECT" in error_msg


def test_multi_statement_with_forbidden_clause_rejected():
    """Multiple statements containing a forbidden statement are rejected entirely."""
    query = "SELECT * FROM sales; DROP TABLE sales;"
    resp = client.post("/api/ai/execute-query", json={"sql": query, "target": "LOCAL"})
    assert resp.status_code == 400
    error_msg = resp.json()["detail"]
    assert "SQL execution guard blocked" in error_msg or "Only read-only SELECT" in error_msg


# ---------------------------------------------------------------------------
# 2. Simulated Preview Removed & Verified Query Enforcement Tests
# ---------------------------------------------------------------------------


def test_simulated_preview_removed_on_offline_warehouse():
    """When external warehouse is offline or not configured, return clear error instead of fake rows."""
    query = "SELECT total_revenue FROM revenue_view"
    resp = client.post("/api/ai/execute-query", json={"sql": query, "target": "SNOWFLAKE"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["is_simulated"] is False
    assert data["columns"] == []
    assert data["rows"] == []
    assert data["row_count"] == 0
    assert "not executed: no warehouse connection" in data["error"]
    assert data["message"] == "not executed: no warehouse connection"


def test_save_verified_query_rejected_without_real_execution():
    """Saving a verified query without prior successful warehouse execution is rejected with HTTP 400."""
    unexecuted_sql = "SELECT total_revenue FROM revenue_view"
    resp = client.post(
        "/api/ai/save-verified-query",
        json={
            "project_id": "proj-ai-security-test",
            "domain": "Finance",
            "question": "What is total revenue?",
            "sql": unexecuted_sql,
            "target": "SNOWFLAKE",
            "confirmed": True,
        },
    )
    assert resp.status_code == 400
    assert "no successful execution record found in database" in resp.json()["detail"]


def test_save_verified_query_allowed_after_successful_real_execution():
    """Saving a verified query succeeds once executed successfully against a real warehouse."""
    target_sql = "SELECT total_revenue, region FROM sales_view WHERE fiscal_year = 2024"

    # Mock Snowflake connector live query execution
    fake_cursor = MagicMock()
    fake_cursor.description = [("TOTAL_REVENUE", None), ("REGION", None)]
    fake_cursor.fetchall.return_value = [[500000.0, "North America"], [250000.0, "EMEA"]]

    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test_acc", "user": "test_user", "password": "test_password"}), \
         patch("snowflake.connector.connect", return_value=fake_conn):

        exec_resp = client.post(
            "/api/ai/execute-query",
            json={"project_id": "proj-ai-security-test", "sql": target_sql, "target": "SNOWFLAKE"},
        )
        assert exec_resp.status_code == 200
        exec_data = exec_resp.json()
        assert exec_data["success"] is True
        assert exec_data["is_simulated"] is False
        assert exec_data["row_count"] == 2

    # Verify query is now marked as verified
    assert is_query_verified_by_execution(target_sql, project_id="proj-ai-security-test")

    # Now saving to verified queries registry succeeds
    save_resp = client.post(
        "/api/ai/save-verified-query",
        json={
            "project_id": "proj-ai-security-test",
            "domain": "Finance",
            "question": "What is total revenue by region?",
            "sql": target_sql,
            "target": "SNOWFLAKE",
            "confirmed": True,
        },
    )
    assert save_resp.status_code == 200
    save_data = save_resp.json()
    assert save_data["success"] is True
    assert "successfully registered" in save_data["message"] or "saved for project" in save_data["message"]
