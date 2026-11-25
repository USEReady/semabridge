"""Databricks integration batch 2: re-planning from the dry run's cached inputs.

Covers the cache written by Step 6b, the re-plan (saved names + planner
options), revision ordering (an older response never replaces a newer plan),
stale state after a failure, the plan endpoints, planner options saved to the
project config, and the planner owning Databricks name clashes on the screen.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.sml.models import Cardinality, CrossFilterDirection, SMLColumn, SMLDataset, SMLMetric, SMLModel, SMLRelationship
from semabridge.target_backends.databricks import replan
from semabridge.target_backends.databricks.tables import TablePlan


def _model() -> SMLModel:
    def t(name, *cols, fact=False):
        return SMLDataset(unique_name=name, label=name, columns=[SMLColumn(unique_name=c, label=c) for c in cols], is_fact=fact)

    def m(name, dax, sql, flags=()):
        return SMLMetric(unique_name=name, label=name, dataset="Sales", expression=dax, sql_expression=sql,
                         sync_enabled=True, advisory_categories=list(flags))

    return SMLModel(
        unique_name="m",
        datasets=[t("Store", "StoreID", "Name"), t("Sales", "SaleID", "StoreID", "Amount", fact=True)],
        relationships=[SMLRelationship(unique_name="r", from_dataset="Sales", from_columns=["StoreID"], to_dataset="Store",
                                       to_columns=["StoreID"], cardinality=Cardinality("many-to-one"), is_active=True,
                                       cross_filter=CrossFilterDirection.SINGLE)],
        metrics=[
            m("Total Revenue", "SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`::DOUBLE)"),
            m("Revenue East", "CALCULATE([Total Revenue], Store[Name] = \"East\")",
              "SUM(CASE WHEN STORE.`NAME` = 'East' THEN SALES.`AMOUNT`::DOUBLE END)",
              ["calculate_filter_semantics_difference"]),
            m("Revenue East x2", "[Revenue East] * 2",
              "SUM(CASE WHEN STORE.`NAME` = 'East' THEN SALES.`AMOUNT`::DOUBLE END) * 2",
              ["calculate_filter_semantics_difference"]),
        ],
    )


def _table_plan() -> TablePlan:
    """The real matcher's plan (no table found in the schema -> empty stub tables)."""
    from semabridge.target_backends.databricks.tables import plan_tables

    return plan_tables(_model(), {("workspace", "sb_tests"): {}}, source_schemas=[("workspace", "sb_tests")],
                       on_missing_source="stub")


DEFAULTS = {"calculate_mode": "approximate", "view_mode": "separate_views", "capabilities": []}


@pytest.fixture
def cached(tmp_path):
    out = replan.output_dir("preview-abc", None, root=tmp_path)
    meta = replan.write_cache(out, sml_model=_model(), table_plan=_table_plan(), security_roles=0, defaults=DEFAULTS,
                              project_planner={}, cache_ref={"project_id": "preview-abc", "model_subdir": None},
                              revision=1000)
    from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views

    first = plan_metric_views(_model(), _table_plan(), PlannerSettings(security_roles=0)).as_dict()
    first["meta"] = meta
    assert replan.record_plan(out, first, "t", reset=True)
    return out


def test_table_plan_round_trips_through_the_cache(cached):
    data = replan.read_cache(cached)
    rebuilt = replan.table_plan_from_dict(data["table_plan"])
    assert replan.table_plan_to_dict(rebuilt) == replan.table_plan_to_dict(_table_plan())
    assert [b.action for b in rebuilt.bindings] == ["stub", "stub"]
    assert rebuilt.bindings[0].stub_columns == _table_plan().bindings[0].stub_columns


def test_replan_applies_saved_names_and_options(cached):
    res = replan.replan(cached, [{"source_path": "datasets.Store.columns.Name", "target_name": "Store Name", "origin": "user"}],
                        {"calculate_mode": "approximate", "excluded_measures": ["Revenue East"]}, 2000, "t")
    assert res["status"] == "valid" and not res["superseded"]
    plan = res["plan"]
    assert plan["meta"]["source"] == "replan" and plan["meta"]["revision"] == 2000
    assert plan["meta"]["basis"]["uc_recheck_required_before_deploy"] is True
    fields = {f["column"]: f["name"] for v in plan["views"] for f in v["fields"] if f["dataset"] == "Store"}
    assert fields["Name"] == "store_name"
    left = {lo["measure"]: lo["reason"] for lo in plan["left_out"]}
    assert left["Revenue East"] == "user_excluded"
    assert left["Revenue East x2"] == "depends_on"          # exclusion propagates to dependents
    on_disk = json.loads((cached / replan.PLAN_JSON).read_text())
    assert on_disk["meta"]["revision"] == 2000


