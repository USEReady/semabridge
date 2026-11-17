"""One ontology domain per MODEL (semantic view) of a multi-model project."""

import json

import pytest

from semabridge.ontology.models import (
    DomainConcept,
    DomainMetric,
    DomainOntology,
    model_key,
    per_model_domain_id,
)
from semabridge.ontology.store import DomainOntologyStore


def _osi(name, metrics, dataset="T"):
    from semabridge.intermediate.models import OSIDataset, OSIMetric, OSIModel

    return OSIModel(
        unique_name=name,
        label=name,
        datasets=[OSIDataset(unique_name=dataset, source_table=dataset, columns=[])],
        metrics=[OSIMetric(unique_name=m, label=m, dataset=dataset, expression=e) for m, e in metrics],
    )


def _model_dom(pid, mid, metric, expr, desc, view):
    return DomainOntology(
        domain=f"{pid} {mid}",
        id=per_model_domain_id(pid, mid),
        source_project_id=pid,
        source_model_id=mid,
        semantic_view_name=view,
        metrics=[DomainMetric(name=metric, sql_expression=expr, description=desc)],
    )


@pytest.fixture
def store(tmp_path):
    s = DomainOntologyStore(storage_dir=tmp_path)
    s.add_or_update_domain(_model_dom("proj-x", "sales", "Revenue", "SUM(net)", "sales rev", "DB.S.SALES_SEMANTIC"))
    s.add_or_update_domain(_model_dom("proj-x", "finance", "Revenue", "SUM(gross)", "finance rev", "DB.S.FINANCE_SEMANTIC"))
    s.add_or_update_domain(DomainOntology(domain="Glossary", metrics=[DomainMetric(name="Margin", description="g")]))
    s.add_or_update_domain(DomainOntology(domain="Other project", source_project_id="proj-y",
                                          metrics=[DomainMetric(name="Revenue", sql_expression="SUM(z)")]))
    s.attach_model_domain("proj-x", "sales", per_model_domain_id("proj-x", "sales"))
    s.attach_model_domain("proj-x", "finance", per_model_domain_id("proj-x", "finance"))
    s.attach_shared_domain("proj-x", "glossary")
    return s


# ------------------------------------------------------------------ ids / model
def test_model_key_and_domain_id():
    assert model_key("/up/0513cdf9944547c186f497c8e0f68511_Competitive Marketing Analysis.pbix") == "competitive_marketing_analysis"
    assert model_key("CORE_FINANCE_V1") == "core_finance_v1"
    assert per_model_domain_id("proj-multi_sync", "Core_Finance_v1") == "multi_sync__core_finance_v1"
    dom = DomainOntology(domain="d", source_model_id="Core Finance v1")
    assert dom.source_model_id == "core_finance_v1"
    with pytest.raises(Exception):
        DomainOntology(domain="d", unknown_field=1)


# ------------------------------------------------------------------ resolver
def test_domains_for_model_is_own_plus_shared(store):
    ids = [d.id for d in store.domains_for("proj-x", "sales")]
    assert ids == ["x__sales", "glossary"]
    assert [d.id for d in store.domains_for("x", "FINANCE")] == ["x__finance", "glossary"]
    assert {d.id for d in store.domains_for("proj-x")} == {"x__sales", "x__finance", "glossary"}


def test_domains_for_never_falls_back(store):
    assert store.domains_for("proj-unknown") == []
    assert store.domains_for("") == []
    assert [d.id for d in store.domains_for("proj-y")] == ["other_project"]


def test_attachments_file_shape_and_legacy_list(store, tmp_path):
    data = json.loads((tmp_path / "_project_domains.json").read_text())
    assert data["proj-x"] == {"models": {"finance": ["x__finance"], "sales": ["x__sales"]}, "shared": ["glossary"]}
    (tmp_path / "_project_domains.json").write_text(json.dumps({"proj-x": ["glossary"]}))
    assert store.get_project_attachments("proj-x") == {"models": {}, "shared": ["glossary"]}
    # generated per-model domains still belong to their model
    assert [d.id for d in store.domains_for("proj-x", "sales")] == ["x__sales", "glossary"]


def test_delete_domain_detaches_and_prune(store, tmp_path):
    store.delete_domain("glossary")
    data = json.loads((tmp_path / "_project_domains.json").read_text())
    assert "glossary" not in json.dumps(data)
    raw = json.loads((tmp_path / "_project_domains.json").read_text())
    raw["proj-gone"] = ["missing_domain"]
    (tmp_path / "_project_domains.json").write_text(json.dumps(raw))
    assert store.prune_dangling_attachments() == ["proj-gone:missing_domain"]


