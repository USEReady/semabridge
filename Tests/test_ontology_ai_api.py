"""Tests for Domain Ontology and Embedded AI FastAPI Endpoints."""

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app

client = TestClient(app)


@pytest.fixture
def seeded_store(tmp_path):
    """Temporarily load demo domains into the live singleton (in memory, tmp storage)."""
    from semabridge.ontology.store import ontology_store

    orig_domains, orig_dir = ontology_store.domains, ontology_store.storage_dir
    ontology_store.domains, ontology_store.storage_dir = {}, tmp_path
    ontology_store.seed_demo_domains()
    try:
        yield ontology_store
    finally:
        ontology_store.domains, ontology_store.storage_dir = orig_domains, orig_dir
        ontology_store._refresh_index()


def test_store_empty_by_default_and_opt_in_seed(tmp_path):
    from semabridge.ontology.store import DomainOntologyStore

    assert DomainOntologyStore(storage_dir=tmp_path).domains == {}
    seeded = DomainOntologyStore(storage_dir=tmp_path, seed_demo=True)
    assert set(seeded.domains) == {"Finance", "Sales"}


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


def test_api_ai_search(seeded_store):
    response = client.post("/api/ai/search", json={"query": "gross margin", "limit": 5})
    assert response.status_code == 200
    hits = response.json()
    assert isinstance(hits, list)
    assert len(hits) > 0
    assert hits[0]["name"] == "gross_margin"


def test_api_ai_nl_query(seeded_store):
    # 1. Governed query generated when semantic view is specified (offline draft)
    response = client.post(
        "/api/ai/nl-query",
        json={
            "prompt": "Show me gross margin",
            "dialect": "SNOWFLAKE",
            "target": "LOCAL",
            "view_name": "FINANCE_VIEW",
        },
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["generated_sql"] is not None
    assert "SELECT" in plan["generated_sql"]
    assert "SEMANTIC_VIEW(FINANCE_VIEW" in plan["generated_sql"]
    assert plan["confidence_score"] > 0
    assert "Offline Draft - Local Engine" in plan["explanation"]

    # 2. When no primary entity or semantic view is known, generated_sql is None with clear explanation
    resp_unbound = client.post(
        "/api/ai/nl-query",
        json={"prompt": "Show me gross margin by customer", "dialect": "SNOWFLAKE", "target": "LOCAL"},
    )
    assert resp_unbound.status_code == 200
    plan_unbound = resp_unbound.json()
    assert plan_unbound["generated_sql"] is None
    assert "No primary entity found" in plan_unbound["explanation"]
