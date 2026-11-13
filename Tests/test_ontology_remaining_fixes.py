"""Focused regression tests for ontology follow-up fixes (A2, A13, C7, C10, C12, C14, C21)."""
import pytest

from semabridge.ontology.federated_loader import (
    FederatedOntologyLoader,
    normalize_formula_for_compare,
)
from semabridge.ontology.models import DomainMetric, DomainOntology


def _dom(name, metric_name, expr, project=None):
    return DomainOntology(
        domain=name,
        source_project_id=project,
        metrics=[DomainMetric(name=metric_name, sql_expression=expr)],
    )


# ---------------------------------------------------------------- A13
@pytest.mark.parametrize(
    "a,b",
    [
        ("SUM([x])", "sum( x )"),
        ("SUM('Sales T'[x])", "SUM(x)"),
        ("SUM(T[x])", 'SUM("x")'),
        ('SUM("T"."x")', "sum(t.x)"),
        ("SUM([Net Amount])", 'SUM("Net Amount")'),
    ],
)
def test_formula_normalisation_ignores_reference_syntax(a, b):
    assert normalize_formula_for_compare(a) == normalize_formula_for_compare(b)


def test_formula_normalisation_keeps_real_differences():
    assert normalize_formula_for_compare("SUM(x)") != normalize_formula_for_compare("AVG(x)")
    assert normalize_formula_for_compare("SUM(x)") != normalize_formula_for_compare("SUM(y)")
    assert normalize_formula_for_compare("COUNT(CASE WHEN a='Foo' THEN 1 END)") != normalize_formula_for_compare(
        "COUNT(CASE WHEN a='foo' THEN 1 END)"
    )


def test_cross_project_equivalent_formulas_do_not_error():
    fed = FederatedOntologyLoader.merge_domains(
        [_dom("D1", "Revenue", "SUM([x])", "p1"), _dom("D2", "Revenue", "sum( x )", "p2")]
    )
    errors = [c for c in fed.collisions if c.item_type == "metric" and c.severity == "ERROR"]
    assert errors == []


def test_cross_project_different_formulas_still_error():
    fed = FederatedOntologyLoader.merge_domains(
        [_dom("D1", "Revenue", "SUM([x])", "p1"), _dom("D2", "Revenue", "AVG(x)", "p2")]
    )
    assert any(c.item_type == "metric" and c.severity == "ERROR" for c in fed.collisions)


# ---------------------------------------------------------------- C10
def _osi_model_with_metric(name, expression):
    from semabridge.intermediate.models import OSIDataset, OSIMetric, OSIModel

    return OSIModel(
        unique_name="M",
        label="M",
        datasets=[OSIDataset(unique_name="T", source_table="T", columns=[])],
        metrics=[OSIMetric(unique_name=name, label=name, dataset="T", expression=expression)],
    )


def test_apply_reports_ignored_domain_formula_for_bi_measure():
    from semabridge.ontology.apply import apply_ontology_to_model

    model = _osi_model_with_metric("Revenue", "SUM(T[amount])")
    dom = DomainOntology(domain="Fin", metrics=[DomainMetric(name="Revenue", sql_expression="AVG(amount)")])
    report = apply_ontology_to_model(model, domains=[dom])
    assert len(report["formula_overrides_ignored"]) == 1
    assert report["formula_overrides_ignored"][0]["metric"] == "Revenue"
    assert any(w.startswith("Formula ignored") for w in report["warnings"])


def test_apply_accepts_equivalent_or_annotation_only_formula():
    from semabridge.ontology.apply import apply_ontology_to_model

    model = _osi_model_with_metric("Revenue", "SUM(T[amount])")
    same = DomainOntology(domain="Fin", metrics=[DomainMetric(name="Revenue", sql_expression='SUM("amount")')])
    bare = DomainOntology(domain="Fin2", metrics=[DomainMetric(name="Revenue", description="d")])
    assert apply_ontology_to_model(model, domains=[same])["formula_overrides_ignored"] == []
    assert apply_ontology_to_model(model, domains=[bare])["formula_overrides_ignored"] == []