def test_reload_from_disk_does_not_accumulate_errors(store):
    store.reload_from_disk()
    store.reload_from_disk()
    assert store.load_errors == []
    assert {"x__sales", "x__finance", "glossary"} <= {d.id for d in store.domains.values()}


def test_reload_keeps_memory_only_domains(store):
    store.add_or_update_domain(DomainOntology(domain="Scratch"), save_to_disk=False)
    store.reload_from_disk()
    assert store.get_domain("Scratch") is not None and store.load_errors == []


def test_same_project_models_are_not_duplicate_domains(store):
    fed = store.get_federated_ontology()
    assert not [c for c in fed.collisions if c.item_type == "domain"]
    assert fed.metric_owners["revenue"] == sorted(fed.metric_owners["revenue"], key=fed.metric_owners["revenue"].index)
    assert {"proj-x sales", "proj-x finance"} <= set(fed.metric_owners["revenue"])


# ------------------------------------------------------------------ apply
def test_apply_uses_only_the_models_own_domain(store):
    from semabridge.ontology.apply import apply_project_ontology

    model = _osi("SALES", [("Revenue", "SUM(T[net])")])
    report = apply_project_ontology("proj-x", model, store=store, overrides={}, model_names=["SALES"])
    assert report["model_id"] == "sales"
    assert report["applied_domains"] == ["proj-x sales", "Glossary"]
    assert report["blocking_conflicts"] == []  # finance's Revenue is not applied
    assert model.metrics[0].description == "sales rev"

    fin = _osi("Whatever", [("Revenue", "SUM(T[gross])")])
    rep2 = apply_project_ontology("proj-x", fin, store=store, overrides={}, model_id="finance")
    assert rep2["applied_domains"] == ["proj-x finance", "Glossary"]
    assert fin.metrics[0].description == "finance rev"


def test_apply_unknown_model_gets_shared_only(store):
    from semabridge.ontology.apply import apply_project_ontology

    model = _osi("UNRELATED", [("Revenue", "SUM(T[net])")])
    report = apply_project_ontology("proj-x", model, store=store, overrides={}, model_names=["UNRELATED"])
    assert report["applied_domains"] == ["Glossary"]


# ------------------------------------------------------------------ Ask AI routing
def test_ask_routes_to_view_owning_metric(store, monkeypatch):
    from semabridge.ai import project_connection as pc

    monkeypatch.setattr("semabridge.ontology.store.ontology_store", store)
    scope = pc.ProjectScope(project_id="proj-x", domain_views={
        "proj-x sales": "DB.S.SALES_SEMANTIC", "proj-x finance": "DB.S.FINANCE_SEMANTIC"})
    pc.route_scope_to_view("what is revenue by month", scope)
    assert scope.semantic_view is None
    assert set(scope.candidate_views) == {"DB.S.SALES_SEMANTIC", "DB.S.FINANCE_SEMANTIC"}
    assert "DB.S.SALES_SEMANTIC" in scope.routing_message

    dom = store.get_domain("x__finance")
    store.add_or_update_domain(dom.model_copy(update={"metrics": [*dom.metrics, DomainMetric(name="Gross Profit")]}),
                               original=dom.domain)
    scope2 = pc.ProjectScope(project_id="proj-x", domain_views={
        "proj-x sales": "DB.S.SALES_SEMANTIC", "proj-x finance": "DB.S.FINANCE_SEMANTIC"})
    pc.route_scope_to_view("show gross profit", scope2)
    assert scope2.semantic_view == "DB.S.FINANCE_SEMANTIC"
    assert scope2.answered_by_domain == "proj-x finance"
    assert "proj-x sales" not in scope2.allowed_domains and "Glossary" in scope2.allowed_domains


def test_resolve_scope_multi_view_leaves_view_unset(store, monkeypatch):
    from semabridge.ai import project_connection as pc

    monkeypatch.setattr("semabridge.ontology.store.ontology_store", store)

    class _Repo:
        def get_project_deployment_info(self, pid):
            return {"semantic_view": "DB.S.FINANCE_SEMANTIC", "model_name": "FINANCE"}

    monkeypatch.setattr("semabridge.repository.model_repository.ModelRepository", lambda: _Repo())
    scope = pc.resolve_project_scope("proj-x")
    assert scope.semantic_view is None and len(scope.domain_views) == 2
    explicit = pc.resolve_project_scope("proj-x", explicit_view="SALES_SEMANTIC")
    assert explicit.semantic_view == "SALES_SEMANTIC"
    assert "proj-x finance" not in explicit.allowed_domains


