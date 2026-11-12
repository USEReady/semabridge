"""Ontology quality checks (wizard Ontology step + per-project health) and their APIs."""

import pytest
from fastapi.testclient import TestClient

from semabridge.intermediate.models import OSIDataset, OSIMetric, OSIModel
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import DomainMetric, DomainOntology
from semabridge.ontology.quality import (
    SourceMetric,
    analyze_source_metrics,
    apply_review_decisions,
    normalize_expression,
    project_ontology_health,
    source_metrics_from_osi,
)


def _model(name, metrics):
    return OSIModel(
        unique_name=name,
        label=name,
        datasets=[OSIDataset(unique_name="Sales")],
        metrics=[OSIMetric(unique_name=n, label=n, dataset="Sales", expression=e, description=d) for n, e, d in metrics],
    )


EXEC = _model("Exec Dashboard", [("Total Revenue", "SUM(Sales[Amount])", None), ("Gross Margin", "DIVIDE([GP],[Rev])", None)])
OPS = _model("Sales Ops", [("Revenue", "SUM( 'Sales'[Amount] ) // net", "Net invoiced revenue"), ("Win Rate", "DIVIDE([Won],[Closed])", None)])
KPI = _model("Regional KPIs", [("Sales Amount", "=sum(Sales[Amount])", None), ("Win Rate", "DIVIDE([Won],[Closed])", None), ("Rows", "COUNT(*)", None)])


def _metrics():
    out = []
    for m in (EXEC, OPS, KPI):
        out.extend(source_metrics_from_osi(m))
    return out


def _finance(expr="(revenue - cogs) / revenue"):
    return DomainOntology(
        domain="Finance",
        owner="CFO Office",
        metrics=[DomainMetric(name="gross_margin", label="Gross Margin", sql_expression=expr, description="GM %")],
    )


# --------------------------------------------------------------------------- normalisation


def test_normalize_ignores_quotes_case_whitespace_comments_and_equals():
    a = normalize_expression("SUM( 'Sales'[Amount] ) // net")
    assert a == normalize_expression("=sum(Sales[Amount])")
    assert a == normalize_expression("SUM(Sales[Amount]) /* comment */")
    assert a != normalize_expression("SUM(Sales[Cost])")


# --------------------------------------------------------------------------- analysis


def test_duplicates_grouped_by_formula_not_name():
    result = analyze_source_metrics(_metrics())
    groups = {d["suggested_name"]: d for d in result["duplicates"]}
    revenue = next(d for d in result["duplicates"] if len(d["members"]) == 3)
    assert revenue["kind"] == "different_names"
    assert {m["name"] for m in revenue["members"]} == {"Total Revenue", "Revenue", "Sales Amount"}
    assert {m["source"] for m in revenue["members"]} == {"Exec Dashboard", "Sales Ops", "Regional KPIs"}
    assert groups["Win Rate"]["kind"] == "same_name"
    # COUNT(*) is too generic to call anything a duplicate
    assert all("Rows" not in [m["name"] for m in d["members"]] for d in result["duplicates"])


def test_summary_counts_business_metrics_once():
    s = analyze_source_metrics(_metrics())["summary"]
    assert s["measures_found"] == 7
    # revenue (3 copies) + win rate (2) + gross margin + rows
    assert s["business_metrics"] == 4
    assert s["duplicate_groups"] == 2
    assert s["duplicate_measures"] == 3
    assert s["glossary_described"] == 1
    assert s["glossary_coverage_pct"] == 25.0


def test_conflict_with_governed_metric_and_governed_match():
    fed = FederatedOntologyLoader.merge_domains([_finance()])
    result = analyze_source_metrics(_metrics(), fed)
    onto = [c for c in result["conflicts"] if c["kind"] == "ontology"]
    assert len(onto) == 1
    assert onto[0]["name"] == "Gross Margin"
    assert onto[0]["existing"]["domain"] == "Finance"
    assert onto[0]["existing"]["owner"] == "CFO Office"

    same = FederatedOntologyLoader.merge_domains([_finance("DIVIDE([GP], [Rev])")])
    result = analyze_source_metrics(_metrics(), same)
    assert [g["name"] for g in result["governed"]] == ["Gross Margin"]
    assert not [c for c in result["conflicts"] if c["kind"] == "ontology"]
    # the governed metric's description counts towards glossary coverage
    assert result["summary"]["glossary_described"] == 2