def test_save_rejects_domain_shadowing_bi_measure_of_same_project(tmp_path):
    from semabridge.ontology.federated_loader import OntologyValidationError
    from semabridge.ontology.store import DomainOntologyStore

    store = DomainOntologyStore(storage_dir=tmp_path)
    bi = DomainOntology.model_validate({
        "domain": "PBIX", "source_project_id": "p1",
        "metrics": [{"name": "Net", "origin": "bi", "sql_expression": "SUM(net)"}],
    })
    store.add_or_update_domain(bi)
    store.set_project_domains("p1", ["PBIX"])
    shadow = DomainOntology.model_validate({
        "domain": "Custom", "source_project_id": "p1",
        "metrics": [{"name": "net", "sql_expression": "SUM(gross)"}],
    })
    with pytest.raises(OntologyValidationError) as exc:
        store.add_or_update_domain(shadow, enforce_bi_formula_readonly=True)
    assert "read-only" in str(exc.value)
    # annotation-only and same-formula entries are fine; unrelated projects are not checked
    ok = DomainOntology.model_validate({
        "domain": "Custom", "source_project_id": "p1",
        "metrics": [{"name": "Net", "description": "x"}, {"name": "Other", "sql_expression": "SUM(a)"}],
    })
    store.add_or_update_domain(ok, enforce_bi_formula_readonly=True)
    other = DomainOntology.model_validate({
        "domain": "Elsewhere", "source_project_id": "p2",
        "metrics": [{"name": "Net", "sql_expression": "SUM(gross)"}],
    })
    store.add_or_update_domain(other, enforce_bi_formula_readonly=True)


# ---------------------------------------------------------------- C12
def _conflicting_store(tmp_path):
    from semabridge.ontology.store import DomainOntologyStore

    store = DomainOntologyStore(storage_dir=tmp_path)
    store.add_or_update_domain(DomainOntology(
        domain="Finance", metrics=[DomainMetric(name="Revenue", sql_expression="SUM(net)", description="finance def")]))
    store.add_or_update_domain(DomainOntology(
        domain="Sales", metrics=[DomainMetric(name="Revenue", sql_expression="SUM(gross)", description="sales def")]))
    store.set_project_domains("p1", ["Finance", "Sales"])
    return store


def _apply(store, **kw):
    from semabridge.ontology.apply import apply_project_ontology

    model = _osi_model_with_metric("Revenue", "SUM(T[net])")
    report = apply_project_ontology("p1", model, store=store, overrides={}, model_names=["M"], **kw)
    return model, report


def test_unresolved_cross_domain_conflict_blocks(tmp_path):
    _, report = _apply(_conflicting_store(tmp_path))
    assert len(report["blocking_conflicts"]) == 1
    assert report["blocking_conflicts"][0]["resolution_options"] == ["priority", "namespace"]


def test_priority_resolution_uses_ordered_domains(tmp_path):
    store = _conflicting_store(tmp_path)
    model, report = _apply(store, conflict_resolution="priority", domain_priority=["Sales"])
    assert report["blocking_conflicts"] == []
    res = report["conflict_resolution"]["resolved"][0]
    assert res["winner"] == "Sales" and res["overridden"] == ["Finance"]
    assert model.metrics[0].description == "sales def"
    # default order (attachment order) -> Finance wins
    model2, report2 = _apply(store, conflict_resolution="priority")
    assert report2["conflict_resolution"]["resolved"][0]["winner"] == "Finance"
    assert model2.metrics[0].description == "finance def"


def test_namespace_resolution_keeps_both_prefixed(tmp_path):
    from semabridge.ontology.apply import resolve_domain_conflicts

    store = _conflicting_store(tmp_path)
    domains = store.get_project_domains("p1")
    resolved, rep = resolve_domain_conflicts(domains, "namespace")
    names = sorted(m.name for d in resolved for m in d.metrics)
    assert names == ["Finance_Revenue", "Sales_Revenue"]
    assert rep["resolved"][0]["namespaced"] == {"Finance": "Finance_Revenue", "Sales": "Sales_Revenue"}
    # originals untouched
    assert [m.name for d in domains for m in d.metrics] == ["Revenue", "Revenue"]
    _, report = _apply(store, conflict_resolution="namespace")
    assert report["blocking_conflicts"] == []


