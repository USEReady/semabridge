"""Phase 3.8a: deploy preparation (offline, fake Databricks).

Covers the exact-statement guard, statement forms, definition normalisation,
the deploy store (per-object state, cumulative names, journal, lease), name
feedback into planning, every pre-check (including the review corrections:
UC-identity ownership, plan-time source identity, populated stubs, history,
restricted staging schema, incomplete views) and the reconciliation of
uncertain operations. Nothing here talks to Databricks.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from semabridge.target_backends.databricks import bundle
from semabridge.target_backends.databricks.deploy import definitions as D
from semabridge.target_backends.databricks.deploy import names as N
from semabridge.target_backends.databricks.deploy import reconcile as R
from semabridge.target_backends.databricks.deploy import statements as S
from semabridge.target_backends.databricks.deploy import store
from semabridge.target_backends.databricks.deploy.live import Live
from semabridge.target_backends.databricks.deploy.prepare import INCOMPLETE_WARNING, DeployRequest, prepare
from semabridge.target_backends.databricks.guard import (
    READ, WRITE, DatabricksGuardError, WritePlan, validate_databricks_sql,
)
from semabridge.target_backends.databricks.probes import evidence
from semabridge.target_backends.databricks.probes import fixtures as F
from semabridge.target_backends.databricks.probes import pipeline

CAT, SCH, STG = "workspace", "sb_deploy", "sb_staging"
VERSION = "2026.47"
WH = "wh1"
ME = "deployer@example.com"
ADMINS = "admins"


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
    c = S.create_staging(CAT, STG, "sales_metrics", "version: 1.1\n", "h", "abc12345")
    assert c.kind == S.CREATE_STAGING and c.target == "workspace.sb_staging.sales_metrics__sbstg_abc12345"
    a = S.alter_view(CAT, SCH, "sales_metrics", "version: 1.1\n", "h")
    assert a.sql.startswith("ALTER VIEW `workspace`.`sb_deploy`.`sales_metrics` AS $$")
    with pytest.raises(S.StatementError):
        S.create_view(CAT, SCH, "v", "x: '$$'\n", "h")
    with pytest.raises(S.StatementError):
        S.staging_name("v", "BAD TOKEN")


def _deploy_plan():
    stmts = [_stub(),
             S.create_staging(CAT, STG, "v", "version: 1.1\n", "h", "abc12345"),
             S.create_view(CAT, SCH, "v", "version: 1.1\n", "h"),
             S.alter_view(CAT, SCH, "w", "version: 1.1\n", "h"),
             S.drop_staging(CAT, STG, "v", "abc12345")]
    return stmts, WritePlan.for_deploy(stmts)


def test_guard_allows_exactly_the_generated_statements():
    stmts, plan = _deploy_plan()
    kinds = [validate_databricks_sql(s.sql, WRITE, plan).kind for s in stmts]
    assert kinds == ["CREATE_STUB_TABLE", "CREATE_METRIC_VIEW", "CREATE_METRIC_VIEW", "ALTER_VIEW", "DROP_STAGING_VIEW"]


@pytest.mark.parametrize("mutate", [
    lambda s: s.replace("BIGINT", "STRING"),
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


def test_definition_comparison_is_strict():
    base = "version: 1.1\nsource: c.s.t\nformat: {decimals: 1}\n"
    assert D.definition_hash(base) != D.definition_hash(base.replace("decimals: 1", "decimals: 1.0"))
    assert D.definition_hash(base) != D.definition_hash(base.replace("decimals: 1", "decimals: '1'"))
    assert D.definition_hash(base) != D.definition_hash(base + "unknown_key: x\n")
    assert D.definition_hash(base) != D.definition_hash(base + "comment: changed\n")
    with pytest.raises(D.DefinitionError, match="duplicate key"):
        D.definition_hash(base + "source: c.s.other\n")
    assert D.extract_yaml(base + "source: c.s.other\n") is None


# --- store ------------------------------------------------------------------------------------------------

def _pid() -> str:
    return f"p-{uuid.uuid4().hex[:10]}"


def test_lease_is_exclusive_renewable_and_expires():
    FIVE = timedelta(minutes=5)
    key = store.lease_key("ms-" + uuid.uuid4().hex[:6], CAT, SCH)
    t0 = datetime.now(timezone.utc)
    assert store.acquire_lease(key, "run-a", ttl=FIVE, now=t0)
    assert not store.acquire_lease(key, "run-b", ttl=FIVE, now=t0 + timedelta(seconds=10))
    assert store.renew_lease(key, "run-a", ttl=FIVE, now=t0 + timedelta(minutes=4))
    assert not store.acquire_lease(key, "run-b", ttl=FIVE, now=t0 + timedelta(minutes=8))
    assert store.acquire_lease(key, "run-b", ttl=FIVE, now=t0 + timedelta(minutes=10))
    assert not store.renew_lease(key, "run-a", ttl=FIVE, now=t0 + timedelta(minutes=10))
    assert store.holds_lease(key, "run-b", now=t0 + timedelta(minutes=11))
    store.release_lease(key, "run-b")
    assert store.acquire_lease(key, "run-c")


def _run(pid: str, status: str, objects=(), names=()) -> str:
    run = f"r-{uuid.uuid4().hex[:10]}"
    store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table")
    for fqn, outcome in objects:
        store.record_object(run_id=run, project_id=pid, model_key="", kind="view", fqn=fqn, outcome=outcome,
                            uc_object_id="id-" + fqn.split(".")[-1], definition_hash="h")
    if names:
        store.record_names(run_id=run, project_id=pid, model_key="", names=list(names))
    store.finish_run(run, status, {})
    return run


def test_latest_state_is_per_object_including_partial_runs():
    pid = _pid()
    _run(pid, "succeeded", [(f"{CAT}.{SCH}.a", "created"), (f"{CAT}.{SCH}.b", "created")])
    _run(pid, "partial", [(f"{CAT}.{SCH}.a", "updated")])                    # b untouched
    _run(pid, "failed", [(f"{CAT}.{SCH}.b", "applied_unverified")])          # b's newest state: unverified
    owned = store.owned_objects(pid, "")
    assert owned[f"{CAT}.{SCH}.a"]["outcome"] == "updated" and owned[f"{CAT}.{SCH}.a"]["verified"]
    assert owned[f"{CAT}.{SCH}.b"]["outcome"] == "applied_unverified" and not owned[f"{CAT}.{SCH}.b"]["verified"]


def test_name_history_is_cumulative():
    pid = _pid()
    m = lambda name, target: {"kind": "measure", "view_name": "v", "view_source": "Sales",  # noqa: E731
                              "source_key": {"kind": "measure", "table": "Sales", "name": name}, "target_name": target}
    _run(pid, "succeeded", names=[m("Total", "total"), m("Old", "old_measure")])
    _run(pid, "partial", names=[m("Total", "total")])          # Old not deployed this time
    got = {(r["source_key"]["name"], r["target_name"]) for r in store.deployed_names(pid, "")}
    assert ("Old", "old_measure") in got and ("Total", "total") in got


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


def test_planner_keeps_deployed_names_and_never_reuses_removed_ones(tmp_path):
    plan = _build_plan(tmp_path, stub=None)
    v = plan["views"][0]
    m0, m1 = v["measures"][0], v["measures"][1]
    rows = [{"kind": "view", "view_source": v["source_dataset"], "source_key": {"kind": "view"},
             "target_name": "kept_view_name"},
            {"kind": "measure", "view_source": v["source_dataset"], "source_key": N.source_key(m0["source_id"]),
             "target_name": "kept_measure_name"},
            # a measure that no longer exists in the model kept the name m1 would naturally get
            {"kind": "measure", "view_source": v["source_dataset"],
             "source_key": {"kind": "measure", "table": "Gone", "name": "Removed", "aggregation": None, "lineage": None},
             "target_name": m1["name"]}]
    again = _build_plan(tmp_path / "again", stub=None, reserved=N.reserved_from_rows(rows))
    v2 = next(x for x in again["views"] if x["source_dataset"] == v["source_dataset"])
    assert v2["name"] == "kept_view_name"
    by_source = {x["source_name"]: x["name"] for x in v2["measures"]}
    assert by_source[m0["source_name"]] == "kept_measure_name"
    assert by_source[m1["source_name"]] != m1["name"]          # the retired name is not reused


# --- a real plan + bundle through the production path ----------------------------------------------------------

FX = next(f for f in F.ALL if f.fid == "p6")          # Sales (fact) + Store (dimension)
KEY = pipeline.PROBE_CATALOG_PLACEHOLDER


def _src_fqn(name: str) -> str:
    return f"{KEY[0]}.{KEY[1]}.{name}"


def _build_plan(out_dir: Path, *, stub: Optional[str], reserved: Optional[Dict[str, Any]] = None,
                revision: int = 1, table_ids: bool = True) -> Dict[str, Any]:
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
    if stub:
        uc[KEY].pop(stub.upper())
    mapping = {t.name: _src_fqn(t.name) for t in FX.tables if t.name != stub}
    tp = plan_tables(sml, uc, source_schemas=[KEY], on_missing_source="stub", table_mapping=mapping)
    if table_ids:
        for b in tp.bindings:
            if b.table is not None:
                b.table.table_id = f"tid-{b.table.name}"
    for metric in sml.metrics:
        metric.sql_expression = rewrite_measure_columns(metric.sql_expression, tp.rewrite_map())
    settings = PlannerSettings(security_roles=0, output_catalog=CAT, output_schema=SCH, existing_objects=frozenset(),
                               evidence={"status": "valid"}, **(reserved or {}))
    plan = plan_metric_views(sml, tp, settings).as_dict()
    plan["meta"] = {"revision": revision, "status": "valid", "stub_form": "empty_table", "names_history": "read"}
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle.attach_definitions(plan, out_dir, catalog=CAT, schema=SCH, model_label="deploy_test")
    assert bundle.set_current(out_dir, revision, reset=True)
    return plan


def _republish(tmp_path: Path, plan: Dict[str, Any], revision: int) -> Dict[str, Any]:
    plan = copy.deepcopy(plan)
    plan["meta"]["revision"] = revision
    plan.pop("definitions", None)
    plan["meta"].pop("definitions", None)
    bundle.attach_definitions(plan, tmp_path, catalog=CAT, schema=SCH, model_label="deploy_test")
    assert bundle.set_current(tmp_path, revision)
    return plan


class FakeLive(Live):
    def __init__(self, plan: Dict[str, Any]):
        self.uc = copy.deepcopy(pipeline._uc_tables(FX))
        bound = {t["dataset"] for t in plan.get("tables") or [] if t["status"] == "bound"}
        for name in list(self.uc[KEY]):
            if self.uc[KEY][name].name not in bound:
                del self.uc[KEY][name]
        self.ver, self.me, self.ms = VERSION, ME, "ms-" + uuid.uuid4().hex[:6]
        self.existing: Dict[str, str] = {}
        self.staged: Dict[str, str] = {}
        self.infos: Dict[str, Dict[str, Any]] = {}
        for t in self.uc[KEY].values():
            self.infos[_src_fqn(t.name).lower()] = {"id": f"tid-{t.name}", "owner": "etl", "properties": {},
                                                   "columns": [], "comment": None, "view_definition": None}
        self.no_select: set = set()
        self.rows: set = set()
        self.privs: Optional[frozenset] = frozenset({"USE_CATALOG", "USE_SCHEMA", "CREATE_TABLE"})
        self.grant_map: Dict[str, Optional[List[Dict[str, Any]]]] = {
            f"schema:{CAT}.{STG}": [{"principal": ME, "privileges": ["ALL_PRIVILEGES"]}],
            f"catalog:{CAT}": [{"principal": "account users", "privileges": ["USE_CATALOG"]}],
            f"schema:{CAT}.{SCH}": [{"principal": "analysts", "privileges": ["USE_SCHEMA", "SELECT"]}]}
        self.owners: Dict[str, Optional[str]] = {f"schema:{CAT}.{STG}": ME, f"catalog:{CAT}": ADMINS,
                                                 f"schema:{CAT}.{SCH}": ME}
        self.statuses: Dict[str, str] = {}
        self.history: Dict[str, str] = {}

    def version(self): return self.ver
    def identity(self): return self.me
    def metastore(self): return self.ms
    def objects(self, catalog, schema): return dict(self.staged if schema == STG else self.existing)
    def table_info(self, fqn):
        from semabridge.target_backends.databricks.guard import normalize_name
        return copy.deepcopy(self.infos.get(normalize_name(fqn)))
    def source_tables(self, schemas): return self.uc
    def can_select(self, fqn): return (fqn not in self.no_select, None if fqn not in self.no_select else "denied")
    def schema_privileges(self, catalog, schema, principal): return self.privs
    def show_create(self, fqn): return None
    def has_rows(self, fqn): return fqn.lower() in self.rows
    def grants(self, securable, full_name): return copy.deepcopy(self.grant_map.get(f"{securable}:{full_name}", []))
    def owner(self, securable, full_name): return self.owners.get(f"{securable}:{full_name}")
    def statement_status(self, statement_id): return self.statuses.get(statement_id)
    def history_status(self, sha, *, since, warehouse_id, user): return self.history.get(sha)

    def put_view(self, name: str, yaml_text: str, *, owner: str = ME, oid: Optional[str] = "id-v",
                 schema: str = SCH, comment: str = "Created by SemaBridge from model deploy_test"):
        (self.staged if schema == STG else self.existing)[name.lower()] = "METRIC_VIEW"
        self.infos[f"{CAT}.{schema}.{name}".lower()] = {
            "id": oid, "owner": owner, "comment": comment, "view_definition": yaml_text, "properties": {},
            "columns": []}


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
    kw.setdefault("staging", (CAT, STG))
    kw.setdefault("deploy_principals", [ADMINS])
    return DeployRequest(project_id=kw.pop("project_id", _pid()), model_key="", out_dir=tmp_path, plan=plan,
                         workspace_host=host, warehouse_id=WH, allow_list=kw.pop("allow_list", []), **kw)


def _codes(pv) -> List[str]:
    return [b["code"] for b in pv.model_blockers]


def _view(pv, name):
    return next(v for v in pv.views if v.name == name)


def test_new_deploy_creates_views_through_staging(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    pv = prepare(_req(tmp_path, plan, proof), FakeLive(plan), token="abc12345")
    assert pv.status == "ready", (pv.model_blockers, [(v.name, v.reasons) for v in pv.views])
    assert [s.kind for s in pv.statements] == [S.CREATE_STAGING, S.CREATE_VIEW, S.DROP_STAGING] * len(plan["views"])
    assert all(s.target.startswith(f"{CAT}.{STG}.") for s in pv.statements if s.kind != S.CREATE_VIEW)
    assert all(v.readiness == "verified_structure" and v.action == "create" for v in pv.views)
    wp = pv.write_plan()
    for st in pv.statements:
        validate_databricks_sql(st.sql, WRITE, wp)
    assert pv.checks and all(c["structural_only"] for c in pv.checks)
    assert any(n["kind"] == "view" for n in pv.names_to_record)
    assert pv.sources and all(s.action == "ok" for s in pv.sources)


def test_stubs_and_incomplete_views_are_two_separate_opt_ins(tmp_path, proof):
    plan = _build_plan(tmp_path, stub="Store")
    folder = tmp_path / "views" / "1"
    incomplete = [v["name"] for v in plan["views"] if any(t == "Store" for t in
                  next(m for m in bundle.deployable_bundle(tmp_path, 1)["views"] if m["view"] == v["name"])["tables"])]
    assert incomplete
    for name in incomplete:                                       # the warning is in the approved bundle
        assert INCOMPLETE_WARNING in (folder / f"{name}.yaml").read_text(encoding="utf-8")
    live = FakeLive(plan)
    pv = prepare(_req(tmp_path, plan, proof), live, token="abc12345")
    assert all(_view(pv, n).action == "blocked" for n in incomplete)
    codes = {r["code"] for n in incomplete for r in _view(pv, n).reasons}
    assert {"incomplete_not_selected", "source_table_missing"} <= codes
    pv = prepare(_req(tmp_path, plan, proof, create_stubs=True), live, token="abc12345")
    assert all(_view(pv, n).action == "blocked" for n in incomplete)           # stub alone is not enough
    pv = prepare(_req(tmp_path, plan, proof, create_stubs=True, publish_incomplete=True), live, token="abc12345")
    assert pv.status == "ready", (pv.model_blockers, [(v.name, v.reasons) for v in pv.views])
    assert pv.statements[0].kind == S.STUB_TABLE
    assert all(_view(pv, n).readiness == "incomplete_unverified" for n in incomplete)
    assert {a["principal"] for a in pv.destination_access} >= {"analysts", ME, ADMINS}


def test_incomplete_publication_blocks_when_destination_access_is_unknown(tmp_path, proof):
    plan = _build_plan(tmp_path, stub="Store")
    live = FakeLive(plan)
    live.grant_map[f"schema:{CAT}.{SCH}"] = None
    pv = prepare(_req(tmp_path, plan, proof, create_stubs=True, publish_incomplete=True), live)
    assert "destination_access_unreadable" in _codes(pv) and not pv.statements


def test_stub_form_cannot_change_after_preview(tmp_path, proof):
    plan = _build_plan(tmp_path, stub="Store")
    pv = prepare(_req(tmp_path, plan, proof, create_stubs=True, stub_form="inline"), FakeLive(plan))
    assert "stub_form_changed" in _codes(pv) and not pv.statements


@pytest.mark.parametrize("setup,code", [
    (lambda live: live.grant_map.__setitem__(f"schema:{CAT}.{STG}", [{"principal": "analysts", "privileges": ["SELECT"]}]),
     "staging_not_restricted"),
    (lambda live: live.grant_map.__setitem__(f"catalog:{CAT}", [{"principal": "all users", "privileges": ["USE_SCHEMA", "SELECT"]}]),
     "staging_not_restricted"),                                    # inherited from the catalog
    (lambda live: live.owners.__setitem__(f"schema:{CAT}.{STG}", "someone_else"), "staging_not_restricted"),
    (lambda live: live.grant_map.__setitem__(f"schema:{CAT}.{STG}", None), "staging_isolation_unknown"),
])
def test_staging_schema_must_be_restricted(tmp_path, proof, setup, code):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    setup(live)
    pv = prepare(_req(tmp_path, plan, proof), live)
    assert code in _codes(pv) and not pv.statements


def test_staging_schema_configuration(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    assert "staging_schema_missing" in _codes(prepare(_req(tmp_path, plan, proof, staging=None), FakeLive(plan)))
    assert "staging_schema_is_destination" in _codes(prepare(_req(tmp_path, plan, proof, staging=(CAT, SCH)),
                                                             FakeLive(plan)))
    live = FakeLive(plan)
    live.grant_map[f"schema:{CAT}.{STG}"].append({"principal": ADMINS, "privileges": ["ALL_PRIVILEGES"]})
    assert prepare(_req(tmp_path, plan, proof), live).status == "ready"      # admin group is authorised


def test_existing_name_not_ours_is_never_replaced(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    name = plan["views"][0]["name"]
    live.put_view(name, "version: 1.1\n")
    pv = prepare(_req(tmp_path, plan, proof), live)
    assert _view(pv, name).reasons[0]["code"] == "name_taken" and all(s.view != name for s in pv.statements)


def _deployed(tmp_path, proof, plan, live, pid, *, oid: Optional[str] = "id-v", outcome: str = "created"):
    """Simulate a verified earlier deploy of the current bundle."""
    pv = prepare(_req(tmp_path, plan, proof, project_id=pid), live, token="abc12345")
    run = pv.run_id
    store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=pv.plan_revision, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table")
    folder = tmp_path / "views" / str(pv.plan_revision)
    for v in plan["views"]:
        body = (folder / f"{v['name']}.yaml").read_text(encoding="utf-8")
        live.put_view(v["name"], body, oid=oid)
        store.record_object(run_id=run, project_id=pid, model_key="", kind="view", fqn=f"{CAT}.{SCH}.{v['name']}",
                            outcome=outcome, uc_object_id=oid, definition_hash=D.definition_hash(body),
                            details={"comment": "Created by SemaBridge from model deploy_test"})
    store.record_names(run_id=run, project_id=pid, model_key="", names=pv.names_to_record)
    store.finish_run(run, "succeeded", {})


def test_unchanged_redeploy_is_a_successful_no_op_and_reverifies(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    pv = prepare(_req(tmp_path, plan, proof, project_id=pid), live)
    assert pv.status == "no_op" and not pv.statements and all(v.action == "unchanged" for v in pv.views)
    assert {o["fqn"] for o in pv.verified_objects} == {f"{CAT}.{SCH}.{v['name']}" for v in plan["views"]}


def test_changed_view_is_altered_and_manual_drift_blocks(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    name = plan["views"][0]["name"]
    plan2 = copy.deepcopy(plan)
    plan2["views"][0]["measures"][0]["description"] = "changed by re-plan"
    plan2 = _republish(tmp_path, plan2, 2)
    pv = prepare(_req(tmp_path, plan2, proof, project_id=pid), live, token="abc12345")
    assert _view(pv, name).action == "update"
    assert [s.kind for s in pv.statements] == [S.CREATE_STAGING, S.ALTER_VIEW, S.DROP_STAGING]
    info = live.infos[f"{CAT}.{SCH}.{name}".lower()]
    info["view_definition"] = info["view_definition"] + "filter: region = 'EU'\n"
    assert _view(prepare(_req(tmp_path, plan2, proof, project_id=pid), live), name).reasons[0]["code"] == "manual_drift"


def test_object_comment_change_is_drift(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    name = plan["views"][0]["name"]
    live.infos[f"{CAT}.{SCH}.{name}".lower()]["comment"] = "edited in the UI"
    assert _view(prepare(_req(tmp_path, plan, proof, project_id=pid), live), name).reasons[0]["code"] == "manual_drift"


@pytest.mark.parametrize("change,code", [
    (lambda live, fqn: live.infos[fqn].update(id="someone-recreated-it"), "identity_changed"),
    (lambda live, fqn: live.infos[fqn].update(id=None), "ownership_unestablished"),
    (lambda live, fqn: live.infos[fqn].update(owner="other@example.com"), "not_owner"),
])
def test_ownership_needs_matching_uc_identity_and_owner(tmp_path, proof, change, code):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    name = plan["views"][0]["name"]
    change(live, f"{CAT}.{SCH}.{name}".lower())
    assert _view(prepare(_req(tmp_path, plan, proof, project_id=pid), live), name).reasons[0]["code"] == code


def test_no_recorded_identity_or_unverified_state_never_mutates(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid, oid=None)          # provenance comment only, no UC id
    name = plan["views"][0]["name"]
    assert _view(prepare(_req(tmp_path, plan, proof, project_id=pid), live), name).reasons[0]["code"] == \
        "ownership_unestablished"
    live2, pid2 = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live2, pid2, outcome="applied_unverified")
    assert _view(prepare(_req(tmp_path, plan, proof, project_id=pid2), live2), name).reasons[0]["code"] == \
        "last_deploy_unverified"


def _owned_stub(live: FakeLive, pid: str, *, oid: Optional[str] = "id-store", rows: bool = False):
    fqn = f"{CAT}.{SCH}.store"
    live.existing["store"] = "MANAGED"
    live.infos[fqn] = {"id": "id-store", "owner": ME, "comment": None, "view_definition": None,
                       "properties": {"semabridge.stub": "true"},
                       "columns": [{"name": c["name"], "type": c["type"]} for c in live._stub_cols]}
    if rows:
        live.rows.add(fqn)
    _run(pid, "succeeded")
    run = f"r-{uuid.uuid4().hex[:10]}"
    store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table")
    store.record_object(run_id=run, project_id=pid, model_key="", kind="stub_table", fqn=fqn, outcome="created",
                        uc_object_id=oid)
    store.finish_run(run, "succeeded", {})


@pytest.mark.parametrize("oid,rows,expect", [
    ("id-store", False, "exists_owned"),
    ("id-store", True, "stub_has_data"),
    (None, False, "ownership_unestablished"),
])
def test_existing_stub_reuse_rules(tmp_path, proof, oid, rows, expect):
    plan = _build_plan(tmp_path, stub="Store")
    live, pid = FakeLive(plan), _pid()
    live._stub_cols = next(t for t in plan["tables"] if t["dataset"] == "Store")["columns"]
    _owned_stub(live, pid, oid=oid, rows=rows)
    pv = prepare(_req(tmp_path, plan, proof, project_id=pid, create_stubs=True, publish_incomplete=True), live)
    item = next(t for t in pv.stub_tables if t.name == "Store")
    assert (item.action if expect == "exists_owned" else item.reasons[0]["code"]) == expect
    if expect == "exists_owned":
        assert not any(s.kind == S.STUB_TABLE for s in pv.statements)
        assert any(o["kind"] == "stub_table" for o in pv.verified_objects)


@pytest.mark.parametrize("mutate,code", [
    (lambda live: live.infos[_src_fqn("Store").lower()].update(id="tid-recreated"), "source_replaced"),
    (lambda live: live.infos[_src_fqn("Store").lower()].update(id=None), "source_identity_unreadable"),
    (lambda live: live.uc[KEY]["STORE"].columns.update(
        {k: (v[0], "boolean") for k, v in list(live.uc[KEY]["STORE"].columns.items())[:1]}), "source_changed"),
    (lambda live: live.no_select.add(_src_fqn("Sales")), "source_no_access"),
])
def test_source_changes_block_views_and_require_replan(tmp_path, proof, mutate, code):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    mutate(live)
    pv = prepare(_req(tmp_path, plan, proof), live)
    assert any(r["code"] == code for s in pv.sources for r in s.reasons)
    assert any(v.action == "blocked" for v in pv.views)


def test_plan_without_source_identity_blocks(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None, table_ids=False)
    pv = prepare(_req(tmp_path, plan, proof), FakeLive(plan))
    assert all(r["code"] == "source_identity_unrecorded" for s in pv.sources for r in s.reasons)
    assert all(v.action == "blocked" for v in pv.views)


def test_history_must_be_readable(tmp_path, proof, monkeypatch):
    plan = _build_plan(tmp_path, stub=None)
    plan2 = copy.deepcopy(plan)
    plan2["meta"]["names_history"] = "unreadable"
    assert "plan_without_name_history" in _codes(prepare(_req(tmp_path, plan2, proof), FakeLive(plan)))

    def broken(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "deployed_names", broken)
    assert "deploy_history_unreadable" in _codes(prepare(_req(tmp_path, plan, proof), FakeLive(plan)))
    reserved, status = N.reserved_for("p", "")
    assert status == "unreadable" and reserved["reserved_names"] == {}


def test_proof_must_match_version_and_capabilities(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    live.ver = "2026.48"
    pv = prepare(_req(tmp_path, plan, proof), live)
    assert _codes(pv) == ["proof_version_mismatch"] and not pv.statements
    pv = prepare(_req(tmp_path, plan, f"no-proof-{uuid.uuid4().hex[:6]}.example"), FakeLive(plan))
    assert "proof_not_valid" in _codes(pv)


def test_missing_destination_privileges_give_grant_text(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    live.privs = frozenset({"USE_CATALOG", "USE_SCHEMA"})
    pv = prepare(_req(tmp_path, plan, proof), live)
    assert "destination_permissions_missing" in _codes(pv)
    assert pv.missing_grants == [f"GRANT CREATE TABLE ON SCHEMA `{CAT}`.`{SCH}` TO `{ME}`;"]
    live.privs = None
    assert any("could not be read" in w for w in prepare(_req(tmp_path, plan, proof), live).warnings)


def test_model_wide_blockers_stop_everything(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    plan2 = copy.deepcopy(plan)
    plan2["blockers"] = [{"code": "security_roles_present", "message": "roles", "scope": "model"}]
    plan2 = _republish(tmp_path, plan2, 3)
    pv = prepare(_req(tmp_path, plan2, proof), FakeLive(plan2))
    assert pv.status == "blocked" and not pv.statements


def test_breaking_changes_and_unused_objects_are_reported(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live, pid = FakeLive(plan), _pid()
    _deployed(tmp_path, proof, plan, live, pid)
    plan2 = copy.deepcopy(plan)
    removed = plan2["views"][0]["measures"].pop(0)
    plan2 = _republish(tmp_path, plan2, 4)
    pv = prepare(_req(tmp_path, plan2, proof, project_id=pid), live)
    assert {"kind": "measure_removed", "view": plan["views"][0]["name"], "name": removed["name"]} in pv.breaking_changes
    live.put_view("old_orphan", "version: 1.1\n")
    _run(pid, "succeeded", [(f"{CAT}.{SCH}.old_orphan", "created")])
    pv = prepare(_req(tmp_path, plan2, proof, project_id=pid), live)
    assert any(u["fqn"] == f"{CAT}.{SCH}.old_orphan" for u in pv.unused)


def test_unreconciled_operations_and_leases_on_both_schemas(tmp_path, proof):
    plan = _build_plan(tmp_path, stub=None)
    live = FakeLive(plan)
    pv = prepare(_req(tmp_path, plan, proof, lease_run_id="nobody"), live)
    assert _codes(pv).count("lease_not_held") == 2 and not pv.preview_only
    stg_key = store.lease_key(live.ms, CAT, STG)
    store.journal_intend(run_id="crashed", seq=1, destination=stg_key, kind=S.CREATE_STAGING, target_fqn="x.y.z",
                         statement_sha256="s", definition_hash="d")
    assert "reconcile_required" in _codes(prepare(_req(tmp_path, plan, proof), live))


# --- reconciliation ------------------------------------------------------------------------------------------------

def test_reconcile_needs_statement_evidence(tmp_path):
    live = FakeLive({"tables": []})
    dest = store.lease_key(live.ms, CAT, SCH)
    pid, run = _pid(), "rc-" + uuid.uuid4().hex[:8]
    store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table", identity=ME, warehouse_id=WH)
    body = "version: 1.1\nsource: a.b.c\nmeasures:\n- name: m\n  expr: SUM(x)\n"
    new_h, old_h = D.definition_hash(body), D.definition_hash(body.replace("SUM", "AVG"))
    cases = [
        # name, kind, statement id, status, live body, owner
        ("ok_by_status", S.CREATE_VIEW, "st1", "SUCCEEDED", body, ME),
        ("ok_by_history", S.CREATE_VIEW, None, None, body, ME),
        ("still_running", S.CREATE_VIEW, "st3", "RUNNING", None, ME),
        ("no_evidence", S.CREATE_VIEW, None, None, body, ME),             # matching definition alone: not enough
        ("wrong_owner", S.CREATE_VIEW, "st5", "SUCCEEDED", body, "intruder"),
        ("failed_absent", S.CREATE_VIEW, "st6", "FAILED", None, ME),
        ("alter_failed_old", S.ALTER_VIEW, "st7", "FAILED", body.replace("SUM", "AVG"), ME),
        ("stg__sbstg_abc12345", S.CREATE_STAGING, "st8", "SUCCEEDED", body, ME),
        ("gone__sbstg_abc12345", S.DROP_STAGING, None, None, None, ME),
    ]
    for i, (name, kind, sid, status, live_body, owner) in enumerate(cases, 1):
        sha = f"sha{i}"
        store.journal_intend(run_id=run, seq=i, destination=dest, kind=kind, target_fqn=f"{CAT}.{SCH}.{name}",
                             statement_sha256=sha, definition_hash=new_h, previous_hash=old_h)
        if sid:
            store.journal_update(run, i, "submitted", statement_id=sid)
            live.statuses[sid] = status
        if live_body is not None:
            live.put_view(name, live_body, owner=owner, oid=f"id-{name}")
    live.history["sha2"] = "FINISHED"
    assert R.reconcile(dest, live, lease_run_id="me")["status"] == "lease_not_held"
    assert store.acquire_lease(dest, "me")
    res = R.reconcile(dest, live, lease_run_id="me")
    got = {o["target_fqn"].split(".")[-1]: o["outcome"] for o in res["operations"]}
    assert got == {"ok_by_status": "succeeded", "ok_by_history": "succeeded", "still_running": "open",
                   "no_evidence": "uncertain", "wrong_owner": "uncertain", "failed_absent": "not_executed",
                   "alter_failed_old": "not_executed", "stg__sbstg_abc12345": "succeeded",
                   "gone__sbstg_abc12345": "succeeded"}
    assert res["staging_to_drop"] == [f"{CAT}.{SCH}.stg__sbstg_abc12345"]
    assert {o["target_fqn"].split(".")[-1] for o in store.open_operations(dest)} == {
        "still_running", "no_evidence", "wrong_owner"}
    owned = store.owned_objects(pid, "")
    assert owned[f"{CAT}.{SCH}.ok_by_status"]["uc_object_id"] == "id-ok_by_status"
    assert f"{CAT}.{SCH}.no_evidence" not in owned
    assert store.run_info(run)["status"] == "uncertain"


def test_databricks_dimensions_synonym_compares_equal():
    """Live: Databricks returns `fields` as `dimensions`, re-wrapped and re-ordered."""
    ours = "version: 1.1\ncomment: a long comment text\nsource: c.s.t\nfields:\n- name: f\n  expr: x\n"
    theirs = "version: 1.1\n\nsource: c.s.t\n\ncomment: a long\n  comment text\n\ndimensions:\n  - name: f\n    expr: x\n"
    assert D.definition_hash(ours) == D.definition_hash(theirs)
