"""Planner path: starting a sync is the approval to deploy."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from semabridge.core.engine.deployment import databricks_planner_deploy as pd
from semabridge.core.engine.exceptions import DeploymentError

PLAN = {
    "settings": {"output_catalog": "workspace", "output_schema": "default"},
    "views": [
        {"name": "sales_metric_view", "measures": [{"name": "total_units", "source_name": "Total Units"},
                                                   {"name": "revenue", "source_name": "Revenue"}]},
        {"name": "returns_metric_view", "measures": [{"name": "returned", "source_name": "Returned Units"}]},
    ],
}


def _ctx():
    return SimpleNamespace(project_id="proj-dbt2", physical_namespace=None, run_id="run-1",
                           databricks_view_plan=PLAN, target_warnings=[])


@pytest.fixture
def wired(monkeypatch):
    from semabridge.api.controllers import databricks_deploy_controller as ctl
    from semabridge.api.controllers import mappings_controller as mc
    from semabridge.target_backends.databricks.deploy import execute, intent, store

    target = {"type": "databricks", "catalog": "workspace", "schema": "default"}
    monkeypatch.setattr(mc, "_stored_config", lambda pid: {})
    monkeypatch.setattr(mc, "_effective_target", lambda cfg: target)
    monkeypatch.setattr(ctl, "_plan", lambda pid, sub, cache=None: ("/tmp/x", PLAN, 3))
    monkeypatch.setattr(ctl, "_connection", lambda t: SimpleNamespace(host="h"))
    monkeypatch.setattr(ctl, "_clients", lambda info, run_id=None: ("live", "writer", "wh"))
    monkeypatch.setattr(ctl, "_allow_list", lambda: [])
    monkeypatch.setattr(store, "sweep_stale_runs", lambda *a: None)
    import semabridge.core.engine.targets.databricks as tdb

    monkeypatch.setattr(tdb, "stub_form_for_run", lambda ctx: "empty_table")
    calls = {}

    def _create(req, live, created_by):
        calls["create"] = (req, created_by)
        return {"preview": {"status": "ready"}, "intent": {"intent_id": "i1"}}

    def _confirm(intent_id, **kw):
        calls["confirm"] = (intent_id, kw)
        return calls.get("result", {"status": "succeeded", "results": [
            {"kind": "view", "name": "sales_metric_view", "fqn": "workspace.default.sales_metric_view", "outcome": "created"},
            {"kind": "view", "name": "returns_metric_view", "fqn": "workspace.default.returns_metric_view", "outcome": "updated"},
        ]})

    monkeypatch.setattr(intent, "create_intent", _create)
    monkeypatch.setattr(execute, "confirm_intent", _confirm)
    return target, calls


def test_no_staging_schema_stops_with_instructions(wired, monkeypatch):
    from semabridge.api.controllers import databricks_deploy_controller as ctl

    monkeypatch.setattr(ctl, "_global_staging_default", lambda: "")
    with pytest.raises(DeploymentError) as exc:
        pd.deploy_planned_views(_ctx())
    assert "staging schema" in str(exc.value) and "never creates schemas" in str(exc.value)


def test_sync_previews_and_confirms_itself(wired):
    target, calls = wired
    target["staging_schema"] = "workspace.semabridge_staging"
    ctx = _ctx()
    pd.apply_deploy_result(ctx, pd.deploy_planned_views(ctx))
    req, created_by = calls["create"]
    assert created_by == "sync:run-1" and req.staging == ("workspace", "semabridge_staging")
    intent_id, kw = calls["confirm"]
    assert intent_id == "i1" and kw["acknowledge_destination_access"] is True and kw["current_revision"] == 3
    assert ctx.deployed_view_name == "sales_metric_view"
    assert ctx.target_artifact_path == "databricks://workspace/default/sales_metric_view"
    assert ctx.target_warnings == []


def test_partial_deploy_is_a_warning_and_failed_is_an_error(wired):
    target, calls = wired
    target["staging_schema"] = "workspace.semabridge_staging"
    calls["result"] = {"status": "partial", "results": [
        {"kind": "view", "name": "a", "fqn": "w.d.a", "outcome": "created"},
        {"kind": "view", "name": "b", "fqn": "w.d.b", "outcome": "failed", "detail": "validation"},
    ]}
    ctx = _ctx()
    pd.apply_deploy_result(ctx, pd.deploy_planned_views(ctx))
    assert any("w.d.b" in w for w in ctx.target_warnings)
    assert any("partial: 1 of 2" in w for w in ctx.target_warnings)
    calls["result"] = {"status": "failed", "reason": "lease lost", "results": []}
    with pytest.raises(DeploymentError, match="Reconcile"):
        pd.apply_deploy_result(_ctx(), pd.deploy_planned_views(_ctx()))


def test_ontology_follows_the_planned_views(tmp_path, monkeypatch):
    from semabridge.ontology import auto_bootstrap
    from semabridge.ontology import store as store_mod
    from semabridge.ontology.models import DomainOntology

    store = store_mod.DomainOntologyStore(storage_dir=tmp_path)
    store.add_or_update_domain(DomainOntology.model_validate({
        "domain": "Store Sales 2", "source_project_id": "proj-dbt2",
        "semantic_view_name": "workspace.default.Store_Sales_2_metric_view",
        "metrics": [{"name": "Total Units", "sql_expression": "SUM(u)"},
                    {"name": "Revenue", "sql_expression": "SUM(r)"},
                    {"name": "Returned Units", "sql_expression": "SUM(x)"}],
    }))
    monkeypatch.setattr(store_mod, "ontology_store", store)
    assert auto_bootstrap.sync_planned_view_names("proj-dbt2", PLAN) == 3
    dom = store.get_domain("Store Sales 2")
    assert dom.semantic_view_name == "workspace.default.sales_metric_view"
    by = {m.name: m.semantic_view_name for m in dom.metrics}
    assert by["Returned Units"] == "workspace.default.returns_metric_view"