def test_strict_mode_leaves_approximate_measures_out(cached):
    res = replan.replan(cached, [], {"calculate_mode": "strict"}, 2000, "t")
    placed = {m["source_name"] for v in res["plan"]["views"] for m in v["measures"]}
    assert placed == {"Total Revenue"}


def test_an_older_response_never_replaces_a_newer_plan(cached):
    new = replan.replan(cached, [], {"calculate_mode": "strict"}, 3000, "t")
    old = replan.replan(cached, [], {"calculate_mode": "approximate"}, 2500, "t")
    assert not new["superseded"] and old["superseded"]
    on_disk = json.loads((cached / replan.PLAN_JSON).read_text())
    assert on_disk["meta"]["revision"] == 3000 and on_disk["settings"]["calculate_mode"] == "strict"


def test_a_failed_replan_marks_the_plan_stale_until_a_newer_success(cached):
    bad = replan.replan(cached, [], {"calculate_mode": "nonsense"}, 4000, "t")
    assert bad["status"] == "failed" and bad["stale"] and bad["plan"] is None
    plan, state = replan.recorded_plan(cached)
    assert state["status"] == "stale" and plan["meta"]["status"] == "stale" and plan["meta"]["error"]
    # an older success cannot clear it; a newer one does
    assert replan.replan(cached, [], None, 3500, "t")["superseded"]
    assert replan.recorded_plan(cached)[1]["status"] == "stale"
    ok = replan.replan(cached, [], None, 5000, "t")
    assert ok["status"] == "valid" and replan.recorded_plan(cached)[1]["status"] == "valid"


def test_a_new_dry_run_resets_the_recorded_plan(cached):
    replan.replan(cached, [], None, 9000, "t")
    plan = json.loads((cached / replan.PLAN_JSON).read_text())
    plan["meta"]["revision"] = 10
    assert replan.record_plan(cached, plan, "t", reset=True)
    assert replan.read_state(cached)["revision"] == 10


def test_replan_without_a_dry_run_fails_cleanly(tmp_path):
    res = replan.replan(tmp_path / "none", [], None, 1, "t")
    assert res["status"] == "failed" and "dry run" in res["error"]


# --- Step 6b writes the cache and the plan meta -------------------------------------------

def test_step6b_writes_the_cache_and_plan_meta(tmp_path, monkeypatch):
    from semabridge.core.engine.targets.databricks import _run_databricks_planner

    monkeypatch.chdir(tmp_path)
    ctx = SimpleNamespace(
        sml_model=_model(), project_id="preview-xyz", physical_namespace=None, databricks_name_requests=None,
        behavior=SimpleNamespace(databricks=SimpleNamespace(planner_calculate_mode="approximate",
                                                            planner_view_mode="separate_views", planner_capabilities=[])),
        config=SimpleNamespace(target=SimpleNamespace()), source_type="pbix", osi_model=SimpleNamespace(metadata={}),
        pbix_parser="schema", source_format=SimpleNamespace(tmsl_definition={"model": {"tables": []}}),
    )
    _run_databricks_planner(None, ctx, _table_plan())
    meta = ctx.databricks_view_plan["meta"]
    assert meta["cache_ref"] == {"project_id": "preview-xyz", "model_subdir": None}
    assert meta["source"] == "dry_run" and meta["basis"]["table_check"] == "live" and meta["basis"]["table_check_at"]
    out = Path("output/databricks/preview-xyz")
    assert (out / replan.CACHE_FILE).exists() and (out / replan.PLAN_MD).exists()
    assert replan.read_state(out)["revision"] == meta["revision"]
    res = replan.replan(out, [], None, meta["revision"] + 1, "t")
    assert res["status"] == "valid"


# --- endpoints ---------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    import semabridge.api.controllers.mappings_controller as mc
    import semabridge.api.services.project_shared as ps

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(mc, "_assert_project_access", lambda *a, **k: None)
    configs = {}
    monkeypatch.setattr(ps, "_compat_project_configs", configs)
    pdir = tmp_path / "Config" / "projects"
    pdir.mkdir(parents=True)
    monkeypatch.setattr(ps, "_compat_project_yaml_path", lambda pid: pdir / f"{pid}.yaml")
    monkeypatch.setattr(ps, "_compat_project_yaml_paths", lambda pid: [pdir / f"{pid}.yaml"])
    app = FastAPI()
    app.include_router(mc.router)
    # a cached wizard dry run
    out = replan.output_dir("preview-abc")
    replan.write_cache(out, sml_model=_model(), table_plan=_table_plan(), security_roles=0, defaults=DEFAULTS,
                       project_planner={}, cache_ref={"project_id": "preview-abc", "model_subdir": None}, revision=1)
    return TestClient(app)