def test_saved_project_choice_is_used(tmp_path):
    store = _conflicting_store(tmp_path)
    store.set_conflict_resolution("p1", "priority")
    _, report = _apply(store)
    assert report["blocking_conflicts"] == []
    assert report["conflict_resolution"]["mode"] == "priority"
    store.set_conflict_resolution("p1", None)
    assert store.get_conflict_resolution("p1") is None


def test_conflict_resolution_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import semabridge.api.routers.domain_ontology_router as dor
    from semabridge.api.main import app

    store = _conflicting_store(tmp_path)
    monkeypatch.setattr(dor, "ontology_store", store)
    client = TestClient(app)
    state = client.get("/api/ontology/projects/p1/conflict-resolution").json()
    assert state["mode"] is None and len(state["conflicts"]) == 1
    res = client.put("/api/ontology/projects/p1/conflict-resolution", json={"mode": "priority", "priority": ["Sales"]})
    assert res.status_code == 200, res.text
    assert res.json()["mode"] == "priority" and res.json()["priority"] == ["Sales", "Finance"]
    bad = client.put("/api/ontology/projects/p1/conflict-resolution", json={"mode": "random"})
    assert bad.status_code == 422


# ---------------------------------------------------------------- C14
def test_apply_reports_description_and_synonym_changes():
    from semabridge.ontology.apply import apply_ontology_to_model

    model = _osi_model_with_metric("Revenue", "SUM(T[amount])")
    dom = DomainOntology(domain="Fin", metrics=[DomainMetric(name="Revenue", description="Net revenue", synonyms=["Sales"])])
    rep = apply_ontology_to_model(model, domains=[dom])
    assert [c["name"] for c in rep["changes"]["descriptions"]] == ["Revenue"]
    assert rep["changes"]["synonyms"][0]["new"] == ["Sales"]
    # applying again changes nothing
    rep2 = apply_ontology_to_model(model, domains=[dom])
    assert rep2["changes"] == {"descriptions": [], "synonyms": []}


def test_changed_verified_queries_lists_only_new_or_modified(monkeypatch):
    from types import SimpleNamespace

    from semabridge.api.controllers import mappings_controller as mc
    from semabridge.api.services import project_shared

    deployed = {"verified_queries": [{"name": "Q1", "sql": "select 1"}, {"name": "Q2", "sql": "select 2"}]}
    monkeypatch.setattr(
        project_shared.db_manager, "get_last_deployed_snapshot",
        lambda **kw: SimpleNamespace(sml_blob=deployed),
    )
    current = [
        {"name": "Q1", "sql": "select   1"},
        {"name": "Q2", "sql": "select 22"},
        {"name": "Q3", "sql": "select 3"},
    ]
    out = mc._changed_verified_queries("proj-x", ["M"], current)
    assert [(v["name"], v["action"]) for v in out] == [("Q2", "modified"), ("Q3", "added")]
    assert mc._changed_verified_queries("preview", ["M"], current) == []


# ---------------------------------------------------------------- C7
def test_caller_view_name_is_qualified_with_project_deployment(monkeypatch):
    from semabridge.api.routers import ai_router
    from semabridge.repository import model_repository

    monkeypatch.setattr(
        model_repository.ModelRepository, "get_project_deployment_info",
        lambda self, pid: {"database": "DBX", "schema": "My Schema"},
    )
    assert ai_router._qualify_view_name("SV", "p1") == 'DBX."My Schema".SV'
    assert ai_router._qualify_view_name("S.SV", "p1") == "DBX.S.SV"
    assert ai_router._qualify_view_name("A.B.SV", "p1") == "A.B.SV"
    assert ai_router._qualify_view_name('"x.y"', "p1") == 'DBX."My Schema"."x.y"'
    assert ai_router._qualify_view_name("SV", None) == "SV"