# ------------------------------------------------------------------ drift key
def test_drift_event_key_includes_model():
    from semabridge.governance.drift_service import drift_event_key

    a = drift_event_key({"project_id": "proj-x", "model_id": "sales", "metric_name": "Revenue"})
    b = drift_event_key({"project_id": "x", "model_id": "finance", "metric_name": "revenue"})
    legacy = drift_event_key({"project_id": "proj-x", "metric_name": "Revenue"})
    view = drift_event_key({"project_id": "proj-x", "semantic_view": "DB.S.SALES_SEMANTIC", "metric_name": "Revenue"})
    assert a != b and legacy == ("x", "", "revenue") and view == ("x", "sales_semantic", "revenue")


# ------------------------------------------------------------------ migration
def _pm(mid, name, osi, view):
    from semabridge.ontology.project_models import ProjectModel

    return ProjectModel(model_id=mid, model_name=name, semantic_view=view, snapshot_id=f"s-{mid}",
                        blob=json.loads(osi.model_dump_json()), aliases=[mid])


def test_migration_splits_merged_domain(tmp_path):
    from semabridge.ontology.per_model import migrate_project_to_per_model

    s = DomainOntologyStore(storage_dir=tmp_path)
    merged = DomainOntology(
        domain="multi",
        id="multi",
        source_project_id="proj-multi",
        concepts=[DomainConcept(name="T", synonyms=["Table T"])],
        metrics=[
            DomainMetric(name="Revenue", origin="bi", synonyms=["Turnover"], business_rules=["net of returns"]),
            DomainMetric(name="Sales $", origin="bi", description="user text", bi_description="bi text"),
            DomainMetric(name="Old BI", origin="bi"),
            DomainMetric(name="My KPI", origin="user", description="hand written"),
        ],
    )
    s.add_or_update_domain(merged)
    s.set_project_domains("proj-multi", ["multi"])
    models = [
        _pm("a", "Model A", _osi("A", [("Revenue", "SUM(T[x])"), ("SALES_DOL", "SUM(T[s])")]), "DB.S.A_SEMANTIC"),
        _pm("b", "Model B", _osi("B", [("Revenue", "SUM(T[y])")]), "DB.S.B_SEMANTIC"),
    ]
    res = migrate_project_to_per_model("proj-multi", s, models)
    assert res["status"] == "migrated" and res["merged_domains"] == ["multi"]
    rep = res["reports"][0]
    assert {c["name"] for c in rep["clashes"]} == {"T", "Revenue"}
    assert rep["dropped_bi"] == ["metric Old BI"]
    assert rep["shared"] == ["metric My KPI"]

    s.reload_from_disk()
    assert s.load_errors == []
    assert s.get_domain("multi") is None
    a = s.get_domain("multi__a")
    b = s.get_domain("multi__b")
    assert a.semantic_view_name == "DB.S.A_SEMANTIC" and a.source_model_id == "a"
    rev_a = next(m for m in a.metrics if m.name == "Revenue")
    rev_b = next(m for m in b.metrics if m.name == "Revenue")
    assert "Turnover" in rev_a.synonyms and "Turnover" in rev_b.synonyms
    assert rev_a.business_rules == ["net of returns"]
    assert "SUM" in (rev_a.sql_expression or rev_a.dax_expression or "")
    sales = next(m for m in a.metrics if m.name == "SALES_DOL")
    assert sales.description == "user text" and "Sales $" in sales.synonyms
    assert not any(m.name == "SALES_DOL" for m in b.metrics)
    shared = s.get_domain("multi__shared")
    assert [m.name for m in shared.metrics] == ["My KPI"]
    att = s.get_project_attachments("proj-multi")
    assert att == {"models": {"a": ["multi__a"], "b": ["multi__b"]}, "shared": ["multi__shared"]}
    assert [d.id for d in s.domains_for("proj-multi", "b")] == ["multi__b", "multi__shared"]

    again = migrate_project_to_per_model("proj-multi", s, models)
    assert again["status"] == "already migrated"
    assert sorted(p.name for p in tmp_path.glob("*.yaml")) == [
        "multi__a.ontology.yaml", "multi__b.ontology.yaml", "multi__shared.ontology.yaml"]


def test_migration_skips_single_model(tmp_path):
    from semabridge.ontology.per_model import migrate_project_to_per_model

    s = DomainOntologyStore(storage_dir=tmp_path)
    models = [_pm("a", "A", _osi("A", [("Revenue", "SUM(T[x])")]), None)]
    assert migrate_project_to_per_model("proj-single", s, models)["status"].startswith("skipped")
