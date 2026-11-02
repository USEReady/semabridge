"""Tests for Domain Ontology and Embedded AI FastAPI Endpoints."""

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app

client = TestClient(app)


def test_api_list_domains():
    response = client.get("/api/ontology/domains")
    assert response.status_code == 200
    data = response.json()
    assert "domains" in data
    assert "total_concepts" in data
    assert "total_metrics" in data


def test_api_validate_yaml():
    valid_yaml = """
domain: TestDomain
concepts:
  - name: TestConcept
metrics:
  - name: test_metric
    sql_expression: "COUNT(*)"
"""
    response = client.post("/api/ontology/domains/validate", json={"yaml_content": valid_yaml})
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["concept_count"] == 1
    assert data["metric_count"] == 1


def test_api_validate_invalid_yaml():
    invalid_yaml = "domain: [unclosed brackets"
    response = client.post("/api/ontology/domains/validate", json={"yaml_content": invalid_yaml})
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert len(data["errors"]) > 0


def test_api_ai_search():
    response = client.post("/api/ai/search", json={"query": "gross margin", "limit": 5})
    assert response.status_code == 200
    hits = response.json()
    assert isinstance(hits, list)
    assert len(hits) > 0
    assert hits[0]["name"] == "gross_margin"


def test_api_ai_nl_query():
    response = client.post(
        "/api/ai/nl-query",
        json={"prompt": "Show me gross margin by customer", "dialect": "SNOWFLAKE"},
    )
    assert response.status_code == 200
    plan = response.json()
    assert "generated_sql" in plan
    assert "SELECT" in plan["generated_sql"]
    assert plan["confidence_score"] > 0
