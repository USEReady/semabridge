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
      - name: unrelated_column
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
    assert report.affected_bi_reports == []
    assert report.affected_ai_tools == []
    assert len(report.remediation_steps) > 0


def test_blast_radius_safe_change():
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])

    simulator = BlastRadiusSimulator(ontology=fed)

    # Existing unused column -> SAFE
    report = simulator.simulate_impact(target_name="unrelated_column", target_type="COLUMN", change_type="DROP")
    assert report.severity == "SAFE"
    assert report.status == "ok"
    assert report.total_impacted_items == 0

    # Non-existent column / metric -> NOT_FOUND (never SAFE) with suggestions
    nf_col = simulator.simulate_impact(target_name="revenu", target_type="COLUMN", change_type="DROP")
    assert nf_col.severity == "NOT_FOUND"
    assert nf_col.status == "not_found"
    assert "revenue" in nf_col.suggestions

    nf_metric = simulator.simulate_impact(target_name="gross margin typo", target_type="METRIC", change_type="DROP")
    assert nf_metric.severity == "NOT_FOUND"
    assert nf_metric.status == "not_found"

    # Normalized matching across case, spaces, and underscores for name or label
    norm_report = simulator.simulate_impact(target_name="Gross Margin", target_type="METRIC", change_type="MODIFY_EXPRESSION")
    assert norm_report.severity == "DEGRADED"
    assert [m.name for m in norm_report.affected_metrics] == ["gross_margin"]


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


def test_lint_project_scoping_and_inactive_cycle_exclusion():
    dom_a = FederatedOntologyLoader.parse_yaml_string("""
domain: Alpha
concepts:
  - name: FactA
    data_steward: owner@corp.com
    attributes:
      - {name: dim_id, data_type: VARCHAR}
  - name: DimA
    data_steward: owner@corp.com
    attributes:
      - {name: dim_id, data_type: VARCHAR}
relationships:
  - {from_concept: FactA, to_concept: DimA, is_active: true}
  - {from_concept: DimA, to_concept: FactA, is_active: false}
metrics:
  - name: metric_a
    description: Valid metric definition for Alpha domain
    data_steward: owner@corp.com
    sql_expression: "COUNT(*)"
""")
    dom_b = FederatedOntologyLoader.parse_yaml_string("""
domain: Beta
metrics:
  - name: broken_metric
    sql_expression: ""
""")
    fed = FederatedOntologyLoader.merge_domains([dom_a, dom_b])
    linter = SemanticLinter()

    # Scoped strictly to Alpha: ignores Beta's errors and ignores inactive reverse edge
    scoped_report = linter.lint_ontology(fed, allowed_domains=["Alpha"])
    assert scoped_report.passed is True
    assert scoped_report.error_count == 0

    # Unscoped reports Beta's missing steward and empty expression
    full_report = linter.lint_ontology(fed)
    assert full_report.passed is False
    assert full_report.error_count >= 2


def test_blast_radius_verified_queries_and_relationship_remediation():
    dom = FederatedOntologyLoader.parse_yaml_string("""
domain: OpsDomain
concepts:
  - name: FactTable
    physical_table: FACT_TBL
    attributes:
      - {name: key_col, data_type: VARCHAR}
      - {name: amt_col, data_type: DECIMAL}
  - name: LookupTable
    physical_table: DIM_TBL
    attributes:
      - {name: key_col, data_type: VARCHAR}
relationships:
  - name: FactToLookup
    from_concept: FactTable
    to_concept: LookupTable
    join_expression: "FactTable.key_col = LookupTable.key_col"
metrics:
  - name: base_metric
    concept: FactTable
    sql_expression: "SUM(amt_col)"
  - name: derived_metric
    concept: FactTable
    derives_from: [base_metric]
    sql_expression: "[base_metric] * 2"
""")
    fed = FederatedOntologyLoader.merge_domains([dom])
    verified = [{"question": "What is derived metric?", "sql": "SELECT derived_metric FROM tbl"}]
    sim = BlastRadiusSimulator(ontology=fed, verified_queries=verified)

    col_report = sim.simulate_impact("amt_col", target_type="COLUMN", change_type="DROP")
    assert [m.name for m in col_report.affected_metrics] == ["base_metric", "derived_metric"]
    assert col_report.affected_metrics[1].impact_level == "TRANSITIVE"
    assert len(col_report.affected_ai_tools) == 1
    assert col_report.total_impacted_items == len(col_report.dependency_graph_edges)

    key_report = sim.simulate_impact("key_col", target_type="COLUMN", change_type="RENAME")
    assert key_report.severity in ("BREAKING", "SAFE")
    tbl_report = sim.simulate_impact("LookupTable", target_type="TABLE", change_type="DROP")
    # Verify relationship cleanup remediation is emitted when table has metrics/joins cleanly
    assert len(tbl_report.remediation_steps) > 0
