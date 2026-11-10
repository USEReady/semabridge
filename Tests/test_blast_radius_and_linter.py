"""Tests for Semantic Blast Radius Simulator and Governance Quality Gate."""

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.governance.blast_radius import BlastRadiusSimulator
from semabridge.governance.linter import SemanticLinter
from semabridge.ontology.federated_loader import FederatedOntologyLoader

client = TestClient(app)

SAMPLE_ONTOLOGY_YAML = """
domain: Finance
owner: finance@corp.com
concepts:
  - name: SalesTransactions
    description: Master sales ledger
    attributes:
      - name: revenue
      - name: cogs
metrics:
  - name: gross_margin
    description: Gross Margin
    sql_expression: "SUM(revenue) - SUM(cogs)"
    data_steward: "cfo@corp.com"
  - name: net_profit
    description: Net Profit
    sql_expression: "SUM(revenue) * 0.2"
    data_steward: "cfo@corp.com"
"""


def test_blast_radius_breaking_change():
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])

    simulator = BlastRadiusSimulator(ontology=fed)

    # Simulating dropping 'revenue' column
    report = simulator.simulate_impact(target_name="revenue", target_type="COLUMN", change_type="DROP")

    assert report.severity == "BREAKING"
    assert report.total_impacted_items >= 2
    metric_names = [m.name for m in report.affected_metrics]
    assert "gross_margin" in metric_names
    assert "net_profit" in metric_names
    assert len(report.affected_bi_reports) == 0
    assert len(report.affected_ai_tools) == 0
    assert len(report.remediation_steps) > 0


def test_blast_radius_safe_change():
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])

    simulator = BlastRadiusSimulator(ontology=fed)

    # Simulating dropping an unused column
    report = simulator.simulate_impact(target_name="unrelated_column", target_type="COLUMN", change_type="DROP")

    assert report.severity == "SAFE"
    assert report.total_impacted_items == 0


def test_api_blast_radius_endpoint():
    resp = client.post(
        "/api/governance/blast-radius",
        json={"target_name": "cogs", "target_type": "COLUMN", "change_type": "DROP"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "severity" in data
    assert "affected_metrics" in data


def test_semantic_linter_checks():
    invalid_yaml = """
domain: Sales
concepts: []
metrics:
  - name: "Invalid Metric Name With Spaces"
    description: "short"
    sql_expression: "" # Empty formula!
    # Missing data_steward
"""
    dom = FederatedOntologyLoader.parse_yaml_string(invalid_yaml)
    fed = FederatedOntologyLoader.merge_domains([dom])

    linter = SemanticLinter()
    report = linter.lint_ontology(fed)

    assert report.passed is False
    assert report.error_count > 0
    rule_ids = [issue.rule_id for issue in report.issues]
    assert "RULE_MISSING_STEWARD" in rule_ids
    assert "RULE_EMPTY_EXPRESSION" in rule_ids
    assert "RULE_NAMING_CONVENTION" in rule_ids


def test_api_lint_endpoint():
    resp = client.post("/api/governance/lint", json={})
    assert resp.status_code == 200
    report = resp.json()
    assert "passed" in report
    assert "summary" in report
