"""Tests for Automated Mathematical Data Reconciliation Engine."""

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.reconciliation.golden_verifier import (
    GoldenQueryVerifier,
    VerificationTolerance,
)

client = TestClient(app)


def test_dax_and_sql_query_generation():
    verifier = GoldenQueryVerifier()
    dax = verifier.generate_dax_query(
        metric_name="GrossMargin",
        dimensions=["Customer.Region", "Date.Year"],
        filters=["'Date'[Year] = 2024"],
    )
    assert "SUMMARIZECOLUMNS" in dax
    assert "'Customer'[Region]" in dax
    assert "'Date'[Year]" in dax
    assert '"GrossMargin", [GrossMargin]' in dax

    sql = verifier.generate_sql_query(
        metric_name="GrossMargin",
        metric_sql_expression="SUM(sales) - SUM(cogs)",
        target_table_or_view="PROD_DWH.ANALYTICS.SALES_METRIC_VIEW",
        dimensions=["Customer.Region", "Date.Year"],
        filters=["Year = 2024"],
    )
    assert "SELECT" in sql
    assert "Region" in sql
    assert "SUM(sales) - SUM(cogs) AS GrossMargin" in sql
    assert "GROUP BY 1, 2" in sql


def test_reconciliation_exact_match():
    verifier = GoldenQueryVerifier()

    dax_data = [
        {"Region": "East", "Year": 2024, "GrossMargin": 150000.50},
        {"Region": "West", "Year": 2024, "GrossMargin": 230000.75},
    ]
    sql_data = [
        {"Region": "East", "Year": 2024, "GrossMargin": 150000.50},
        {"Region": "West", "Year": 2024, "GrossMargin": 230000.75},
    ]

    res = verifier.reconcile_data(
        metric_name="GrossMargin",
        dax_rows=dax_data,
        sql_rows=sql_data,
        dimension_keys=["Region", "Year"],
    )

    assert res.status == "CERTIFIED"
    assert res.matched_rows == 2
    assert res.mismatched_rows == 0
    assert res.max_absolute_delta == 0.0
    assert "Certified 100% Match" in res.certification_badge


def test_reconciliation_mismatch_detection():
    verifier = GoldenQueryVerifier(
        default_tolerance=VerificationTolerance(absolute_tolerance=0.01, max_mismatch_percentage=0.0)
    )

    dax_data = [
        {"Region": "East", "Year": 2024, "GrossMargin": 150000.50},
        {"Region": "West", "Year": 2024, "GrossMargin": 230000.75},
    ]
    sql_data = [
        {"Region": "East", "Year": 2024, "GrossMargin": 150000.50},
        {"Region": "West", "Year": 2024, "GrossMargin": 210000.00},  # Mismatch by $20,000.75
    ]

    res = verifier.reconcile_data(
        metric_name="GrossMargin",
        dax_rows=dax_data,
        sql_rows=sql_data,
        dimension_keys=["Region", "Year"],
    )

    assert res.status == "FAILED"
    assert res.matched_rows == 1
    assert res.mismatched_rows == 1
    assert res.max_absolute_delta > 20000.0
    assert len(res.sample_mismatches) == 1
    assert res.sample_mismatches[0].dimension_key == "West | 2024"


def test_api_reconciliation_endpoints():
    response = client.post(
        "/api/reconciliation/generate-queries",
        json={
            "metric_name": "Revenue",
            "metric_sql_expression": "SUM(amount)",
            "target_table_or_view": "FACT_SALES",
            "dimensions": ["Region"],
        },
    )
    assert response.status_code == 200
    queries = response.json()
    assert "dax_query" in queries
    assert "sql_query" in queries

    verify_resp = client.post(
        "/api/reconciliation/verify",
        json={
            "metric_name": "Revenue",
            "dax_results": [{"Region": "EMEA", "Revenue": 5000.0}],
            "sql_results": [{"Region": "EMEA", "Revenue": 5000.0}],
            "dimension_keys": ["Region"],
        },
    )
    assert verify_resp.status_code == 200
    res_data = verify_resp.json()
    assert res_data["status"] == "CERTIFIED"
