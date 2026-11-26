"""Every synced project gets an ontology; another project's domain is never taken over."""
from __future__ import annotations

import pytest

from semabridge.api.routers import domain_ontology_router as dor
from semabridge.ontology import auto_bootstrap


def _osi(label="Store Sales"):
    from semabridge.intermediate.models import OSIDataset, OSIMetric, OSIModel

    return OSIModel(
        unique_name=label, label=label,
        datasets=[OSIDataset(unique_name="Sales", source_table="SALES", columns=[])],
        metrics=[OSIMetric(unique_name="Total Units", label="Total Units", dataset="Sales", expression="SUM(Sales[Units])")],
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    from semabridge.api.routers import export_router
    from semabridge.api.services import project_shared
    from semabridge.ontology import store as store_mod
    from semabridge.sources.fabric import synonyms

    store = store_mod.DomainOntologyStore(storage_dir=tmp_path)
    monkeypatch.setattr(store_mod, "ontology_store", store)
    monkeypatch.setattr(dor, "ontology_store", store)
    models = {}

    async def _load(pid):
        return models.get(pid)

    async def _no_multi(req, pid, request):
        return None

    monkeypatch.setattr(export_router, "_load_osi_model", _load)
    monkeypatch.setattr(dor, "_sync_multi_model_project", _no_multi)
    monkeypatch.setattr(synonyms, "project_uses_fabric_report_layer", lambda pid: False)
    monkeypatch.setitem(project_shared._compat_projects, "proj-dbt2", {"display_name": "dbt2"})
    monkeypatch.setitem(project_shared._compat_projects, "proj-cxv", {"display_name": "cxv"})
    monkeypatch.delenv("SEMABRIDGE_AUTO_ONTOLOGY", raising=False)
    return store, models


def test_synced_project_without_ontology_gets_one(env):
    store, models = env
    models["proj-dbt2"] = _osi()
    assert auto_bootstrap.ensure_project_ontology("proj-dbt2")
    doms = store.domains_for("proj-dbt2")
    assert len(doms) == 1 and [m.name for m in doms[0].metrics] == ["Total Units"]
    # existing ontology is left alone
    assert auto_bootstrap.ensure_project_ontology("proj-dbt2") is None


def test_never_synced_project_is_skipped(env):
    assert auto_bootstrap.ensure_project_ontology("proj-dbt2") is None


def test_disabled(env, monkeypatch):
    _, models = env
    models["proj-dbt2"] = _osi()
    monkeypatch.setenv("SEMABRIDGE_AUTO_ONTOLOGY", "false")
    assert auto_bootstrap.ensure_project_ontology("proj-dbt2") is None


def test_same_model_in_two_projects_gets_two_domains(env):
    store, models = env
    models["proj-dbt2"] = _osi("Competitive Marketing Analysis")
    models["proj-cxv"] = _osi("Competitive Marketing Analysis")
    auto_bootstrap.ensure_project_ontology("proj-dbt2")
    auto_bootstrap.ensure_project_ontology("proj-cxv")
    a = store.domains_for("proj-dbt2")
    b = store.domains_for("proj-cxv")
    assert a[0].domain == "Competitive Marketing Analysis"
    assert b[0].domain == "cxv · Competitive Marketing Analysis"
    assert a[0].source_project_id == "proj-dbt2"


def test_backfill_counts_created(env):
    _, models = env
    models["proj-dbt2"] = _osi()
    assert auto_bootstrap.backfill(["proj-dbt2", "proj-cxv"]) == 1


def test_databricks_view_name_keys_to_its_model():
    from semabridge.ontology.project_models import _view_model_base
    from semabridge.ontology.store import model_key

    assert model_key(_view_model_base("main.default.customerfeedback_1_metric_view")) == model_key("customerfeedback_1")
    assert model_key(_view_model_base("SALES_SEMANTIC")) == model_key("SALES")
    assert _view_model_base("`workspace`.`default`.`Store_Sales_2_metric_view`") == "Store_Sales_2"
