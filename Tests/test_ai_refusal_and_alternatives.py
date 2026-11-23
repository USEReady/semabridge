from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import (
    CardinalityType,
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    DomainRelationship,
)
from semabridge.api.main import app

client = TestClient(app)


def test_fan_out_refusal_structured_response():
    # Dimension table BU, fact table Employee. BU has metric "BU_COUNT", Employee has "EMP_COUNT"
    # BU -> Employee is 1-to-many (fan-out)
    dom = DomainOntology(
        domain="test_fan_out",
        source_project_id="proj-test",
        concepts=[
            DomainConcept(
                name="BU",
                attributes=[DomainAttribute(name="BU", data_type="VARCHAR", is_primary_key=True)],
            ),
            DomainConcept(
                name="Employee",
                attributes=[
                    DomainAttribute(name="EmpId", data_type="INTEGER", is_primary_key=True),
                    DomainAttribute(name="AgeGroup", data_type="VARCHAR"),
                    DomainAttribute(name="BU", data_type="VARCHAR"),
                ],
            ),
        ],
        relationships=[
            DomainRelationship(
                name="r_bu_emp",
                from_concept="BU",
                to_concept="Employee",
                cardinality=CardinalityType.ONE_TO_MANY,
                join_expression="BU.BU = Employee.BU",
            ),
            DomainRelationship(
                name="r_emp_bu",
                from_concept="Employee",
                to_concept="BU",
                cardinality=CardinalityType.MANY_TO_ONE,
                join_expression="Employee.BU = BU.BU",
            ),
        ],
        metrics=[
            DomainMetric(
                name="BU_COUNT",
                label="Count of BU",
                concept="BU",
                sql_expression="COUNT(BU.BU)",
            ),
            DomainMetric(
                name="EMP_COUNT",
                label="Count of Employee",
                concept="Employee",
                sql_expression="COUNT(Employee.EmpId)",
            ),
        ],
    )

    fed = FederatedOntologyLoader.merge_domains([dom])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    engine = NLQueryEngine(idx, fed)
    plan = engine.resolve_query("What is Count of BU by AgeGroup?")

    assert plan.refused is True
    assert plan.reason_code == "FAN_OUT"
    assert plan.sql is None
    assert not plan.generated_sql
    assert plan.engine == "local"
    assert plan.guard_checks == []
    assert len(plan.alternatives) > 0
    # The alternative should propose EMP_COUNT because Employee reaches AgeGroup and BU safely
    alt_questions = [a["question"] for a in plan.alternatives]
    assert any("Employee" in q for q in alt_questions)
    assert not any("Count of BU" in q for q in alt_questions)


def test_execute_query_comment_only_sql_raises_422():
    comment_sql = """-- SemaBridge AI Query Refusal
-- Refusal reason: FAN_OUT
-- Safe alternatives: Count of Employee by AgeGroup
"""
    res = client.post("/api/ai/execute-query", json={
        "sql": comment_sql,
        "target": "LOCAL",
    })
    assert res.status_code == 422
    data = res.json()
    detail = data.get("detail", {})
    if isinstance(detail, dict):
        assert detail.get("code") == "NO_EXECUTABLE_SQL"
    else:
        assert "NO_EXECUTABLE_SQL" in str(detail) or "cannot be empty" in str(detail).lower()


def test_hr_golden_bu_by_agegroup_refusal():
    hr_path = Path("Config/ontologies/hr__human_resources_sample_pbix.ontology.yaml")
    if not hr_path.exists():
        pytest.skip("HR ontology file not found")

    dom = FederatedOntologyLoader.load_from_file(hr_path)
    fed = FederatedOntologyLoader.merge_domains([dom])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    engine = NLQueryEngine(idx, fed)

    plan = engine.resolve_query("What is Count of BU by AgeGroup?")
    assert plan.refused is True
    assert plan.sql is None
    assert plan.engine == "local"
    assert plan.guard_checks == []
