"""Governance fixes: lint scoping of unresolved references, impact source, naming rules, CI gate, recon validation."""

from semabridge.governance.linter import SemanticLinter
from semabridge.ontology.federated_loader import FederatedOntologyLoader


def _fed(*yamls):
    return FederatedOntologyLoader.merge_domains([FederatedOntologyLoader.parse_yaml_string(y) for y in yamls])


_DOM_WITH_BROKEN_REL = """
domain: Broken
concepts:
  - name: fact_one
    data_steward: owner@corp.com
relationships:
  - {from_concept: fact_one, to_concept: missing_dim}
"""

_DOM_CLEAN = """
domain: Clean
concepts:
  - name: fact_two
    data_steward: owner@corp.com
metrics:
  - name: metric_two
    description: Valid metric definition for the clean domain
    data_steward: owner@corp.com
    sql_expression: "COUNT(*)"
"""


def _unresolved(report):
    return [i for i in report.issues if i.rule_id == "RULE_UNRESOLVED_REFERENCE"]


def test_unresolved_references_are_scoped_to_their_domain():
    fed = _fed(_DOM_WITH_BROKEN_REL, _DOM_CLEAN)
    assert fed.unresolved_references  # loader still records them globally
    linter = SemanticLinter()

    clean = linter.lint_ontology(fed, allowed_domains=["Clean"])
    assert _unresolved(clean) == []
    assert clean.passed is True

    broken = _unresolved(linter.lint_ontology(fed, allowed_domains=["Broken"]))
    assert len(broken) == 1
    assert broken[0].domain == "Broken"
    assert broken[0].object_name


def test_unresolved_reference_reported_once_when_loaded_by_two_domains():
    dup = _DOM_WITH_BROKEN_REL.replace("domain: Broken", "domain: BrokenCopy")
    fed = _fed(_DOM_WITH_BROKEN_REL, dup)
    issues = _unresolved(SemanticLinter().lint_ontology(fed))
    assert len(issues) == 1
    assert set(issues[0].domain.split(", ")) == {"Broken", "BrokenCopy"}


def test_unresolved_derives_from_is_reported_as_metric():
    fed = _fed("""
domain: Deriv
metrics:
  - name: child_metric
    sql_expression: "SUM(x)"
    derives_from: [ghost_metric]
""")
    issues = _unresolved(SemanticLinter().lint_ontology(fed))
    assert len(issues) == 1
    assert issues[0].object_type == "METRIC"
    assert issues[0].domain == "Deriv"


# ---------------------------------------------------------------------------
# A9: every impacted item carries a structured `source`
# ---------------------------------------------------------------------------
_IMPACT_YAML = """
domain: Impact
concepts:
  - name: fact_sales
    physical_table: FACT_SALES
    attributes:
      - name: amount
      - name: qty
metrics:
  - name: base_amount
    concept: fact_sales
    sql_expression: "SUM(amount)"
  - name: dax_qty
    concept: fact_sales
    dax_expression: "SUM(fact_sales[qty])"
    sql_expression: "SUM(qty)"
  - name: derived_ratio
    concept: fact_sales
    sql_expression: "[base_amount] / 2"
  - name: lineage_child
    concept: fact_sales
    sql_expression: "COUNT(*)"
    derives_from: [base_amount]
"""


def _impact(**kw):
    from semabridge.governance.blast_radius import BlastRadiusSimulator

    return BlastRadiusSimulator(ontology=_fed(_IMPACT_YAML)).simulate_impact(**kw)


def test_impact_items_have_structured_source_for_direct_and_transitive():
    rep = _impact(target_name="amount", target_type="COLUMN", change_type="DROP")
    by_name = {i.name: i for i in rep.affected_metrics}
    assert by_name["base_amount"].impact_level == "DIRECT"
    assert by_name["base_amount"].source == "SQL"
    assert by_name["derived_ratio"].impact_level == "TRANSITIVE"
    assert "SQL" in by_name["derived_ratio"].source.split(" + ")
    assert by_name["lineage_child"].source == "derives_from"
    assert all(i.source for i in rep.affected_metrics)


def test_impact_source_dax_table_and_target():
    rep = _impact(target_name="qty", target_type="COLUMN", change_type="DROP")
    assert {i.name: i.source for i in rep.affected_metrics}["dax_qty"] == "DAX + SQL"

    rep = _impact(target_name="FACT_SALES", target_type="TABLE", change_type="DROP")
    assert rep.affected_metrics and all(i.source for i in rep.affected_metrics)
    assert {i.name: i.source for i in rep.affected_metrics}["base_amount"] == "table"

    rep = _impact(target_name="base_amount", target_type="METRIC", change_type="MODIFY_EXPRESSION")
    srcs = {i.name: i.source for i in rep.affected_metrics}
    assert srcs["base_amount"] == "target"
    assert srcs["lineage_child"] == "derives_from"


# ---------------------------------------------------------------------------
# A19: CI governance gate passes on an empty ontology store (nothing to govern)
# ---------------------------------------------------------------------------
def _run_gate(tmp_path, *extra):
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, str(repo / "Tests" / "governance_tests" / "g09_cicd_gate.py"), "--gate-only", "--direct",
         "--ontologies-dir", str(tmp_path), "--max-errors", "0", "--steward-level", "warning", *extra],
        capture_output=True, text=True, cwd=str(repo), timeout=120,
    )