def test_same_name_different_formula_across_sources_is_a_conflict():
    metrics = [
        SourceMetric("Margin", "SUM(A[x])", source="one.pbix"),
        SourceMetric("Margin", "SUM(A[y])", source="two.pbix"),
    ]
    conflicts = analyze_source_metrics(metrics)["conflicts"]
    assert len(conflicts) == 1 and conflicts[0]["kind"] == "source"
    assert len(conflicts[0]["variants"]) == 2


# --------------------------------------------------------------------------- decisions


def _draft():
    combined = EXEC.model_copy(update={"metrics": [*EXEC.metrics, *OPS.metrics, KPI.metrics[0]]})
    return FederatedOntologyLoader.from_osi_model(model=combined, domain_name="Sales Review")


def test_merge_keeps_one_metric_bound_to_every_copy():
    dom, report = apply_review_decisions(
        _draft(), merges=[{"name": "revenue", "members": ["Total Revenue", "Revenue", "Sales Amount"]}]
    )
    names = [m.name for m in dom.metrics]
    assert "revenue" in names and "Total Revenue" not in names and "Sales Amount" not in names
    rev = next(m for m in dom.metrics if m.name == "revenue")
    assert set(rev.maps_to) == {"Total Revenue", "Revenue", "Sales Amount"}
    assert {"Total Revenue", "Sales Amount"} <= set(rev.synonyms)
    assert rev.description == "Net invoiced revenue"
    assert report["merged"]
    DomainOntology.model_validate(dom.model_dump())  # still a valid domain


def test_merge_onto_unrelated_existing_name_is_skipped():
    dom, report = apply_review_decisions(_draft(), merges=[{"name": "Win Rate", "members": ["Revenue", "Sales Amount"]}])
    assert report["skipped"] and not report["merged"]
    assert len(dom.metrics) == len(_draft().metrics)


def test_conflict_resolutions_and_descriptions():
    dom, report = apply_review_decisions(
        _draft(),
        conflicts=[
            {"name": "Gross Margin", "resolution": "keep_new", "new_name": "gross_margin_ops"},
            {"name": "Win Rate", "resolution": "use_existing"},
        ],
        descriptions={"Total Revenue": "Invoiced revenue, net of returns"},
    )
    names = {m.name for m in dom.metrics}
    assert "gross_margin_ops" in names and "Gross Margin" not in names
    assert "Win Rate" not in names
    assert next(m for m in dom.metrics if m.name == "gross_margin_ops").maps_to == ["Gross Margin"]
    assert next(m for m in dom.metrics if m.name == "Total Revenue").description.startswith("Invoiced")
    assert report["renamed"] and report["dropped"] == ["Win Rate"] and report["described"] == ["Total Revenue"]


def test_resync_does_not_re_add_merged_copies():
    dom, _ = apply_review_decisions(
        _draft(), merges=[{"name": "revenue", "members": ["Total Revenue", "Revenue", "Sales Amount"]}]
    )
    combined = EXEC.model_copy(update={"metrics": [*EXEC.metrics, *OPS.metrics, KPI.metrics[0]]})
    resynced = FederatedOntologyLoader.from_osi_model(model=combined, existing_ontology=dom)
    names = [m.name for m in resynced.metrics]
    assert "revenue" in names
    assert not {"Total Revenue", "Revenue", "Sales Amount"} & set(names)


# --------------------------------------------------------------------------- health


