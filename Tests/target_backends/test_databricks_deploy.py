"""Phase 3.8a: deploy preparation (offline, fake Databricks).

Covers the exact-statement guard, statement forms, definition normalisation,
the deploy store (record, names, journal, lease), name feedback into planning,
every pre-check and the reconciliation of uncertain operations. Nothing here
talks to Databricks.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from semabridge.target_backends.databricks import bundle
from semabridge.target_backends.databricks.deploy import definitions as D
from semabridge.target_backends.databricks.deploy import names as N
from semabridge.target_backends.databricks.deploy import reconcile as R
from semabridge.target_backends.databricks.deploy import statements as S
from semabridge.target_backends.databricks.deploy import store
from semabridge.target_backends.databricks.deploy.live import Live
from semabridge.target_backends.databricks.deploy.prepare import DeployRequest, prepare
from semabridge.target_backends.databricks.guard import (
    READ, WRITE, DatabricksGuardError, WritePlan, validate_databricks_sql,
)
from semabridge.target_backends.databricks.probes import evidence
from semabridge.target_backends.databricks.probes import fixtures as F
from semabridge.target_backends.databricks.probes import pipeline

CAT, SCH = "workspace", "sb_deploy"
VERSION = "2026.47"
WH = "wh1"
ME = "deployer@example.com"


# --- statements and the guard ------------------------------------------------------------------------

def _stub():
    return S.stub_table(CAT, SCH, "store", [("store_id", "BIGINT"), ("name", "STRING"), ("opened", "DATE")],
                        {"semabridge.run": "abc123", "semabridge.project": "p1"})


def test_stub_table_statement_form():
    st = _stub()
    assert st.sql.startswith("CREATE TABLE `workspace`.`sb_deploy`.`store` (")
    assert "USING DELTA" in st.sql and "'semabridge.stub' = 'true'" in st.sql
    assert "IF NOT EXISTS" not in st.sql and "LOCATION" not in st.sql
    assert st.target == "workspace.sb_deploy.store"


@pytest.mark.parametrize("cols,props", [
    ([("a", "STRING; DROP TABLE x")], {}),
    ([("a", "ARRAY<STRING>")], {}),
    ([("a", "STRING"), ("A", "BIGINT")], {}),
    ([], {}),
    ([("a", "STRING")], {"other.key": "x"}),
    ([("a", "STRING")], {"semabridge.run": "x' , 'evil' = '1"}),
])
def test_stub_table_rejects_unapproved_types_and_properties(cols, props):
    with pytest.raises(S.StatementError):
        S.stub_table(CAT, SCH, "t", cols, props)


def test_view_statements_and_staging_names():
    c = S.create_view(CAT, SCH, "sales_metrics", "version: 1.1\n", "h", staging_token="abc12345")
    assert c.kind == S.CREATE_STAGING and c.target == "workspace.sb_deploy.sales_metrics__sbstg_abc12345"
    a = S.alter_view(CAT, SCH, "sales_metrics", "version: 1.1\n", "h")
    assert a.sql.startswith("ALTER VIEW `workspace`.`sb_deploy`.`sales_metrics` AS $$")
    with pytest.raises(S.StatementError):
        S.create_view(CAT, SCH, "v", "x: '$$'\n", "h")
    with pytest.raises(S.StatementError):
        S.staging_name("v", "BAD TOKEN")


def _deploy_plan():
    stmts = [_stub(),
             S.create_view(CAT, SCH, "v", "version: 1.1\n", "h", staging_token="abc12345"),
             S.create_view(CAT, SCH, "v", "version: 1.1\n", "h"),
             S.alter_view(CAT, SCH, "w", "version: 1.1\n", "h"),
             S.drop_staging(CAT, SCH, "v", "abc12345")]
    return stmts, WritePlan.for_deploy(stmts)


def test_guard_allows_exactly_the_generated_statements():
    stmts, plan = _deploy_plan()
    kinds = [validate_databricks_sql(s.sql, WRITE, plan).kind for s in stmts]
    assert kinds == ["CREATE_STUB_TABLE", "CREATE_METRIC_VIEW", "CREATE_METRIC_VIEW", "ALTER_VIEW", "DROP_STAGING_VIEW"]


@pytest.mark.parametrize("mutate", [
    lambda s: s.replace("BIGINT", "STRING"),                       # a different column type
    lambda s: s.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"),
    lambda s: s + " LOCATION 's3://x'",
    lambda s: s.replace("'semabridge.stub' = 'true'", "'semabridge.stub' = 'true', 'delta.x' = '1'"),
])
def test_guard_refuses_any_variation_of_an_approved_table(mutate):
    _, plan = _deploy_plan()
    with pytest.raises(DatabricksGuardError, match="not one of the statements approved"):
        validate_databricks_sql(mutate(_stub().sql), WRITE, plan)


@pytest.mark.parametrize("sql", [
    "CREATE TABLE `workspace`.`sb_deploy`.`store` AS SELECT 1 AS a",
    "CREATE TABLE `workspace`.`sb_deploy`.`store` LIKE `workspace`.`x`.`y`",
    "CREATE TABLE `workspace`.`sb_deploy`.`store` SHALLOW CLONE `workspace`.`x`.`y`",
    "CREATE OR REPLACE TABLE `workspace`.`sb_deploy`.`store` (a STRING) USING DELTA TBLPROPERTIES ('semabridge.stub' = 'true')",
    "CREATE TABLE `workspace`.`sb_deploy`.`store` (a STRING DEFAULT 'x') USING DELTA TBLPROPERTIES ('semabridge.stub' = 'true')",
    "CREATE TABLE `workspace`.`sb_deploy`.`store` (a STRING) USING DELTA LOCATION 's3://b' TBLPROPERTIES ('semabridge.stub' = 'true')",
    "CREATE TABLE `workspace`.`sb_deploy`.`store` (a STRING) USING DELTA TBLPROPERTIES ('owner' = 'x')",
])
def test_guard_refuses_forbidden_table_forms_even_if_authorised(sql):
    """Defence in depth: a bad text that somehow got into the plan is still refused by form."""
    bad = S.Statement(S.STUB_TABLE, "workspace.sb_deploy.store", sql)
    plan = WritePlan.for_deploy([bad])
    with pytest.raises(DatabricksGuardError):
        validate_databricks_sql(sql, WRITE, plan)


@pytest.mark.parametrize("sql", [
    "ALTER VIEW `workspace`.`sb_deploy`.`w` RENAME TO `workspace`.`sb_deploy`.`z`",
    "ALTER VIEW `workspace`.`sb_deploy`.`w` OWNER TO `someone`",
    "GRANT SELECT ON VIEW `workspace`.`sb_deploy`.`w` TO `x`",
    "DROP VIEW `workspace`.`sb_deploy`.`w`",
    "DROP TABLE `workspace`.`sb_deploy`.`store`",
    "CREATE OR REPLACE VIEW `workspace`.`sb_deploy`.`v` WITH METRICS LANGUAGE YAML AS $$\nversion: 1.1\n$$",
])
def test_guard_refuses_unapproved_view_operations(sql):
    _, plan = _deploy_plan()
    with pytest.raises(DatabricksGuardError):
        validate_databricks_sql(sql, WRITE, plan)


def test_table_ddl_still_refused_outside_deploy_plans():
    with pytest.raises(DatabricksGuardError):
        validate_databricks_sql(_stub().sql, WRITE, WritePlan.of(views=["workspace.sb_deploy.store"]))
    with pytest.raises(DatabricksGuardError):
        validate_databricks_sql(_stub().sql, READ)


def test_show_create_table_is_a_read():
    assert validate_databricks_sql("SHOW CREATE TABLE `workspace`.`sb_deploy`.`v`", READ).kind == "SHOW"
    for sql in ("SHOW CREATE TABLE `a`.`b`.`c` ; DROP TABLE x", "SHOW CREATE TABLE a.b.c DROP"):
        with pytest.raises(DatabricksGuardError):
            validate_databricks_sql(sql, READ)


# --- definitions ---------------------------------------------------------------------------------------

def test_definition_hash_ignores_formatting_not_content():
    a = "version: 1.1\nsource: c.s.t\nmeasures:\n- name: m\n  expr: SUM(x)\n"
    b = "# comment\nsource:   c.s.t\nversion: 1.1\nmeasures:\n  - {name: m, expr: SUM(x)}\n"
    assert D.definition_hash(a) == D.definition_hash(b)
    assert D.definition_hash(a) != D.definition_hash(a.replace("SUM", "AVG"))
    assert D.extract_yaml(f"CREATE VIEW x WITH METRICS LANGUAGE YAML AS $$\n{a}$$") == f"\n{a}"
    assert D.names(a) == {"fields": [], "measures": ["m"]}
    assert D.extract_yaml("not: [valid") is None


# --- store ------------------------------------------------------------------------------------------------

def _pid() -> str:
    return f"p-{uuid.uuid4().hex[:10]}"


def test_lease_is_exclusive_renewable_and_expires():
    key = store.lease_key("ms-" + uuid.uuid4().hex[:6], CAT, SCH)
    t0 = datetime.now(timezone.utc)
    assert store.acquire_lease(key, "run-a", now=t0)
    assert not store.acquire_lease(key, "run-b", now=t0 + timedelta(seconds=10))
    assert store.renew_lease(key, "run-a", now=t0 + timedelta(minutes=4))
    assert not store.acquire_lease(key, "run-b", now=t0 + timedelta(minutes=8))      # renewed until 9 min
    assert store.acquire_lease(key, "run-b", now=t0 + timedelta(minutes=10))         # expired: taken over
    assert not store.renew_lease(key, "run-a", now=t0 + timedelta(minutes=10))       # a must stop writing
    assert store.holds_lease(key, "run-b", now=t0 + timedelta(minutes=11))
    store.release_lease(key, "run-b")
    assert store.acquire_lease(key, "run-c")


def test_owned_objects_and_names_only_from_successful_runs():
    pid = _pid()
    for run, status in (("r-fail-" + pid, "failed"), ("r-ok-" + pid, "succeeded")):
        store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                        catalog=CAT, schema=SCH, stub_form="empty_table")
        store.record_object(run_id=run, project_id=pid, model_key="", kind="view", fqn=f"`{CAT}`.`{SCH}`.`v_{status}`",
                            outcome="created", definition_hash="h")
        store.record_names(run_id=run, project_id=pid, model_key="", names=[
            {"kind": "view", "view_name": f"v_{status}", "view_source": "Sales", "source_key": {"kind": "view"},
             "target_name": f"v_{status}"}])
        store.finish_run(run, status, {})
    owned = store.owned_objects(pid, "")
    assert list(owned) == [f"{CAT}.{SCH}.v_succeeded"]
    assert [n["target_name"] for n in store.deployed_names(pid, "")] == ["v_succeeded"]


def test_journal_open_operations():
    dest = store.lease_key("ms-" + uuid.uuid4().hex[:6], CAT, SCH)
    store.journal_intend(run_id="rj", seq=1, destination=dest, kind=S.CREATE_VIEW, target_fqn="a.b.c",
                         statement_sha256="x", definition_hash="d")
    store.journal_intend(run_id="rj", seq=2, destination=dest, kind=S.DROP_STAGING, target_fqn="a.b.d",
                         statement_sha256="y")
    store.journal_update("rj", 2, "succeeded", statement_id="s-1")
    assert [o["seq"] for o in store.open_operations(dest)] == [1]


# --- names fed back into planning (A5) -----------------------------------------------------------------------

def test_reserved_names_from_history():
    rows = [
        {"kind": "view", "view_name": "sales_v", "view_source": "Sales", "source_key": {"kind": "view"},
         "target_name": "sales_v"},
        {"kind": "measure", "view_name": "sales_v", "view_source": "Sales",
         "source_key": {"kind": "measure", "table": "Sales", "name": "Total", "aggregation": None, "lineage": None},
         "target_name": "total_sales_kept"},
        {"kind": "stub_table", "view_name": None, "view_source": "Store", "source_key": {"kind": "table"},
         "target_name": "store"},
    ]
    r = N.reserved_from_rows(rows)
    from semabridge.target_backends.databricks.planner.model import SourceId

    assert r["reserved_view_names"] == {"Sales": "sales_v"}
    assert r["reserved_table_names"] == {"Store": "store"}
    assert r["reserved_names"]["Sales"][SourceId("measure", "Sales", "Total")] == "total_sales_kept"


def test_planner_keeps_deployed_view_and_measure_names(tmp_path):
    plan = _build_plan(tmp_path, stub=None)
    v = plan["views"][0]
    m = v["measures"][0]
    rows = [{"kind": "view", "view_source": v["source_dataset"], "source_key": {"kind": "view"},
             "target_name": "kept_view_name"},
            {"kind": "measure", "view_source": v["source_dataset"], "source_key": N.source_key(m["source_id"]),
             "target_name": "kept_measure_name"}]
    again = _build_plan(tmp_path / "again", stub=None, reserved=N.reserved_from_rows(rows))
    v2 = next(x for x in again["views"] if x["source_dataset"] == v["source_dataset"])
    assert v2["name"] == "kept_view_name"
    assert any(x["name"] == "kept_measure_name" and x["source_name"] == m["source_name"] for x in v2["measures"])


# --- a real plan + bundle through the production path ----------------------------------------------------------

FX = next(f for f in F.ALL if f.fid == "p6")          # Sales (fact) + Store (dimension)


def _build_plan(out_dir: Path, *, stub: Optional[str], reserved: Optional[Dict[str, Any]] = None,
                revision: int = 1) -> Dict[str, Any]:
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views
    from semabridge.target_backends.databricks.tables import plan_tables, rewrite_measure_columns
    from semabridge.target_rules import DATABRICKS_RULES

    osi = TMSLToOSIConverter().to_osi({
        "tmsl": pipeline.tmsl_of(FX), "workspace_id": "t", "dataset_id": "t", "display_name": "deploy_test",
        "project_id": "t", "field_aliases": [], "source_file": None, "infer_relationships": None})
    sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi)
    uc = pipeline._uc_tables(FX)
    key = pipeline.PROBE_CATALOG_PLACEHOLDER
    if stub:
        uc[key].pop(stub.upper())
    mapping = {t.name: f"{key[0]}.{key[1]}.{t.name}" for t in FX.tables if t.name != stub}
    tp = plan_tables(sml, uc, source_schemas=[key], on_missing_source="stub", table_mapping=mapping)
    for metric in sml.metrics:
        metric.sql_expression = rewrite_measure_columns(metric.sql_expression, tp.rewrite_map())
    settings = PlannerSettings(security_roles=0, output_catalog=CAT, output_schema=SCH, existing_objects=frozenset(),
                               evidence={"status": "valid"}, **(reserved or {}))
    plan = plan_metric_views(sml, tp, settings).as_dict()
    plan["meta"] = {"revision": revision, "status": "valid", "stub_form": "empty_table"}
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle.attach_definitions(plan, out_dir, catalog=CAT, schema=SCH, model_label="deploy_test")
    assert bundle.set_current(out_dir, revision, reset=True)
    return plan


class FakeLive(Live):
    def __init__(self, plan: Dict[str, Any]):
        key = pipeline.PROBE_CATALOG_PLACEHOLDER
        self.uc = copy.deepcopy(pipeline._uc_tables(FX))
        bound = {t["dataset"] for t in plan.get("tables") or [] if t["status"] == "bound"}
        for name in list(self.uc[key]):
            if self.uc[key][name].name not in bound:
                del self.uc[key][name]
        self.ver, self.me, self.ms = VERSION, ME, "metastore-1"
        self.existing: Dict[str, str] = {}
        self.infos: Dict[str, Dict[str, Any]] = {}
        self.no_select: set = set()
        self.privs: Optional[frozenset] = frozenset({"USE_CATALOG", "USE_SCHEMA", "CREATE_TABLE"})

    def version(self): return self.ver
    def identity(self): return self.me
    def metastore(self): return self.ms
    def objects(self, catalog, schema): return dict(self.existing)
    def table_info(self, fqn):
        from semabridge.target_backends.databricks.guard import normalize_name
        return copy.deepcopy(self.infos.get(normalize_name(fqn)))
    def source_tables(self, schemas): return self.uc
    def can_select(self, fqn): return (fqn not in self.no_select, None if fqn not in self.no_select else "denied")
    def schema_privileges(self, catalog, schema, principal): return self.privs
    def show_create(self, fqn): return None

    def put_view(self, name: str, yaml_text: str, *, owner: str = ME, oid: str = "id-v"):
        self.existing[name.lower()] = "METRIC_VIEW"
        self.infos[f"{CAT}.{SCH}.{name}".lower()] = {
            "id": oid, "owner": owner, "comment": "Created by SemaBridge from model deploy_test",
            "view_definition": yaml_text, "properties": {}, "columns": []}


@pytest.fixture
def proof():
    host = f"dbc-{uuid.uuid4().hex[:8]}.cloud.databricks.com"
    now = datetime.now(timezone.utc)
    from semabridge.target_backends.databricks.writer import WRITER_REVISION

    evidence.record(run_id=uuid.uuid4().hex, workspace_host=host, warehouse_id=WH, probe_schema="w.s",
                    dbsql_version=VERSION, platform={}, suite_revision=F.SUITE_REVISION, writer_revision=WRITER_REVISION,
                    outcomes={"gates": {g: {"outcome": "proven"} for g in evidence.GATES}}, cleanup={},
                    started_at=now - timedelta(minutes=2), completed_at=now, created_by="t")
    return host


def _req(tmp_path, plan, host, **kw) -> DeployRequest:
    return DeployRequest(project_id=kw.pop("project_id", _pid()), model_key="", out_dir=tmp_path, plan=plan,
                         workspace_host=host, warehouse_id=WH, allow_list=kw.pop("allow_list", []), **kw)


def test_new_deploy_creates_views_through_staging(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    pv = prepare(_req(tmp_path, plan, proof), live, token="abc12345")
    assert pv.status == "ready", (pv.model_blockers, [(v.name, v.reasons) for v in pv.views])
    kinds = [s.kind for s in pv.statements]
    assert kinds == [S.CREATE_STAGING, S.CREATE_VIEW, S.DROP_STAGING] * len(plan["views"])
    assert all(v.readiness == "verified_structure" and v.action == "create" for v in pv.views)
    wp = pv.write_plan()
    for st in pv.statements:
        validate_databricks_sql(st.sql, WRITE, wp)
    assert pv.checks and all(c["structural_only"] for c in pv.checks)
    assert any(n["kind"] == "view" for n in pv.names_to_record)
    assert pv.sources and all(s.action == "ok" for s in pv.sources)


def test_missing_table_needs_authorised_stub_and_marks_view_incomplete(tmp_path, proof):
    plan = _build_plan(tmp_path, stub="Store")
    live = FakeLive(plan)
    pv = prepare(_req(tmp_path, plan, proof), live, token="abc12345")
    assert pv.status == "blocked" and all(v.action == "blocked" for v in pv.views if "Store" in v.details.get("stub_backed", []))
    assert "not authorised" in str(pv.as_dict())
    pv = prepare(_req(tmp_path, plan, proof, create_stubs=True), live, token="abc12345")
    assert pv.status == "ready"
    assert pv.statements[0].kind == S.STUB_TABLE and "`store`" in pv.statements[0].sql.lower()
    stub_view = [v for v in pv.views if v.details.get("stub_backed")]
    assert stub_view and all(v.readiness == "incomplete_unverified" for v in stub_view)


def test_stub_form_cannot_change_after_preview(tmp_path, proof):
    plan = _build_plan(tmp_path, stub="Store")
    pv = prepare(_req(tmp_path, plan, proof, create_stubs=True, stub_form="inline"), FakeLive(plan))
    assert pv.status == "blocked" and pv.model_blockers[0]["code"] == "stub_form_changed" and not pv.statements


def test_existing_name_not_ours_is_never_replaced(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    name = plan["views"][0]["name"]
    live.put_view(name, "version: 1.1\n")
    pv = prepare(_req(tmp_path, plan, proof), live)
    v = next(x for x in pv.views if x.name == name)
    assert v.action == "blocked" and v.reasons[0]["code"] == "name_taken"
    assert all(s.view != name for s in pv.statements)


def _deployed(tmp_path, proof, plan, live, pid):
    """Simulate a successful earlier deploy of the current bundle."""
    pv = prepare(_req(tmp_path, plan, proof, project_id=pid), live, token="abc12345")
    run = pv.run_id
    store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=pv.plan_revision, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table")
    folder = tmp_path / "views" / str(pv.plan_revision)
    for v in plan["views"]:
        body = (folder / f"{v['name']}.yaml").read_text(encoding="utf-8")
        live.put_view(v["name"], body)
        store.record_object(run_id=run, project_id=pid, model_key="", kind="view", fqn=f"{CAT}.{SCH}.{v['name']}",
                            outcome="created", uc_object_id="id-v", definition_hash=D.definition_hash(body))
    store.record_names(run_id=run, project_id=pid, model_key="", names=pv.names_to_record)
    store.finish_run(run, "succeeded", {})


def test_unchanged_redeploy_is_a_successful_no_op(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    pv = prepare(_req(tmp_path, plan, proof, project_id=pid), live)
    assert pv.status == "no_op" and not pv.statements and all(v.action == "unchanged" for v in pv.views)


def test_changed_view_is_altered_and_manual_drift_blocks(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    name = plan["views"][0]["name"]
    # a new plan revision changes one measure's comment
    plan2 = copy.deepcopy(plan)
    plan2["views"][0]["measures"][0]["description"] = "changed by re-plan"
    plan2["meta"]["revision"] = 2
    bundle.attach_definitions(plan2, tmp_path, catalog=CAT, schema=SCH, model_label="deploy_test")
    bundle.set_current(tmp_path, 2)
    pv = prepare(_req(tmp_path, plan2, proof, project_id=pid), live, token="abc12345")
    v = next(x for x in pv.views if x.name == name)
    assert v.action == "update" and [s.kind for s in pv.statements] == [S.CREATE_STAGING, S.ALTER_VIEW, S.DROP_STAGING]
    # someone edits the live view by hand -> drift blocks
    info = live.infos[f"{CAT}.{SCH}.{name}".lower()]
    info["view_definition"] = info["view_definition"] + "filter: region = 'EU'\n"
    pv = prepare(_req(tmp_path, plan2, proof, project_id=pid), live)
    v = next(x for x in pv.views if x.name == name)
    assert v.action == "blocked" and v.reasons[0]["code"] == "manual_drift"


def test_identity_and_owner_checks(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    name = plan["views"][0]["name"]
    live.infos[f"{CAT}.{SCH}.{name}".lower()]["id"] = "someone-recreated-it"
    pv = prepare(_req(tmp_path, plan, proof, project_id=pid), live)
    assert next(x for x in pv.views if x.name == name).reasons[0]["code"] == "identity_changed"
    live.infos[f"{CAT}.{SCH}.{name}".lower()].update(id="id-v", owner="other@example.com")
    pv = prepare(_req(tmp_path, plan, proof, project_id=pid), live)
    assert next(x for x in pv.views if x.name == name).reasons[0]["code"] == "not_owner"


def test_source_changes_block_views_and_require_replan(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    key = pipeline.PROBE_CATALOG_PLACEHOLDER
    store_t = live.uc[key]["STORE"]
    col = next(iter(store_t.columns))
    store_t.columns[col] = (store_t.columns[col][0], "boolean")
    pv = prepare(_req(tmp_path, plan, proof), live)
    src = next(s for s in pv.sources if s.name == "Store")
    assert src.action == "blocked" and src.reasons[0]["code"] == "source_changed" and "re-plan" in src.reasons[0]["message"]
    dependent = [v for v in pv.views if v.name in {x for t in plan["tables"] if t["dataset"] == "Store" for x in t["views"]}]
    assert dependent and all(v.action == "blocked" for v in dependent)
    live2 = FakeLive(plan)
    live2.no_select.add(f"{key[0]}.{key[1]}.Sales")
    pv = prepare(_req(tmp_path, plan, proof), live2)
    assert any(r["code"] == "source_no_access" for s in pv.sources for r in s.reasons)


def test_proof_must_match_version_and_capabilities(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    live.ver = "2026.48"
    pv = prepare(_req(tmp_path, plan, proof), live)
    assert [b["code"] for b in pv.model_blockers] == ["proof_version_mismatch"] and not pv.statements
    pv = prepare(_req(tmp_path, plan, f"no-proof-{uuid.uuid4().hex[:6]}.example"), FakeLive(plan))
    assert pv.model_blockers[0]["code"] == "proof_not_valid"


def test_missing_destination_privileges_give_grant_text(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    live.privs = frozenset({"USE_CATALOG", "USE_SCHEMA"})
    pv = prepare(_req(tmp_path, plan, proof), live)
    assert pv.model_blockers[0]["code"] == "destination_permissions_missing"
    assert pv.missing_grants == [f"GRANT CREATE TABLE ON SCHEMA `{CAT}`.`{SCH}` TO `{ME}`;"]
    live.privs = None
    assert any("could not be read" in w for w in prepare(_req(tmp_path, plan, proof), live).warnings)


def test_model_wide_blockers_stop_everything(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    plan2 = copy.deepcopy(plan)
    plan2["meta"]["revision"] = 3
    plan2["blockers"] = [{"code": "security_roles_present", "message": "roles", "scope": "model"}]
    bundle.attach_definitions(plan2, tmp_path, catalog=CAT, schema=SCH, model_label="deploy_test")
    bundle.set_current(tmp_path, 3)
    pv = prepare(_req(tmp_path, plan2, proof), FakeLive(plan2))
    assert pv.status == "blocked" and not pv.statements


def test_breaking_changes_and_unused_objects_are_reported(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    plan2 = copy.deepcopy(plan)
    old = plan2["views"][0]
    removed = old["measures"].pop(0)
    plan2["views"][0]["measures"] = old["measures"] or []
    plan2["meta"]["revision"] = 4
    if not old["measures"] and not old["fields"]:
        pytest.skip("fixture view would be empty")
    bundle.attach_definitions(plan2, tmp_path, catalog=CAT, schema=SCH, model_label="deploy_test")
    bundle.set_current(tmp_path, 4)
    pv = prepare(_req(tmp_path, plan2, proof, project_id=pid), live)
    assert {"kind": "measure_removed", "view": old["name"], "name": removed["name"]} in pv.breaking_changes
    live.put_view("old_orphan", "version: 1.1\n")
    store.start_run(run_id="orph-" + pid, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table")
    store.record_object(run_id="orph-" + pid, project_id=pid, model_key="", kind="view", fqn=f"{CAT}.{SCH}.old_orphan",
                        outcome="created")
    store.finish_run("orph-" + pid, "succeeded", {})
    pv = prepare(_req(tmp_path, plan2, proof, project_id=pid), live)
    assert any(u["fqn"] == f"{CAT}.{SCH}.old_orphan" for u in pv.unused)


def test_unreconciled_operations_block_and_lease_is_checked(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    live.ms = "ms-" + uuid.uuid4().hex[:6]
    key = store.lease_key(live.ms, CAT, SCH)
    pv = prepare(_req(tmp_path, plan, proof, lease_run_id="nobody"), live)
    assert any(b["code"] == "lease_not_held" for b in pv.model_blockers) and not pv.preview_only
    store.journal_intend(run_id="crashed", seq=1, destination=key, kind=S.CREATE_VIEW, target_fqn="x.y.z",
                         statement_sha256="s", definition_hash="d")
    pv = prepare(_req(tmp_path, plan, proof), live)
    assert any(b["code"] == "reconcile_required" for b in pv.model_blockers)


# --- reconciliation ------------------------------------------------------------------------------------------------

def test_reconcile_resolves_from_live_state(tmp_path):
    live = FakeLive({"tables": []})
    live.ms = "ms-" + uuid.uuid4().hex[:6]
    dest = store.lease_key(live.ms, CAT, SCH)
    pid, run = _pid(), "rc-" + uuid.uuid4().hex[:8]
    store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table")
    body = "version: 1.1\nsource: a.b.c\nmeasures:\n- name: m\n  expr: SUM(x)\n"
    new_h, old_h = D.definition_hash(body), D.definition_hash(body.replace("SUM", "AVG"))
    ops = [(S.CREATE_VIEW, "made", new_h, None), (S.CREATE_VIEW, "never_made", new_h, None),
           (S.ALTER_VIEW, "altered", new_h, old_h), (S.ALTER_VIEW, "not_altered", new_h, old_h),
           (S.CREATE_STAGING, "made__sbstg_abc12345", new_h, None), (S.ALTER_VIEW, "weird", new_h, old_h)]
    for i, (kind, name, h, prev) in enumerate(ops, 1):
        store.journal_intend(run_id=run, seq=i, destination=dest, kind=kind, target_fqn=f"{CAT}.{SCH}.{name}",
                             statement_sha256=f"s{i}", definition_hash=h, previous_hash=prev)
    for name, b in (("made", body), ("altered", body), ("not_altered", body.replace("SUM", "AVG")),
                    ("made__sbstg_abc12345", body), ("weird", body.replace("SUM", "MAX"))):
        live.put_view(name, b)
    assert R.reconcile(dest, live, lease_run_id="me")["status"] == "lease_not_held"
    assert store.acquire_lease(dest, "me")
    res = R.reconcile(dest, live, lease_run_id="me")
    got = {o["target_fqn"].split(".")[-1]: o["outcome"] for o in res["operations"]}
    assert got == {"made": "succeeded", "never_made": "not_executed", "altered": "succeeded",
                   "not_altered": "not_executed", "made__sbstg_abc12345": "succeeded", "weird": "uncertain"}
    assert res["status"] == "needs_attention" and res["staging_to_drop"] == [f"{CAT}.{SCH}.made__sbstg_abc12345"]
    assert [o["target_fqn"] for o in store.open_operations(dest)] == [f"{CAT}.{SCH}.weird"]
    assert store.run_info(run)["status"] == "uncertain"