def test_wizard_replan_endpoint(client):
    r = client.post("/api/projects/preview/databricks/replan", json={
        "cache_project_id": "preview-abc", "revision": 50,
        "mappings_overrides": [{"source_path": "metrics.Total Revenue", "target_name": "revenue", "origin": "user"}],
        "planner": {"calculate_mode": "strict"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "valid"
    names = {m["source_name"]: m["name"] for v in body["plan"]["views"] for m in v["measures"]}
    assert names == {"Total Revenue": "revenue"}
    assert client.post("/api/projects/preview/databricks/replan", json={"cache_project_id": "proj-x"}).status_code == 400
    assert client.post("/api/projects/preview/databricks/replan",
                       json={"cache_project_id": "preview-abc", "planner": {"calculate_mode": "bad"}}).status_code == 400
    got = client.get("/api/projects/preview/databricks/plan", params={"cache_project_id": "preview-abc"})
    assert got.status_code == 200 and got.json()["plan"]["meta"]["revision"] == 50
    assert client.get("/api/projects/preview/databricks/plan",
                      params={"cache_project_id": "preview-none"}).status_code == 404


def _save(pid, cfg):
    from semabridge.api.services import project_config_store as store

    store.save_project_config(pid, yaml.safe_dump(cfg), reason="create")


def test_planner_options_are_saved_to_the_project_config(client):
    from semabridge.api.services import project_config_store as store

    pid = f"proj-opts-{time.time_ns()}"
    _save(pid, {"project_name": "P", "targets": [{"type": "databricks", "catalog": "workspace"}]})
    r = client.put(f"/api/projects/{pid}/databricks/planner-options",
                   json={"calculate_mode": "strict", "excluded_measures": ["B", "A", "A"]})
    assert r.status_code == 200, r.text
    target = yaml.safe_load(store.get_project_config_text(pid, use_cache=False))["targets"][0]
    assert target["planner"] == {"calculate_mode": "strict", "excluded_measures": ["A", "B"]}
    assert target["catalog"] == "workspace"
    assert client.put("/api/projects/preview-abc/databricks/planner-options", json={}).status_code == 409
    sf = f"proj-sf-{time.time_ns()}"
    _save(sf, {"project_name": "S", "targets": [{"type": "snowflake"}]})
    assert client.put(f"/api/projects/{sf}/databricks/planner-options", json={"calculate_mode": "strict"}).status_code == 400


def test_real_project_replan_uses_its_stored_names_and_options(client):
    pid = f"proj-real-{time.time_ns()}"
    _save(pid, {"project_name": "R", "targets": [{"type": "databricks", "planner": {"calculate_mode": "strict"}}],
                "mappings_overrides": [{"source_path": "metrics.Total Revenue", "target_name": "rev", "origin": "user"}]})
    r = client.post(f"/api/projects/{pid}/databricks/replan", json={"cache_project_id": "preview-abc", "revision": 70})
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["settings"]["calculate_mode"] == "strict"
    assert [m["name"] for v in plan["views"] for m in v["measures"]] == ["rev"]


# --- the planner owns Databricks clashes on the dry-run screen -------------------------------

def test_databricks_rows_never_show_the_screen_s_own_clash_verdicts():
    from semabridge.api.controllers.mappings_controller import _planner_owns_databricks_clashes

    rows = [{"collision_detected": True, "validation_status": "collision", "validation_code": "NAME_COLLISION",
             "validation_message": "clash", "status": "collision", "mapping_status": "collision", "collision_group": "g"},
            {"collision_detected": False, "validation_status": "invalid", "validation_code": "UNSUPPORTED_CHARACTERS",
             "validation_message": "bad", "status": "auto"}]
    _planner_owns_databricks_clashes(rows)
    assert rows[0]["collision_detected"] is False and rows[0]["validation_code"] == "OK" and rows[0]["status"] == "auto"
    assert rows[1]["validation_code"] == "UNSUPPORTED_CHARACTERS"     # other checks untouched