def test_project_health_score():
    assert project_ontology_health([])["score"] is None

    clean = DomainOntology(
        domain="Ops",
        owner="Ops team",
        metrics=[DomainMetric(name="units", sql_expression="SUM(x)", description="Units shipped")],
    )
    good = project_ontology_health([clean])
    assert good["score"] == 100 and good["status"] == "good"

    messy = DomainOntology(
        domain="Mkt",
        metrics=[
            DomainMetric(name="a", sql_expression="SUM(x)"),
            DomainMetric(name="b", sql_expression="sum( x )"),
            DomainMetric(name="c", sql_expression="SUM(y)", description="described"),
        ],
    )
    h = project_ontology_health([messy])
    assert h["duplicate_groups"] == 1
    assert h["glossary_coverage_pct"] == pytest.approx(33.3)
    # 0.7 * 1/3 * 100 = 23.3, minus 5 for the duplicate group
    assert h["score"] == 18 and h["status"] == "poor"


def test_project_health_counts_cross_domain_conflicts():
    sales = DomainOntology(domain="Sales", owner="RevOps", metrics=[DomainMetric(name="gross_margin", sql_expression="X", description="d")])
    fed = FederatedOntologyLoader.merge_domains([_finance(), sales])
    h = project_ontology_health([sales], fed)
    assert h["conflicts"] == 1 and h["conflict_examples"] == ["gross_margin"]
    assert h["score"] == 90


# --------------------------------------------------------------------------- API


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    from semabridge.api.main import app
    from semabridge.ontology.store import ontology_store

    orig_domains, orig_dir = ontology_store.domains, ontology_store.storage_dir
    ontology_store.domains, ontology_store.storage_dir = {}, tmp_path
    ontology_store.add_or_update_domain(_finance(), save_to_disk=False)

    async def fake_read(source_config, selected_sources, project_id=None):
        return [EXEC, OPS, KPI], [{"source": "broken.pbix", "error": "bad file"}]

    monkeypatch.setattr("semabridge.api.services.ontology_review_service.read_source_models", fake_read)
    try:
        yield TestClient(app), ontology_store
    finally:
        ontology_store.domains, ontology_store.storage_dir = orig_domains, orig_dir
        ontology_store._refresh_index()


