"""Ontology target integration: TargetRules.features, the read-only Databricks
facts adapter (planned vs last verified, occurrences, left-out reasons, ownership,
unavailable bundles) and the API guard for Snowflake-only actions."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest
import yaml

from semabridge.ontology.targets import item_key, ontology_adapter_for
from semabridge.ontology.targets.databricks import DatabricksOntologyAdapter
from semabridge.target_backends.databricks.deploy.definitions import definition_hash
from semabridge.target_rules import DATABRICKS_RULES, SNOWFLAKE_RULES, TargetFeatures

DEST = "workspace.sb"


# --- TargetRules.features ----------------------------------------------------------------------


def test_snowflake_features_are_all_on_and_databricks_all_off() -> None:
    sf = asdict(SNOWFLAKE_RULES.features)
    assert sf.pop("sql_label") == "Snowflake SQL" and all(sf.values())
    db = asdict(DATABRICKS_RULES.features)
    assert db.pop("sql_label") == "Databricks SQL" and not any(db.values())


def test_expressiveness_capabilities_are_unchanged() -> None:
    sf, db = SNOWFLAKE_RULES.capabilities, DATABRICKS_RULES.capabilities
    assert (sf.text_measures, sf.window_metrics, sf.nested_joins, sf.bridge_views, sf.enriched_helper_columns) == \
        (False, True, True, False, True)
    assert (db.text_measures, db.window_metrics, db.nested_joins, db.bridge_views, db.enriched_helper_columns) == \
        (False, False, True, True, False)


def test_target_rules_default_features_are_all_off() -> None:
    assert not any(v for k, v in asdict(TargetFeatures()).items() if k != "sql_label")


# --- fixtures: plan + revision bundles on disk ----------------------------------------------------------


def _plan(revision: int, owner: str = "proj-1", subdir=None, expr: str = "SUM(source.`amount`::DOUBLE)") -> Dict[str, Any]:
    return {
        "settings": {"output_location": DEST},
        "meta": {"revision": revision, "status": "valid",
                 "cache_ref": {"project_id": "preview-x", "model_subdir": subdir, "binding_project_id": owner}},
        "views": [
            {"name": "sales_metrics",
             "fields": [{"name": "store_name", "dataset": "Store", "column": "Store Name", "expr": "`store`.`name`",
                         "readiness": "structure_only",
                         "source_id": {"kind": "field", "table": "Store", "name": "Store Name"}}],
             "measures": [{"name": "total_sales", "source_name": "Total Sales", "expr": expr,
                           "readiness": "structure_only", "warnings": [],
                           "source_id": {"kind": "measure", "table": "", "name": "Total Sales"}},
                          {"name": "sales_ly", "source_name": "Sales LY", "expr": "SUM(x)",
                           "warnings": ["approximate: CALCULATE filters may differ"],
                           "source_id": {"kind": "measure", "table": "", "name": "Sales LY"}}]},
            {"name": "store_metrics",
             "fields": [{"name": "store_name", "dataset": "Store", "column": "Store Name", "expr": "source.`name`",
                         "source_id": {"kind": "field", "table": "Store", "name": "Store Name"}}],
             "measures": [{"name": "total_sales", "source_name": "Total Sales", "expr": "SUM(sales.`amount`)",
                           "source_id": {"kind": "measure", "table": "", "name": "Total Sales"}}]},
        ],
        "left_out": [{"measure": "Share of Total", "reason": "conversion_failed",
                      "detail": "not applicable: needs window metric", "root_cause": None}],
    }


def _bundle(out_dir: Path, revision: int, view: str, measures: Dict[str, str]) -> str:
    doc = {"version": "1.1", "source": f"{DEST}.sales",
           "fields": [{"name": "store_name", "expr": "`store`.`name`"}],
           "measures": [{"name": n, "expr": e} for n, e in measures.items()]}
    text = yaml.safe_dump(doc, sort_keys=False)
    d = out_dir / "views" / str(revision)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{view}.yaml").write_bytes(text.encode("utf-8"))
    import hashlib

    (d / "manifest.json").write_text(json.dumps({"plan_revision": revision, "views": [
        {"view": view, "file": f"{view}.yaml", "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}]}),
        encoding="utf-8")
    return text


def _write_plan(root: Path, folder: str, plan: Dict[str, Any], subdir=None) -> Path:
    d = root / "databricks" / folder
    if subdir:
        d = d / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / "databricks_view_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return d


def _states(monkeypatch, states):
    from semabridge.target_backends.databricks.deploy import store

    monkeypatch.setattr(store, "deploy_states", lambda p, m, kind="view": states)


def _verified(revision, text=None, outcome="updated", run="r1"):
    return {"run_id": run, "outcome": outcome, "verified": outcome in ("created", "updated", "verified", "reconciled"),
            "plan_revision": revision, "definition_hash": definition_hash(text) if text else None}


# --- planned facts -----------------------------------------------------------------------------------


def test_planned_occurrences_left_out_and_approximation(tmp_path, monkeypatch) -> None:
    _write_plan(tmp_path, "preview-x", _plan(11))
    _states(monkeypatch, {})
    f = DatabricksOntologyAdapter(tmp_path).facts("proj-1")
    assert f.available and f.plan_revision == 11 and f.destination == DEST
    ts = f.items[item_key("measure", "", "Total Sales")]
    assert [(o.occurrence_id, o.planned.expression) for o in ts.occurrences] == [
        ("sales_metrics/total_sales", "SUM(source.`amount`::DOUBLE)"),
        ("store_metrics/total_sales", "SUM(sales.`amount`)")]
    assert all(o.verified is None for o in ts.occurrences)
    assert f.items[item_key("measure", "", "Sales LY")].occurrences[0].planned.approximate
    assert len(f.items[item_key("field", "Store", "Store Name")].occurrences) == 2
    assert f.items[item_key("measure", "", "Share of Total")].left_out["detail"] == "not applicable: needs window metric"


def test_other_projects_and_other_models_plans_are_never_read(tmp_path, monkeypatch) -> None:
    _write_plan(tmp_path, "preview-a", _plan(50, owner="someone-else"))
    _write_plan(tmp_path, "preview-b", _plan(60, subdir="model_b"), subdir="model_b")
    _states(monkeypatch, {})
    f = DatabricksOntologyAdapter(tmp_path).facts("proj-1")
    assert not f.available and f.items == {} and f.notes
    g = DatabricksOntologyAdapter(tmp_path).facts("proj-1", "model_b")
    assert g.plan_revision == 60


def test_the_newest_owned_plan_wins(tmp_path, monkeypatch) -> None:
    _write_plan(tmp_path, "preview-old", _plan(10, expr="OLD"))
    _write_plan(tmp_path, "preview-new", _plan(12, expr="NEW"))
    _states(monkeypatch, {})
    f = DatabricksOntologyAdapter(tmp_path).facts("proj-1")
    assert f.plan_revision == 12
    assert f.items[item_key("measure", "", "Total Sales")].occurrences[0].planned.expression == "NEW"


# --- verified facts ----------------------------------------------------------------------------------


def test_verified_expression_comes_from_the_deployed_revision_not_the_plan(tmp_path, monkeypatch) -> None:
    d = _write_plan(tmp_path, "preview-x", _plan(11, expr="SUM(source.`amount`) * 2"))
    text = _bundle(d, 10, "sales_metrics", {"total_sales": "SUM(source.`amount`)"})
    _states(monkeypatch, {f"{DEST}.sales_metrics": {"latest": _verified(10, text), "verified": _verified(10, text)}})
    occ = DatabricksOntologyAdapter(tmp_path).facts("proj-1").items[item_key("measure", "", "Total Sales")].occurrences[0]
    assert occ.planned.revision == 11 and occ.planned.expression == "SUM(source.`amount`) * 2"
    assert occ.verified.revision == 10 and occ.verified.expression == "SUM(source.`amount`)"
    assert occ.verified.status == "verified" and occ.verified.run_id == "r1"


def test_applied_unverified_keeps_the_last_verified_state_and_flags_it(tmp_path, monkeypatch) -> None:
    d = _write_plan(tmp_path, "preview-x", _plan(11))
    text = _bundle(d, 10, "sales_metrics", {"total_sales": "SUM(source.`amount`)"})
    _states(monkeypatch, {f"{DEST}.sales_metrics": {
        "latest": _verified(11, outcome="applied_unverified", run="r2"), "verified": _verified(10, text)}})
    occ = DatabricksOntologyAdapter(tmp_path).facts("proj-1").items[item_key("measure", "", "Total Sales")].occurrences[0]
    assert occ.verified.status == "verified" and occ.verified.revision == 10
    assert "unresolved" in (occ.verified.note or "")


def test_never_verified_view_is_not_reported_as_verified(tmp_path, monkeypatch) -> None:
    _write_plan(tmp_path, "preview-x", _plan(11))
    _states(monkeypatch, {f"{DEST}.sales_metrics": {"latest": _verified(11, outcome="applied_unverified"),
                                                   "verified": None}})
    occ = DatabricksOntologyAdapter(tmp_path).facts("proj-1").items[item_key("measure", "", "Total Sales")].occurrences[0]
    assert occ.verified.status == "applied_unverified" and occ.verified.expression is None


def test_missing_or_changed_bundle_is_unavailable_never_the_plan(tmp_path, monkeypatch) -> None:
    d = _write_plan(tmp_path, "preview-x", _plan(11))
    _states(monkeypatch, {f"{DEST}.sales_metrics": {"latest": _verified(7, "x: 1"), "verified": _verified(7, "x: 1")}})
    occ = DatabricksOntologyAdapter(tmp_path).facts("proj-1").items[item_key("measure", "", "Total Sales")].occurrences[0]
    assert occ.verified.status == "unavailable" and occ.verified.expression is None
    assert "no longer kept" in occ.verified.note
    # bundle present but not what was verified live
    _bundle(d, 7, "sales_metrics", {"total_sales": "SOMETHING ELSE"})
    occ = DatabricksOntologyAdapter(tmp_path).facts("proj-1").items[item_key("measure", "", "Total Sales")].occurrences[0]
    assert occ.verified.status == "unavailable" and "differs" in occ.verified.note


def test_deployed_but_no_longer_planned_is_still_shown(tmp_path, monkeypatch) -> None:
    d = _write_plan(tmp_path, "preview-x", _plan(11))
    text = _bundle(d, 10, "sales_metrics", {"total_sales": "SUM(a)", "old_measure": "SUM(b)"})
    _states(monkeypatch, {f"{DEST}.sales_metrics": {"latest": _verified(10, text), "verified": _verified(10, text)}})
    f = DatabricksOntologyAdapter(tmp_path).facts("proj-1")
    occs = [o for i in f.items.values() for o in i.occurrences if o.name == "old_measure"]
    assert len(occs) == 1 and occs[0].planned is None and occs[0].verified.expression == "SUM(b)"


def test_unreadable_deploy_records_give_a_note_not_an_error(tmp_path, monkeypatch) -> None:
    from semabridge.target_backends.databricks.deploy import store

    _write_plan(tmp_path, "preview-x", _plan(11))

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "deploy_states", boom)
    f = DatabricksOntologyAdapter(tmp_path).facts("proj-1")
    assert f.available and any("could not be read" in n for n in f.notes)


def test_registry_and_snowflake_adapter_store_nothing() -> None:
    assert ontology_adapter_for("Databricks").target == "databricks"
    f = ontology_adapter_for("snowflake").facts("p")
    assert f.target == "snowflake" and not f.available and f.items == {}
    with pytest.raises(KeyError):
        ontology_adapter_for("fabric")


# --- deploy store: last verified per object ---------------------------------------------------------


def test_deploy_states_keeps_the_last_verified_behind_a_newer_unverified_write() -> None:
    import uuid

    from semabridge.target_backends.databricks.deploy import store

    pid, fqn = f"p-{uuid.uuid4().hex[:8]}", f"workspace.s_{uuid.uuid4().hex[:6]}.v"
    for outcome, rev in (("created", 10), ("applied_unverified", 11)):
        run = f"r-{uuid.uuid4().hex[:8]}"
        store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=rev, writer_revision="1",
                        catalog="workspace", schema="s", stub_form="empty_table")
        store.record_object(run_id=run, project_id=pid, model_key="", kind="view", fqn=fqn, outcome=outcome,
                            definition_hash="h" if outcome == "created" else None)
    st = store.deploy_states(pid, "")[fqn]
    assert st["latest"]["outcome"] == "applied_unverified" and st["latest"]["plan_revision"] == 11
    assert st["verified"]["outcome"] == "created" and st["verified"]["plan_revision"] == 10
    assert st["verified"]["definition_hash"] == "h"


# --- API guard ------------------------------------------------------------------------------------


def _target(monkeypatch, target):
    from semabridge.api.services import project_shared

    monkeypatch.setattr(project_shared, "_compat_stored_target_type", lambda pid: target or "")


@pytest.mark.parametrize("target,refused", [("databricks", True), ("snowflake", False), (None, False)])
def test_require_target_feature(monkeypatch, target, refused) -> None:
    from fastapi import HTTPException

    from semabridge.api.services.target_features import require_target_feature

    _target(monkeypatch, target)
    if refused:
        with pytest.raises(HTTPException) as e:
            require_target_feature("p", "live_query")
        assert e.value.status_code == 409 and e.value.detail["code"] == "feature_not_available_for_target"
    else:
        require_target_feature("p", "live_query")
    require_target_feature(None, "live_query")       # no project: never refused


def test_target_features_for(monkeypatch) -> None:
    from semabridge.api.services.target_features import target_features_for

    _target(monkeypatch, "databricks")
    out = target_features_for("p")
    assert out["target"] == "databricks" and out["features"]["sql_label"] == "Databricks SQL"
    assert out["features"]["live_query"] is False
    _target(monkeypatch, "fabric")
    assert target_features_for("p")["features"]["live_query"] is False
    _target(monkeypatch, None)
    assert target_features_for("p") == {"target": None, "features": asdict(TargetFeatures())}


def test_reconciliation_refuses_snowflake_actions_on_databricks_projects(monkeypatch) -> None:
    from fastapi import HTTPException

    from semabridge.api.routers import reconciliation_router as rr

    _target(monkeypatch, "databricks")
    with pytest.raises(HTTPException) as e:
        rr.update_metric_sql_expression(rr.UpdateMetricSqlRequest(metric_name="m", sql_expression="SUM(1)",
                                                                  project_id="p"))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        rr.generate_reconciliation_queries(rr.GenerateQueriesRequest(
            metric_name="m", metric_sql_expression="SUM(1)", target_table_or_view="v", project_id="p"))
    assert e.value.status_code == 409


def test_rls_refused_only_with_a_databricks_project_id(monkeypatch) -> None:
    from fastapi import HTTPException

    from semabridge.api.routers import security_router as sr

    _target(monkeypatch, "databricks")
    with pytest.raises(HTTPException) as e:
        sr.convert_tmdl_role_to_snowflake(sr.TMDLToSnowflakeRequest(project_id="p"))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:   # no project id: old behaviour (400 for missing roles)
        sr.convert_tmdl_role_to_snowflake(sr.TMDLToSnowflakeRequest())
    assert e.value.status_code == 400
