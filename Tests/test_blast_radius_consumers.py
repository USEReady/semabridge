"""Blast Radius names the real report visuals and AI agents a change affects."""
from __future__ import annotations

import json
import zipfile

from semabridge.governance.blast_radius import BlastRadiusSimulator
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import DomainOntology
from semabridge.sources.report_parsing import report_field_usage


def _visual(vtype, title, select):
    return {
        "config": json.dumps({"singleVisual": {"visualType": vtype, "vcObjects": {"title": [
            {"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]}}}),
        "query": json.dumps({"Commands": [{"SemanticQuery": {
            "From": [{"Name": "s", "Entity": "Sales"}], "Select": select}}]}),
    }


LAYOUT = {"sections": [{"displayName": "Overview", "visualContainers": [
    _visual("card", "Revenue card", [{"Measure": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Total Revenue"}}]),
    _visual("barChart", "By region", [
        {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Region"}},
        {"Measure": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Units"}},
    ]),
    {"config": json.dumps({"singleVisual": {"visualType": "textbox"}})},
]}]}


def _sim(**kw):
    dom = DomainOntology.model_validate({
        "domain": "Sales", "source_project_id": "proj-a",
        "concepts": [{"name": "Sales", "physical_table": "SALES",
                      "attributes": [{"name": "Amount", "data_type": "NUMBER"}, {"name": "Region", "data_type": "VARCHAR"}]}],
        "metrics": [{"name": "Total Revenue", "concept": "Sales", "sql_expression": "SUM(SALES.Amount)"},
                    {"name": "Units", "concept": "Sales", "sql_expression": "COUNT(*)"}],
    })
    return BlastRadiusSimulator(ontology=FederatedOntologyLoader.merge_domains([dom]), mock_mode=False, **kw)


def test_layout_lists_every_visual_with_its_fields():
    usage = report_field_usage(LAYOUT, report="Sales Report")
    assert [(v["page"], v["title"], v["visual_type"]) for v in usage] == [
        ("Overview", "Revenue card", "Card"), ("Overview", "By region", "BarChart"),
    ]
    assert {f["field"] for f in usage[1]["fields"]} == {"Region", "Units"}


def test_dropping_a_column_names_the_reports_and_tests_that_use_it():
    usage = report_field_usage(LAYOUT, report="Sales Report")
    cases = [{"question": "What is total revenue?", "metric_name": "Total Revenue", "status": "approved"},
             {"question": "Units by region?", "metric_name": "Units", "status": "approved"}]
    rep = _sim(report_usage=usage, truth_cases=cases).simulate_impact(
        target_name="Amount", target_type="COLUMN", change_type="DROP", table_context="SALES", mock_mode=False)
    assert [m.name for m in rep.affected_metrics] == ["Total Revenue"]
    assert [r.name for r in rep.affected_bi_reports] == ["Sales Report · Overview · Revenue card"]
    assert rep.affected_bi_reports[0].source == "report_layout"
    assert [a.name for a in rep.affected_ai_tools] == ["What is total revenue?"]
    assert rep.affected_ai_tools[0].source == "bi_truth"


def test_a_column_shown_directly_is_a_direct_report_impact():
    usage = report_field_usage(LAYOUT, report="Sales Report")
    rep = _sim(report_usage=usage).simulate_impact(
        target_name="Region", target_type="COLUMN", change_type="RENAME", table_context="Sales", mock_mode=False)
    names = {r.name: r for r in rep.affected_bi_reports}
    assert "Sales Report · Overview · By region" in names
    assert names["Sales Report · Overview · By region"].impact_level == "DIRECT"


def test_no_usage_means_no_invented_reports():
    rep = _sim().simulate_impact(target_name="Amount", target_type="COLUMN", change_type="DROP",
                                 table_context="SALES", mock_mode=False)
    assert rep.affected_bi_reports == []


def test_usage_is_saved_and_backfilled_from_the_pbix(tmp_path, monkeypatch):
    from semabridge.governance import report_usage

    store = {}
    monkeypatch.setattr(report_usage, "save", lambda pid, model, visuals: store.__setitem__((pid, model), visuals) or True)
    pbix = tmp_path / "Sales Report.pbix"
    with zipfile.ZipFile(pbix, "w") as z:
        z.writestr("Report/Layout", json.dumps(LAYOUT))
    from semabridge.api.services import run_records

    monkeypatch.setattr(run_records, "list_project_runs", lambda pid, limit=20: [
        {"run_id": "r1", "status": "success", "results": [{"pbix_path": str(pbix)}]}])
    visuals = report_usage.backfill_from_pbix("proj-a")
    assert len(visuals) == 2 and store[("proj-a", "Sales Report")] == visuals
