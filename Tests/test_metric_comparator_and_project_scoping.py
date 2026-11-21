"""Tests for Metric Comparator, collision scoping, identical formulas, and preview exclusion."""

import pytest
from semabridge.governance.linter import SemanticLinter
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import DomainConcept, DomainMetric, DomainOntology, OntologyCollision
from semabridge.ontology.store import DomainOntologyStore
from semabridge.api.routers.governance_router import _resolve_project_allowed_domains


def test_identical_definitions_across_projects_are_info():
    """Identical formulas across domains yield collision with identical=True and severity='INFO'."""
    d1 = DomainOntology(
        domain="HR_A",
        id="hr_a",
        source_project_id="proj-hr",
        metrics=[DomainMetric(name="Headcount", sql_expression="COUNT(emp_id)", description="Total employees")],
    )
    d2 = DomainOntology(
        domain="HR_B",
        id="hr_b",
        source_project_id="proj-other",
        metrics=[DomainMetric(name="Headcount", sql_expression="count( emp_id )", description="Total staff")],
    )
    fed = FederatedOntologyLoader.merge_domains([d1, d2])
    cols = [c for c in fed.collisions if c.item_name == "Headcount"]
    assert len(cols) == 1
    col = cols[0]
    assert col.identical is True
    assert col.severity == "INFO"
    assert "identical formula" in col.message


def test_different_definitions_across_projects_are_error():
    """Different formulas across domains yield collision with identical=False and severity='ERROR'."""
    d1 = DomainOntology(
        domain="HR_A",
        id="hr_a",
        source_project_id="proj-hr",
        metrics=[DomainMetric(name="Headcount", sql_expression="COUNT(emp_id)")],
    )
    d2 = DomainOntology(
        domain="HR_B",
        id="hr_b",
        source_project_id="proj-other",
        metrics=[DomainMetric(name="Headcount", sql_expression="SUM(active_flag)")],
    )
    fed = FederatedOntologyLoader.merge_domains([d1, d2])
    cols = [c for c in fed.collisions if c.item_name == "Headcount"]
    assert len(cols) == 1
    col = cols[0]
    assert col.identical is False
    assert col.severity == "ERROR"


def test_proj_hr_scope_shows_no_collisions_involving_only_one_project_domain():
    """In project scope, collisions involving only 1 domain of the project are not flagged."""
    d_hr = DomainOntology(
        domain="HR_Domain",
        id="hr__human_resources_sample_pbix",
        source_project_id="proj-hr",
        metrics=[DomainMetric(name="Actives", sql_expression="COUNT(id)")],
    )
    d_other = DomainOntology(
        domain="Other_Domain",
        id="other__model",
        source_project_id="proj-other",
        metrics=[DomainMetric(name="Actives", sql_expression="SUM(active)")],
    )
    fed = FederatedOntologyLoader.merge_domains([d_hr, d_other])
    assert len(fed.collisions) >= 1

    linter = SemanticLinter()
    # Scoped to proj-hr allowed domains
    report = linter.lint_ontology(fed, allowed_domains=["HR_Domain", "hr__human_resources_sample_pbix"])
    col_issues = [i for i in report.issues if i.rule_id == "RULE_METRIC_COLLISION"]
    assert len(col_issues) == 0, "Cross-project collision involving only 1 proj-hr domain must not be reported in proj-hr scope"
    assert report.metrics_checked == 1, "Must return metrics_checked for proj-hr's domains"


def test_preview_dry_run_creates_no_ontology_domain(tmp_path):
    """Preview-* projects must never persist domain YAMLs to disk or record attachments."""
    store = DomainOntologyStore(storage_dir=tmp_path)
    dom = DomainOntology(
        domain="Preview Test Domain",
        id="preview_123456__sample",
        source_project_id="preview-123456",
        source_model_id="sample",
        metrics=[DomainMetric(name="M1", sql_expression="1")],
    )
    store.add_or_update_domain(dom, save_to_disk=True)
    store.attach_model_domain("preview-123456", "sample", dom.id)

    # Check disk
    yaml_files = list(tmp_path.glob("*.yaml"))
    assert len(yaml_files) == 0, f"Preview domain must not be written to disk: {yaml_files}"
    attachments = store.get_project_attachments("preview-123456")
    assert attachments == {"models": {}, "shared": []}, "Preview project must not be attached"


def test_project_with_metrics_never_shows_no_metrics_in_this_project():
    """Project with metrics resolves allowed domains and checks them."""
    allowed = _resolve_project_allowed_domains("proj-hr")
    assert allowed is not None
    assert any("hr" in str(a).lower() for a in allowed)

    from semabridge.ontology.store import ontology_store
    fed = ontology_store.get_federated_ontology()
    linter = SemanticLinter()
    report = linter.lint_ontology(fed, allowed_domains=allowed)
    assert report.metrics_checked > 0, "proj-hr has metrics, so metrics_checked must be > 0"
