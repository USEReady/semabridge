"""Parity check measure list for a project that has no ontology domain."""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.api.routers import reconciliation_router as rr


BLOB = {
    "datasets": [
        {"unique_name": "sales", "label": "Sales", "columns": [
            {"unique_name": "region", "label": "Region", "data_type": "string"},
            {"unique_name": "units", "label": "Units", "data_type": "integer"},
            {"unique_name": "secret", "label": "Secret", "data_type": "string", "is_hidden": True},
        ]},
    ],
    "metrics": [
        {"unique_name": "Total Units", "dataset": "sales", "expression": "SUM(Sales[Units])",
         "sql_expression": "SUM(units)"},
        {"unique_name": "Dropped", "dataset": "sales", "expression": "X", "sync_enabled": False},
    ],
}


def _patch(monkeypatch, runs):
    monkeypatch.setattr(rr, "_get_project_domains_for_validation", lambda pid: [])
    monkeypatch.setattr(rr, "_last_sync_runs", lambda pid, limit=20: runs)
    from semabridge.repository import model_repository

    class Repo:
        def get_project_deployment_info(self, pid):
            return {}

        def get_snapshot(self, sid):
            return SimpleNamespace(sml_blob=BLOB) if sid == "snap-1" else None

        def get_snapshot_for_run(self, rid):
            return SimpleNamespace(sml_blob=BLOB) if rid == "engine-1" else None

    monkeypatch.setattr(model_repository, "ModelRepository", Repo)


def test_project_without_ontology_lists_last_sync_measures(monkeypatch):
    _patch(monkeypatch, [{
        "run_id": "r1", "status": "success",
        "results": [{"model": "Store Sales", "view_name": "main.gold.store_sales",
                     "summary": {"sml_snapshot_id": "snap-1"}}],
        "summary": {"sml_snapshot_id": "snap-1"},
    }])
    out = rr.get_project_reconciliation_metrics("proj-dbt2")
    assert out["source"] == "last_sync"
    assert [m["name"] for m in out["metrics"]] == ["Total Units"]
    m = out["metrics"][0]
    assert m["dax_expression"] == "SUM(Sales[Units])"
    assert m["sql_expression"] == "SUM(units)"
    assert m["target_table_or_view"] == "main.gold.store_sales"
    assert m["target_kind"] == "semantic_view"
    assert m["dimensions"] == ["Sales.Region"]


def test_never_synced_project_has_no_measures(monkeypatch):
    _patch(monkeypatch, [])
    out = rr.get_project_reconciliation_metrics("proj-new")
    assert out["count"] == 0 and out["source"] == "none"


def test_skips_runs_without_snapshot(monkeypatch):
    _patch(monkeypatch, [
        {"run_id": "r2", "status": "success", "results": [], "summary": {}},
        {"run_id": "r1", "status": "success", "results": [], "summary": {"sml_snapshot_id": "snap-1"}},
    ])
    out = rr.get_project_reconciliation_metrics("proj-dbt2")
    assert [m["name"] for m in out["metrics"]] == ["Total Units"]


def test_older_run_without_snapshot_id_uses_engine_run(monkeypatch):
    _patch(monkeypatch, [{
        "run_id": "r1", "status": "success", "summary": {},
        "results": [{"model": "Store Sales 2", "run_id": "engine-1",
                     "view_name": "workspace.default.Store_Sales_2_metric_view", "summary": {}}],
    }])
    out = rr.get_project_reconciliation_metrics("proj-dbt2")
    assert [m["name"] for m in out["metrics"]] == ["Total Units"]
    assert out["metrics"][0]["target_table_or_view"] == "workspace.default.Store_Sales_2_metric_view"


def test_snapshot_for_run_repository_lookup():
    from semabridge.repository.model_repository import ModelRepository

    repo = ModelRepository()
    repo.commit_model(project_id="model-x", sml_json={"metrics": [{"unique_name": "M"}]},
                      status="success", duration_ms=1, run_id="engine-xyz")
    snap = repo.get_snapshot_for_run("engine-xyz")
    assert snap is not None and snap.sml_blob["metrics"][0]["unique_name"] == "M"
    assert repo.get_snapshot_for_run("nope") is None