def test_view_name_left_alone_without_deployment(monkeypatch):
    from semabridge.api.routers import ai_router
    from semabridge.repository import model_repository

    monkeypatch.setattr(model_repository.ModelRepository, "get_project_deployment_info", lambda self, pid: {})
    assert ai_router._qualify_view_name("SV", "p1") == "SV"


# ---------------------------------------------------------------- C21
def test_ontology_emitter_has_no_invented_defaults():
    from semabridge.connectors.ontology_emitter import OntologyEmitter
    from semabridge.intermediate.models import OSIModel

    with pytest.raises(TypeError):
        OntologyEmitter()  # database is required
    with pytest.raises(ValueError):
        OntologyEmitter(target_database="  ")
    model = OSIModel(unique_name="M", label="M", datasets=[], metrics=[])
    ddl = OntologyEmitter(target_database="DB").emit_ontology_ddl(model)
    assert "PROD_DWH" not in ddl and "COMPUTE_WH" not in ddl
    assert "WAREHOUSE not configured" in ddl
    assert "\nCREATE OR REPLACE CORTEX SEARCH SERVICE" not in ddl
    ddl2 = OntologyEmitter(target_database="DB", warehouse="WH", semantic_view="DB.S.V").emit_ontology_ddl(model)
    assert "WAREHOUSE = WH" in ddl2


def test_knowledge_graph_sql_requires_real_target(monkeypatch):
    from fastapi.testclient import TestClient

    import semabridge.api.routers.domain_ontology_router as dor
    import semabridge.api.routers.export_router as er
    from semabridge.api.main import app
    from semabridge.intermediate.models import OSIModel

    async def fake_load(pid):
        return OSIModel(unique_name="M", label="M", datasets=[], metrics=[])

    monkeypatch.setattr(er, "_load_osi_model", fake_load)
    monkeypatch.setattr(dor, "_project_kg_target", lambda pid: {})
    client = TestClient(app)
    res = client.get("/api/ontology/projects/p-kg/knowledge-graph.sql")
    assert res.status_code == 400 and "database" in res.text and "warehouse" in res.text
    monkeypatch.setattr(
        dor, "_project_kg_target",
        lambda pid: {"database": "RDB", "schema": "RS", "semantic_view": "RDB.RS.VIEW1", "warehouse": "RWH"},
    )
    res = client.get("/api/ontology/projects/p-kg/knowledge-graph.sql")
    assert res.status_code == 200, res.text
    assert "RDB._SEMABRIDGE_ONTOLOGY" in res.text and "PROD_DWH" not in res.text


# ---------------------------------------------------------------- D2 hardening / offline draft
def test_dataset_label_that_only_respaces_name_is_not_a_synonym():
    from semabridge.intermediate.models import OSIDataset, OSIModel

    model = OSIModel(
        unique_name="M", label="M",
        datasets=[
            OSIDataset(unique_name="fact_sales", label="Fact Sales", source_table="fact_sales", columns=[]),
            OSIDataset(unique_name="dim_cust", label="Customers", source_table="dim_cust", columns=[]),
        ],
        metrics=[],
    )
    dom = FederatedOntologyLoader.from_osi_model(model=model, domain_name="D")
    by = {c.name: c for c in dom.concepts}
    fact = next(c for n, c in by.items() if n.casefold() == "fact_sales")
    cust = next(c for n, c in by.items() if n.casefold() == "dim_cust")
    assert "Fact Sales" not in fact.synonyms
    assert "Customers" in cust.synonyms


def test_local_plan_passes_offline_draft_flag():
    from semabridge.ai.project_connection import ProjectScope
    from semabridge.api.routers import ai_router

    seen = {}

    class _Plan:
        explanation = ""
        warnings = []

    class _Eng:
        def resolve_query(self, prompt, **kw):
            seen.update(kw)
            return _Plan()

    ai_router._local_plan(_Eng(), "q", "DATABRICKS", ProjectScope(project_id=None), offline_label=False)
    assert seen["offline_draft"] is False
    ai_router._local_plan(_Eng(), "q", "DATABRICKS", ProjectScope(project_id=None), offline_label=True)
    assert seen["offline_draft"] is True