def test_api_review_analyze_and_apply(client_and_store):
    client, store = client_and_store
    body = {"source_config": {"type": "pbix"}, "selected_sources": ["a.pbix", "b.pbix", "c.pbix"]}
    res = client.post("/api/ontology/review/analyze", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["supported"] is True and data["analysis_id"]
    assert data["summary"]["duplicate_groups"] == 2
    assert data["errors"] == [{"source": "broken.pbix", "error": "bad file"}]
    assert any(c["kind"] == "ontology" for c in data["conflicts"])

    apply = {
        "analysis_id": data["analysis_id"],
        "domain_name": "Sales Pipeline",
        "owner": "RevOps",
        "project_id": "proj-1",
        "merges": [{"name": "revenue", "members": ["Total Revenue", "Revenue", "Sales Amount"]}],
        "conflicts": [{"name": "Gross Margin", "resolution": "use_existing"}],
        "descriptions": {"Win Rate": "Won / closed opportunities"},
    }
    res = client.post("/api/ontology/review/apply", json=apply)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["domain"] == "Sales Pipeline"
    dom = store.get_domain("Sales Pipeline")
    assert {m.name for m in dom.metrics} == {"revenue", "Win Rate", "Rows"}
    assert dom.source_project_id == "proj-1"
    assert dom.id in store.get_project_domain_ids("proj-1")

    health = client.get("/api/ontology/projects/proj-1/health").json()
    assert health["metric_count"] == 3 and health["score"] is not None
    batch = client.get("/api/ontology/projects/health", params={"project_ids": "proj-1,none"}).json()
    assert batch["projects"]["proj-1"]["score"] == health["score"]
    assert batch["projects"]["none"]["status"] == "none"

    # Same project again (even under another name): merged into the project's
    # existing domain instead of creating a second one (A2).
    again = client.post("/api/ontology/review/apply", json={**apply, "domain_name": "Other Name"})
    assert again.status_code == 200, again.text
    assert again.json()["synced_existing"] is True and again.json()["domain"] == "Sales Pipeline"
    assert store.get_domain("Other Name") is None
    assert store.get_project_domain_ids("proj-1") == [dom.id]

    # A different project may not reuse another project's domain name.
    clash = client.post("/api/ontology/review/apply", json={**apply, "project_id": "proj-2"})
    assert clash.status_code == 409


def test_api_review_unsupported_source(client_and_store, monkeypatch):
    client, _ = client_and_store
    from semabridge.api.services import ontology_review_service as svc

    async def unsupported(*_a, **_k):
        raise svc.UnsupportedSourceError("Run the dry run first.")

    monkeypatch.setattr(svc, "read_source_models", unsupported)
    res = client.post("/api/ontology/review/analyze", json={"source_config": {"type": "fabric"}, "selected_sources": ["m"]})
    assert res.status_code == 200
    assert res.json() == {"supported": False, "message": "Run the dry run first."}


# --------------------------------------------------------------------------- portfolio summary


@pytest.mark.asyncio
async def test_portfolio_summary_aggregates_latest_successful_run(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from semabridge.api.services import portfolio_summary_service as svc

    now = datetime.now(timezone.utc)
    runs = {
        "p1": [
            {"run_id": "r2", "status": "failed", "started_at": now.isoformat(), "message": "Warehouse suspended"},
            {"run_id": "r1", "status": "success", "started_at": (now - timedelta(days=1)).isoformat()},
        ],
        "p2": [],
    }
    reports = {
        "r1": {
            "counts": {"tables": 4, "columns": 30, "calculations": 10, "relationships": 3,
                       "standard": 6, "ai_assisted": 2, "needs_review": 1, "dropped": 1, "excluded_by_design": 0},
            "source_files": ["a.pbix", "b.pbix"],
            "sections": {"ai_assisted": [{"confidence": 0.8}, {"confidence": 0.9}]},
        }
    }

    async def fake_runs(pid):
        return runs[pid]

    async def fake_report(pid, rid):
        return reports.get(rid)

    monkeypatch.setattr("semabridge.api.services.run_service.get_project_runs_compat", fake_runs)
    monkeypatch.setattr("semabridge.api.services.run_service.get_run_report_data_compat", fake_report)
    svc._report_cache.clear()

    projects = [
        {"id": "p1", "name": "Sales", "source": "pbix", "target_type": "snowflake"},
        {"id": "p2", "name": "HR", "source": "pbix", "target_type": "snowflake",
         "created_at": (now - timedelta(days=12)).isoformat()},
    ]
    out = await svc.build_portfolio_summary(projects, days=30)

    assert out["projects"] == {"total": 2, "active": 0, "draft": 1, "failing": 1}
    assert out["migrated"]["calculations"] == 10 and out["migrated"]["models"] == 2
    assert out["migrated"]["measures_total"] == 10 and out["migrated"]["conversion_pct"] == 80.0
    assert out["migrated"]["projects_with_data"] == 1 and out["migrated"]["projects_with_runs"] == 1
    assert out["ai"]["measures_ai_assisted"] == 2 and out["ai"]["avg_confidence"] == 0.85
    assert out["ai"]["ask_ai"] is None
    assert out["runs"]["total"] == 2 and out["runs"]["succeeded"] == 1 and out["runs"]["success_pct"] == 50.0
    assert len(out["runs"]["trend"]) == 30
    kinds = [a["kind"] for a in out["attention"]]
    assert kinds[0] == "failed" and "draft" in kinds and "review" in kinds
    p1 = out["per_project"]["p1"]
    assert p1["last_run_status"] == "failed" and p1["conversion_pct"] == 80.0
    assert out["routes"] == [{"route": "pbix → snowflake", "projects": 2}]


def test_description_for_a_merged_copy_lands_on_the_merged_metric():
    dom, report = apply_review_decisions(
        _draft(),
        merges=[{"name": "revenue", "members": ["Total Revenue", "Revenue", "Sales Amount"]}],
        descriptions={"Sales Amount": "Invoiced revenue"},
    )
    assert next(m for m in dom.metrics if m.name == "revenue").description == "Invoiced revenue"
    assert report["described"] == ["Sales Amount"]


def test_placeholder_measures_are_not_duplicates():
    metrics = [SourceMetric("#Space01", '""'), SourceMetric("#Space02", '""'), SourceMetric("Blank", "BLANK()")]
    assert analyze_source_metrics(metrics)["duplicates"] == []