def test_ci_gate_passes_on_empty_store_with_notice(tmp_path):
    res = _run_gate(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "nothing to govern" in res.stdout


def test_ci_gate_strict_empty_store_and_real_errors(tmp_path):
    assert _run_gate(tmp_path, "--fail-on-empty").returncode == 2
    (tmp_path / "broken.ontology.yaml").write_text(
        "domain: GateBroken\nmetrics:\n  - name: empty_metric\n    description: metric without any expression\n",
        encoding="utf-8",
    )
    res = _run_gate(tmp_path)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "RULE_EMPTY_EXPRESSION" in res.stdout


# ---------------------------------------------------------------------------
# B4: /certify validates TABLE.METRIC + dimensions against the project model before executing
# ---------------------------------------------------------------------------
_RECON_MODEL = """
domain: ReconModel
concepts:
  - name: orders
    attributes:
      - name: region
metrics:
  - name: order_total
    concept: orders
    sql_expression: "SUM(amount)"
"""


def _certify(monkeypatch, payload):
    import semabridge.api.routers.ai_router as ai_router
    import semabridge.api.routers.reconciliation_router as rr
    from fastapi.testclient import TestClient
    from semabridge.api.main import app

    calls = []

    def _fake_exec(req, **kwargs):  # never touch a live warehouse in tests
        from types import SimpleNamespace

        calls.append(req.sql)
        return SimpleNamespace(success=False, is_simulated=False, error="stubbed", message="", columns=[], rows=[])

    monkeypatch.setattr(ai_router, "execute_semantic_query", _fake_exec)
    dom = FederatedOntologyLoader.parse_yaml_string(_RECON_MODEL)
    monkeypatch.setattr(rr, "_get_project_domains_for_validation", lambda pid: [dom] if pid else [])
    body = {"project_id": "proj_x", "target_table_or_view": "DB.SCH.SV", "target_kind": "semantic_view",
            "dax_results": []}
    body.update(payload)
    return TestClient(app).post("/api/reconciliation/certify", json=body), calls


def test_certify_rejects_free_text_metric_before_execution(monkeypatch):
    res, calls = _certify(monkeypatch, {"metric_name": "Total Orders please", "metric_sql_expression": ""})
    assert res.status_code == 400
    assert "TABLE.METRIC" in res.json()["detail"]
    assert calls == []


def test_certify_rejects_unknown_metric_and_dimension(monkeypatch):
    res, calls = _certify(monkeypatch, {"metric_name": "x", "metric_sql_expression": "orders.not_a_metric"})
    assert res.status_code == 400 and "not found" in res.json()["detail"]
    res, calls2 = _certify(monkeypatch, {"metric_name": "x", "metric_sql_expression": "orders.order_total",
                                         "dimensions": ["orders.no_such_col"]})
    assert res.status_code == 400 and "does not exist" in res.json()["detail"]
    assert calls == [] and calls2 == []


def test_certify_valid_input_reaches_execution(monkeypatch):
    res, calls = _certify(monkeypatch, {"metric_name": "Order Total", "metric_sql_expression": "orders.order_total",
                                        "dimensions": ["orders.region"]})
    assert len(calls) == 1 and "order_total" in calls[0].lower()
    assert res.status_code == 400 and "stubbed" in res.json()["detail"]


# ---------------------------------------------------------------------------
# C17: concept & attribute naming checks (same WARNING severity/switch as metrics)
# ---------------------------------------------------------------------------
_NAMING_YAML = """
domain: Naming
concepts:
  - name: SalesOrder
    data_steward: owner@corp.com
    attributes:
      - name: OrderAmount
      - name: order_id
  - name: order_line
    data_steward: owner@corp.com
    attributes:
      - name: LINE_NO
  - name: Month Name Table
    logical_name: MONTH_NAME_TABLE
    data_steward: owner@corp.com
    attributes:
      - name: Month Name
        logical_name: MONTHNAME
"""


def _naming(report):
    return {(i.object_type, i.object_name): i for i in report.issues if i.rule_id == "RULE_NAMING_CONVENTION"}


def test_concept_and_attribute_naming_flagged_as_warning():
    rep = SemanticLinter().lint_ontology(_fed(_NAMING_YAML))
    found = _naming(rep)
    assert ("CONCEPT", "SalesOrder") in found
    assert ("ATTRIBUTE", "SalesOrder.OrderAmount") in found
    assert all(i.severity == "WARNING" for i in found.values())
    # snake_case (either case) and BI display names with a logical_name identifier are fine
    assert ("ATTRIBUTE", "SalesOrder.order_id") not in found
    assert ("CONCEPT", "order_line") not in found
    assert ("ATTRIBUTE", "order_line.LINE_NO") not in found
    assert ("CONCEPT", "Month Name Table") not in found
    assert ("ATTRIBUTE", "Month Name Table.Month Name") not in found
    assert rep.passed is True


def test_concept_naming_follows_switch_and_can_be_set_independently():
    fed = _fed(_NAMING_YAML)
    off = _naming(SemanticLinter(enforce_snake_case_metrics=False).lint_ontology(fed))
    assert not [k for k in off if k[0] in ("CONCEPT", "ATTRIBUTE")]
    only_metrics = _naming(SemanticLinter(enforce_snake_case_concepts=False).lint_ontology(fed))
    assert not [k for k in only_metrics if k[0] in ("CONCEPT", "ATTRIBUTE")]


def test_lint_api_accepts_concept_naming_option():
    from fastapi.testclient import TestClient
    from semabridge.api.main import app

    res = TestClient(app).post("/api/governance/lint", json={"enforce_snake_case_concepts": False})
    assert res.status_code == 200
    assert not [i for i in res.json()["issues"]
                if i["rule_id"] == "RULE_NAMING_CONVENTION" and i["object_type"] in ("CONCEPT", "ATTRIBUTE")]
