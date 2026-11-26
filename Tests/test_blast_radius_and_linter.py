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


def test_blast_radius_addition_finding_does_not_search_ontology():
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])
    sim = BlastRadiusSimulator(ontology=fed)

    # ADDITION finding exists only in warehouse, not in ontology
    report = sim.simulate_impact(
        target_name="DRIFT_TEST_HEADCOUNT",
        target_type="METRIC",
        change_type="ADD",
        table_context="EMPLOYEE",
        expression="COUNT(DISTINCT EMPLOYEE.EMPLID)",
        category="ADDITION",
    )
    assert report.severity == "SAFE"
    assert report.status == "ok"
    assert report.total_impacted_items == 0
    assert report.suggestions == []
    assert "not deployed" in report.not_found_reason.lower()
    # Check that warehouse references are reported
    assert any("EMPLOYEE.EMPLID" in e.get("from", "") or "EMPLOYEE" in e.get("from", "") for e in report.dependency_graph_edges)
    assert any("not deployed" in step.lower() for step in report.remediation_steps)
    assert any("warehouse references" in step.lower() for step in report.remediation_steps)


def test_blast_radius_drift_finding_not_found_drops_did_you_mean():
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])
    sim = BlastRadiusSimulator(ontology=fed)

    # When checking a drift finding for an unmanaged object missing from baseline with expression drift
    report = sim.simulate_impact(
        target_name="UNKNOWN_WH_METRIC",
        target_type="METRIC",
        change_type="MODIFY_EXPRESSION",
        category="EXPRESSION_DRIFT",
    )
    assert report.severity == "NOT_FOUND"
    assert report.status == "not_found"
    assert report.suggestions == []  # Did you mean dropped on finding path
    assert "not deployed" in report.not_found_reason.lower()


def test_blast_radius_no_cross_project_event_leakage(monkeypatch):
    """Blast radius for Project A must never inherit drift event details from Project B."""
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])
    sim = BlastRadiusSimulator(ontology=fed, mock_mode=False)

    fake_events = [
        {
            "id": "ev_proj_b_shared_metric",
            "project_id": "proj-b",
            "object_name": "HEADCOUNT",
            "table": "CUSTOM_TABLE_B",
            "live_value": "SUM(CUSTOM_B.AMT)",
            "category": "DESCRIPTION_DRIFT",
            "live_description": "Project B specific description comment",
        }
    ]

    import semabridge.governance.drift_service as ds
    monkeypatch.setattr(ds, "list_drift_events", lambda project_id=None: [e for e in fake_events if e.get("project_id") == project_id])

    # Run for proj-a: must NOT inherit proj-b's table, expression, or category
    rep_a = sim.simulate_impact(
        target_name="HEADCOUNT",
        target_type="METRIC",
        change_type="MODIFY_EXPRESSION",
        project_id="proj-a",
    )
    assert rep_a.target_object == "HEADCOUNT"
    # Project B's custom table and expression must not leak into Project A report
    for edge in rep_a.dependency_graph_edges:
        assert "CUSTOM_TABLE_B" not in edge.get("from", "")
        assert "CUSTOM_TABLE_B" not in edge.get("to", "")


def test_blast_radius_uniform_response_shape_across_all_four_categories():
    """All 4 drift categories (ADDITION, DELETION, EXPRESSION_DRIFT, DESCRIPTION_DRIFT) return identical response shape."""
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])
    sim = BlastRadiusSimulator(ontology=fed, mock_mode=False)

    expected_keys = {
        "target_object", "target_type", "change_type", "severity", "risk", "status",
        "suggestions", "not_found_reason", "total_impacted_items", "affected_metrics",
        "affected_bi_reports", "affected_ai_tools", "remediation_steps",
        "dependency_graph_edges", "referencing_metrics", "stated_dimension_constraints",
        "certified_queries", "bi_truth_cases", "source_model_difference", "empty_messages",
    }

    # 1. ADDITION
    rep_add = sim.simulate_impact("NEW_METRIC", target_type="METRIC", change_type="ADD", category="ADDITION")
    assert set(rep_add.model_dump().keys()) == expected_keys
    assert "referencing_metrics" in rep_add.empty_messages
    assert "stated_dimension_constraints" in rep_add.empty_messages
    assert "certified_queries" in rep_add.empty_messages
    assert "bi_truth_cases" in rep_add.empty_messages

    # 2. DELETION
    rep_del = sim.simulate_impact("HEADCOUNT", target_type="METRIC", change_type="DROP", category="DELETION")
    assert set(rep_del.model_dump().keys()) == expected_keys

    # 3. EXPRESSION_DRIFT
    rep_expr = sim.simulate_impact("HEADCOUNT", target_type="METRIC", change_type="MODIFY_EXPRESSION", category="EXPRESSION_DRIFT")
    assert set(rep_expr.model_dump().keys()) == expected_keys

    # 4. DESCRIPTION_DRIFT
    rep_desc = sim.simulate_impact("HEADCOUNT", target_type="METRIC", change_type="MODIFY_DESCRIPTION", category="DESCRIPTION_DRIFT")
    assert set(rep_desc.model_dump().keys()) == expected_keys


def test_blast_radius_live_mode_never_contains_mock_fixture_names():
    """Live results (mock_mode=False) must never invent or return mock fixture report names."""
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])
    sim = BlastRadiusSimulator(ontology=fed, mock_mode=False)

    from semabridge.demo.blast_radius_fixtures import MOCK_BI_REPORTS, MOCK_AI_TOOLS
    mock_bi_names = {r["name"].casefold() for r in MOCK_BI_REPORTS}
    mock_ai_names = {t["name"].casefold() for t in MOCK_AI_TOOLS}

    report = sim.simulate_impact("HEADCOUNT", target_type="METRIC", change_type="DROP", mock_mode=False)
    for bi in report.affected_bi_reports:
        assert bi.name.casefold() not in mock_bi_names, f"Mock report leaked into live result: {bi.name}"
    for ai in report.affected_ai_tools:
        assert ai.name.casefold() not in mock_ai_names, f"Mock AI tool leaked into live result: {ai.name}"


def test_dimension_constraint_extractor_best_effort_and_empty_messages():
    """Unrecognized description comments are recorded distinctly from missing comments."""
    from semabridge.governance.blast_radius import extract_stated_dimension_constraints
    assert "best-effort" in (extract_stated_dimension_constraints.__doc__ or "").lower()

    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_ONTOLOGY_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])
    sim = BlastRadiusSimulator(ontology=fed, mock_mode=False)

    # 1. Unrecognized constraint phrasing in description
    rep_unrec = sim.simulate_impact(
        "HEADCOUNT",
        target_type="METRIC",
        change_type="MODIFY_DESCRIPTION",
        expression="Active headcount metric. This should ideally be viewed quarterly by finance teams.",
    )
    assert len(rep_unrec.stated_dimension_constraints) == 0
    assert "no standard dimension constraint phrasing was recognized" in rep_unrec.empty_messages.get("stated_dimension_constraints", "")

    # 2. No comment at all
    rep_none = sim.simulate_impact(
        "HEADCOUNT",
        target_type="METRIC",
        change_type="MODIFY_DESCRIPTION",
        expression="",
    )
    assert len(rep_none.stated_dimension_constraints) == 0
    assert "No description comment or required query dimensions stated" in rep_none.empty_messages.get("stated_dimension_constraints", "")


