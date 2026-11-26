"""Phase 3.8b: executing deployment intents (offline, fake Databricks).

The fake writer sends every statement through the REAL guard with the run's
WritePlan, then applies its effect to a fake Databricks. Failures are injected
at each step. Nothing here talks to Databricks.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import pytest

from semabridge.target_backends.databricks import bundle
from semabridge.target_backends.databricks.client import DatabricksStatementError, DatabricksTimeoutError
from semabridge.target_backends.databricks.deploy import definitions as D
from semabridge.target_backends.databricks.deploy import statements as S
from semabridge.target_backends.databricks.deploy import store
from semabridge.target_backends.databricks.deploy.execute import (
    IntentError, confirm_intent, reconcile_and_clean, reverify,
)
from semabridge.target_backends.databricks.deploy.intent import create_intent
from semabridge.target_backends.databricks.guard import WRITE, normalize_name, validate_databricks_sql

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_databricks_deploy import (  # noqa: E402  (same directory)
    ADMINS, CAT, ME, SCH, STG, FakeLive, _build_plan, _pid, _republish, _req, proof,  # noqa: F401
)


class ExecLive(FakeLive):
    """FakeLive plus what the executor reads: staging validation and grants."""

    def __init__(self, plan):
        super().__init__(plan)
        self.read_failures: Dict[str, Exception] = {}
        self.grants_by: Dict[str, List] = {}
        self.on_read: Optional[Callable[[str], None]] = None
        self.reads: List[str] = []

    def run_read(self, sql):
        self.reads.append(sql)
        if self.on_read:
            self.on_read(sql)
        for frag, exc in self.read_failures.items():
            if frag in sql:
                raise exc
        return None

    def show_grants(self, fqn):
        return copy.deepcopy(self.grants_by.get(normalize_name(fqn), [("analysts", "SELECT")]))


class FakeWriter:
    """Executes through the real guard, then applies the effect to the fake live."""

    def __init__(self, live: ExecLive, plan, log: List[str], failures: Dict[str, Any]):
        self.live, self.plan, self.log, self.failures = live, plan, log, failures
        self.n = 0

    def execute(self, sql: str):
        validate_databricks_sql(sql, WRITE, self.plan)              # the real exact-statement guard
        self.n += 1
        kind = sql.split()[0] + " " + sql.split()[1]
        target = normalize_name(re.match(r"\s*\w+ (?:VIEW|TABLE) (`[^`]+`\.`[^`]+`\.`[^`]+`)", sql).group(1))
        self.log.append(f"{kind} {target}")
        fail = self.failures.get(target) or self.failures.get(f"{kind} {target}")
        if fail == "apply_then_timeout":
            self._apply(sql, target)
            raise DatabricksTimeoutError("Statement st-x did not finish; it was cancelled.", statement_id=f"st-{self.n}")
        if isinstance(fail, Exception):
            raise fail
        self._apply(sql, target)
        return type("R", (), {"statement_id": f"st-{self.n}"})()

    def _apply(self, sql: str, target: str):
        cat, sch, name = target.split(".")
        if sql.startswith("CREATE TABLE"):
            cols = [{"name": m.group(1), "type": m.group(2).lower()}
                    for m in re.finditer(r"^\s+`([^`]+)` ([A-Z_]+(?:\([^)]*\))?)", sql, re.M)]
            props = dict(re.findall(r"'([^']+)' = '([^']*)'", sql.split("TBLPROPERTIES", 1)[1]))
            self.live.existing[name] = "MANAGED"
            self.live.infos[target] = {"id": f"id-{uuid.uuid4().hex[:6]}", "owner": self.live.me, "comment": None,
                                       "view_definition": None, "properties": props, "columns": cols}
        elif sql.startswith("CREATE VIEW"):
            body = sql.split("$$", 2)[1]
            self.live.put_view(name, body, owner=self.live.me, oid=f"id-{uuid.uuid4().hex[:6]}", schema=sch,
                               comment=D.canonical(body).get("comment"))
        elif sql.startswith("ALTER VIEW"):
            self.live.infos[target]["view_definition"] = sql.split("$$", 2)[1]
        elif sql.startswith("DROP VIEW"):
            (self.live.staged if sch == STG else self.live.existing).pop(name, None)
            self.live.infos.pop(target, None)


class Harness:
    def __init__(self, tmp_path, host, *, stub=None, **req_kw):
        self.tmp, self.host = tmp_path, host
        self.plan = _build_plan(tmp_path, stub=stub)
        self.live = ExecLive(self.plan)
        self.pid = _pid()
        self.req_kw = req_kw
        self.log: List[str] = []
        self.failures: Dict[str, Any] = {}
        self.plans: List[Any] = []

    def writer(self, wp):
        self.plans.append(wp)
        return FakeWriter(self.live, wp, self.log, self.failures)

    def preview(self, **kw):
        return create_intent(_req(self.tmp, self.plan, self.host, project_id=self.pid, **{**self.req_kw, **kw}),
                             self.live, created_by="u1")

    def confirm(self, intent_id, *, ack=False, revision=None):
        rev = revision if revision is not None else bundle.read_current(self.tmp).get("plan_revision")
        return confirm_intent(intent_id, project_id=self.pid, model_key="", user_id="u1",
                              acknowledge_destination_access=ack, plan=self.plan, current_revision=rev,
                              live=self.live, write_client=self.writer)

    def deploy(self, **kw):
        res = self.preview(**kw)
        assert res["intent"], res["preview"]["model_blockers"]
        return self.confirm(res["intent"]["intent_id"],
                            ack=bool({**self.req_kw, **kw}.get("publish_incomplete", False)))


def _fqn(name: str, schema: str = SCH) -> str:
    return f"{CAT}.{schema}.{name}".lower()


def test_new_deploy_end_to_end(tmp_path, proof):
    h = Harness(tmp_path, proof)
    out = h.deploy()
    assert out["status"] == "succeeded", out
    names = [v["name"] for v in h.plan["views"]]
    for n in names:
        assert n.lower() in h.live.existing                      # the real views exist
        assert not any(k.startswith(n.lower() + "__sbstg_") for k in h.live.staged)   # staging cleaned
    owned = store.owned_objects(h.pid, "")
    assert all(owned[_fqn(n)]["verified"] and owned[_fqn(n)]["uc_object_id"] for n in names)
    assert {r["target_name"] for r in store.deployed_names(h.pid, "") if r["kind"] == "view"} == set(names)
    assert all(j["state"] == "succeeded" for j in out["journal"])
    assert [l.split()[0] + " " + l.split()[1] for l in h.log[:3]] == ["CREATE VIEW", "CREATE VIEW", "DROP VIEW"]
    assert h.live.reads and any("LIMIT 0" in r for r in h.live.reads)       # structural validation ran
    # leases released
    assert store.acquire_lease(h.live and store.lease_key(h.live.ms, CAT, SCH), "after")
    # the write client only ever had this intent's statements
    assert h.plans[0].authorized == {s["sha256"] for s in store.get_intent(
        next(i for i in [_last_intent(h.pid)]))["intent"]["preview"]["statements"]}


def _last_intent(pid):
    from sqlalchemy import select

    from semabridge.repository.orm.project_models import DatabricksDeployIntent as I
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as s:
        return s.execute(select(I.intent_id).where(I.project_id == pid).order_by(I.id.desc())).scalars().first()


def test_confirmation_is_idempotent_and_intents_expire(tmp_path, proof):
    h = Harness(tmp_path, proof)
    res = h.preview()
    iid = res["intent"]["intent_id"]
    first = h.confirm(iid)
    writes = len(h.log)
    again = h.confirm(iid)
    assert again["status"] == "existing" and again["run_id"] == first["run_id"] and len(h.log) == writes
    # an expired, unused intent
    res2 = h.preview()
    from sqlalchemy import update

    from semabridge.repository.orm.project_models import DatabricksDeployIntent as I
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as s:
        s.execute(update(I).where(I.intent_id == res2["intent"]["intent_id"])
                  .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    with pytest.raises(IntentError) as e:
        h.confirm(res2["intent"]["intent_id"])
    assert e.value.code == "intent_expired"


def test_intent_invalidated_by_a_newer_plan(tmp_path, proof):
    h = Harness(tmp_path, proof)
    res = h.preview()
    with pytest.raises(IntentError) as e:
        h.confirm(res["intent"]["intent_id"], revision=999)
    assert e.value.code == "intent_invalidated" and not h.log


def test_changed_situation_supersedes_without_writing(tmp_path, proof):
    h = Harness(tmp_path, proof)
    res = h.preview()
    h.live.put_view(h.plan["views"][0]["name"], "version: 1.1\n", owner="someone")     # appears after the preview
    out = h.confirm(res["intent"]["intent_id"])
    assert out["status"] == "superseded" and not h.log and out["new_preview"]


def test_local_failure_drops_staging_and_continues(tmp_path, proof):
    h = Harness(tmp_path, proof)
    view = h.plan["views"][0]["name"]
    h.failures[f"CREATE VIEW {_fqn(view)}"] = DatabricksStatementError("Statement st-9 FAILED: bad yaml",
                                                                       statement_id="st-9")
    out = h.deploy()
    res = {r["name"]: r["outcome"] for r in out["results"]}
    assert res[view] == "failed"
    assert not any(k.startswith(view.lower() + "__sbstg_") for k in h.live.staged)      # staging dropped
    others = [v["name"] for v in h.plan["views"][1:]]
    assert all(res[o] == "created" for o in others)
    assert out["status"] == ("partial" if others else "failed")
    if not others:
        assert "DeploymentError" in (out.get("error") or "")


def test_staging_validation_failure_is_local(tmp_path, proof):
    h = Harness(tmp_path, proof)
    h.live.read_failures["LIMIT 0"] = DatabricksStatementError("Statement s FAILED: MEASURE not found")
    out = h.deploy()
    assert all(r["outcome"] == "failed" for r in out["results"] if r["kind"] == "view")
    assert not h.live.staged and not any(v["name"].lower() in h.live.existing for v in h.plan["views"])
    assert not any(l.startswith("CREATE VIEW " + _fqn(h.plan["views"][0]["name"])) for l in h.log)


def test_uncertain_write_stops_everything_without_cleanup(tmp_path, proof):
    h = Harness(tmp_path, proof)
    view = h.plan["views"][0]["name"]
    h.failures[f"CREATE VIEW {_fqn(view)}"] = "apply_then_timeout"
    out = h.deploy()
    assert out["status"] == "uncertain" and "outcome unknown" in out["stopped"]
    assert h.log[-1] == f"CREATE VIEW {_fqn(view)}"                          # nothing after, not even the drop
    assert any(k.startswith(view.lower() + "__sbstg_") for k in h.live.staged)
    states = {j["kind"]: j["state"] for j in out["journal"]}
    assert states[S.CREATE_VIEW] == "uncertain"
    # the next preview requires reconciliation
    assert "reconcile_required" in [b["code"] for b in h.preview()["preview"]["model_blockers"]]
    # reconcile with statement evidence: the uncertain CREATE succeeded -> recorded; the staging
    # copy (created with a confirmed statement, never dropped) is cleaned up
    for sid in [f"st-{i}" for i in range(1, 10)]:
        h.live.statuses[sid] = "SUCCEEDED"
    res = reconcile_and_clean([store.lease_key(h.live.ms, CAT, SCH), store.lease_key(h.live.ms, CAT, STG)],
                              h.live, h.writer)
    assert res["status"] == "reconciled", res
    assert not h.live.staged and res["dropped"]
    assert store.owned_objects(h.pid, "")[_fqn(view)]["outcome"] == "reconciled"
    assert h.preview()["preview"]["status"] in ("ready", "no_op")


def test_cancelled_statement_is_uncertain_not_failed(tmp_path, proof):
    h = Harness(tmp_path, proof)
    view = h.plan["views"][0]["name"]
    h.failures[f"CREATE VIEW {_fqn(view)}"] = DatabricksStatementError("Statement st-3 CANCELED: user", statement_id="st-3")
    out = h.deploy()
    assert out["status"] == "uncertain"


def test_lease_loss_stops_writes(tmp_path, proof, monkeypatch):
    h = Harness(tmp_path, proof)
    calls = {"n": 0}
    real = store.renew_lease

    def flaky(key, run_id, **kw):
        calls["n"] += 1
        return calls["n"] <= 2 and real(key, run_id, **kw)        # lost before the second write

    monkeypatch.setattr(store, "renew_lease", flaky)
    out = h.deploy()
    assert out["status"] == "failed" and "lease" in out["stopped"] and len(h.log) == 1


def test_busy_lease_and_deterministic_order(tmp_path, proof):
    h = Harness(tmp_path, proof)
    res = h.preview()
    stg_key = store.lease_key(h.live.ms, CAT, STG)
    assert store.acquire_lease(stg_key, "someone-else")
    out = h.confirm(res["intent"]["intent_id"])
    assert out["status"] == "failed" and "lease" in out["reason"] and not h.log
    dest_key = store.lease_key(h.live.ms, CAT, SCH)
    assert store.acquire_lease(dest_key, "probe")                  # the first lease was released again


def test_applied_unverified_does_not_advance_history_until_reverified(tmp_path, proof):
    h = Harness(tmp_path, proof)
    first = h.deploy()
    assert first["status"] == "succeeded"
    view = h.plan["views"][0]["name"]
    plan2 = copy.deepcopy(h.plan)
    plan2["views"][0]["measures"][0]["description"] = "changed"
    h.plan = _republish(h.tmp, plan2, 2)
    h.live.grants_by[_fqn(view)] = [("analysts", "SELECT")]
    real_alter = FakeWriter._apply

    def alter_drops_grants(self, sql, target):
        real_alter(self, sql, target)
        if sql.startswith("ALTER VIEW"):
            self.live.grants_by[target] = []                          # grants lost by the update
    FakeWriter._apply = alter_drops_grants
    try:
        out = h.deploy()
    finally:
        FakeWriter._apply = real_alter
    r = next(x for x in out["results"] if x["name"] == view)
    assert r["outcome"] == "applied_unverified" and "grants" in r["detail"]
    st = store.owned_objects(h.pid, "")[_fqn(view)]
    assert not st["verified"]
    # names of that view were not re-recorded by the unverified run
    assert all(n["run_id"] != out["run_id"] for n in store.deployed_names(h.pid, "") if n["view_name"] == view)
    # next preview refuses to touch it
    pv = h.preview()["preview"]
    assert next(v for v in pv["views"] if v["name"] == view)["reasons"][0]["code"] == "last_deploy_unverified"
    assert reverify(h.pid, "", h.live)["results"][0]["outcome"] == "still_unverified"
    h.live.grants_by[_fqn(view)] = [("analysts", "SELECT")]       # an admin restores the grant
    assert reverify(h.pid, "", h.live)["results"][0]["outcome"] == "verified"
    assert store.owned_objects(h.pid, "")[_fqn(view)]["verified"]


def test_update_keeps_grants_and_is_verified(tmp_path, proof):
    h = Harness(tmp_path, proof)
    assert h.deploy()["status"] == "succeeded"
    plan2 = copy.deepcopy(h.plan)
    plan2["views"][0]["measures"][0]["description"] = "changed"
    h.plan = _republish(h.tmp, plan2, 2)
    out = h.deploy()
    view = h.plan["views"][0]["name"]
    assert next(x for x in out["results"] if x["name"] == view)["outcome"] == "updated"
    assert any(l.startswith("ALTER VIEW") for l in h.log)
    assert out["status"] in ("succeeded", "partial")
    assert h.deploy()["status"] == "no_op"                          # nothing changed now


def test_drift_right_before_promotion_is_local(tmp_path, proof):
    h = Harness(tmp_path, proof)
    assert h.deploy()["status"] == "succeeded"
    view = h.plan["views"][0]["name"]
    plan2 = copy.deepcopy(h.plan)
    plan2["views"][0]["measures"][0]["description"] = "changed"
    h.plan = _republish(h.tmp, plan2, 2)

    def edit_during_validation(sql):
        info = h.live.infos[_fqn(view)]
        if "filter:" not in info["view_definition"]:
            info["view_definition"] += "filter: x = 1\n"           # someone edits it in the UI meanwhile
    h.live.on_read = edit_during_validation
    out = h.deploy()
    r = next(x for x in out["results"] if x["name"] == view)
    assert r["outcome"] == "failed" and "changed since the preview" in r["detail"]
    assert not any(l.startswith(f"ALTER VIEW {_fqn(view)}") for l in h.log)


def test_stub_tables_then_incomplete_views_with_acknowledgement(tmp_path, proof):
    h = Harness(tmp_path, proof, stub="Store", create_stubs=True, publish_incomplete=True)
    res = h.preview()
    assert res["intent"]["requires_access_acknowledgement"]
    with pytest.raises(IntentError) as e:
        h.confirm(res["intent"]["intent_id"], ack=False)
    assert e.value.code == "acknowledgement_required" and not h.log
    out = h.confirm(res["intent"]["intent_id"], ack=True)
    assert out["status"] == "succeeded", out
    assert h.log[0].startswith(f"CREATE TABLE {_fqn('store')}")
    owned = store.owned_objects(h.pid, "")
    assert owned[_fqn("store")]["kind"] == "stub_table" and owned[_fqn("store")]["verified"]
    assert any(r["kind"] == "stub_table" for r in store.deployed_names(h.pid, ""))


def test_stub_readback_failure_skips_its_views(tmp_path, proof):
    h = Harness(tmp_path, proof, stub="Store", create_stubs=True, publish_incomplete=True)
    real = FakeWriter._apply

    def wrong_columns(self, sql, target):
        real(self, sql, target)
        if sql.startswith("CREATE TABLE"):
            self.live.infos[target]["columns"] = [{"name": "other", "type": "string"}]
    FakeWriter._apply = wrong_columns
    try:
        out = h.deploy()
    finally:
        FakeWriter._apply = real
    stub = next(r for r in out["results"] if r["kind"] == "stub_table")
    assert stub["outcome"] == "failed"
    assert any(r["outcome"] == "skipped" for r in out["results"] if r["kind"] == "view")
    assert not store.owned_objects(h.pid, "")[_fqn("store")]["verified"]


def test_deploy_authority(monkeypatch):
    from fastapi import HTTPException

    import semabridge.api.controllers.databricks_deploy_controller as c

    monkeypatch.setattr(c, "auth_is_enabled", lambda: True)
    monkeypatch.setattr(c, "_is_admin_user", lambda u: u == "admin")
    monkeypatch.setattr(c, "is_project_owned_by_user", lambda p, u, **k: u == "owner")
    target = {"deployers": ["42", " 43 "]}
    for user in ("admin", "owner", "42", "43"):
        c.assert_deploy_authority("p", user, target)
    for user in ("7", None):
        with pytest.raises(HTTPException) as e:
            c.assert_deploy_authority("p", user, target)
        assert e.value.status_code in (401, 403)


# --- API -------------------------------------------------------------------------------------------

@pytest.fixture
def api_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import semabridge.api.controllers.databricks_deploy_controller as c
    import semabridge.api.controllers.mappings_controller as mc

    monkeypatch.setenv("AUTH_ENABLED", "false")
    cfgs = {}
    monkeypatch.setattr(mc, "_stored_config", lambda pid: cfgs.get(pid, {}))
    app = FastAPI()
    app.include_router(c.router)
    return TestClient(app), cfgs, c


def test_api_preview_confirm_status_and_idempotency(tmp_path, proof, monkeypatch, api_client):
    import time
    from types import SimpleNamespace

    client, cfgs, c = api_client
    h = Harness(tmp_path, proof)
    cfgs[h.pid] = {"target": {"type": "databricks", "catalog": CAT, "schema": SCH,
                              "staging_schema": f"{CAT}.{STG}", "deploy_principals": [ADMINS]}}
    monkeypatch.setattr(c, "_connection", lambda target: SimpleNamespace(host=proof))
    monkeypatch.setattr(c, "_clients", lambda info, run_id=None: (h.live, h.writer, "wh1"))
    monkeypatch.setattr(c, "_plan", lambda pid, sub, cache=None: (h.tmp, h.plan, 1))
    monkeypatch.setattr(c, "_allow_list", lambda: [])

    class InlineThread:   # run the background deploy to completion (SQLite test DB: no concurrent writers)
        def __init__(self, target, name=None, daemon=None):
            self.target = target

        def start(self):
            self.target()
    import threading as real_threading

    monkeypatch.setattr(c, "threading", SimpleNamespace(Thread=InlineThread, Event=real_threading.Event))
    r = client.post(f"/api/projects/{h.pid}/databricks/deploy/preview", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preview"]["status"] == "ready" and body["intent"]["intent_id"]
    iid = body["intent"]["intent_id"]
    r = client.post(f"/api/projects/{h.pid}/databricks/deploy", json={"intent_id": iid})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    st = client.get(f"/api/projects/{h.pid}/databricks/deploy/runs/{run_id}").json()
    assert st["status"] == "succeeded", st
    assert st["journal"] and all(j["state"] == "succeeded" for j in st["journal"])
    r = client.post(f"/api/projects/{h.pid}/databricks/deploy", json={"intent_id": iid})
    assert r.json() == {"run_id": run_id, "status": "existing"}
    # a project that is not Databricks / an unknown intent
    cfgs["sf"] = {"target": {"type": "snowflake"}}
    assert client.post("/api/projects/sf/databricks/deploy/preview", json={}).status_code == 400
    assert client.post(f"/api/projects/{h.pid}/databricks/deploy", json={"intent_id": "nope"}).status_code == 404
    assert client.post("/api/projects/preview-x/databricks/deploy/preview", json={}).status_code == 400


def test_api_requires_deploy_authority(tmp_path, monkeypatch, api_client):
    client, cfgs, c = api_client
    monkeypatch.setattr(c, "auth_is_enabled", lambda: True)
    monkeypatch.setattr(c, "require_request_user_id", lambda request: "7")
    monkeypatch.setattr(c, "_is_admin_user", lambda u: False)
    monkeypatch.setattr(c, "is_project_owned_by_user", lambda p, u, **k: False)
    cfgs["p-auth"] = {"target": {"type": "databricks", "deployers": ["42"]}}
    for path in ("deploy/preview", "deploy", "deploy/reconcile", "deploy/reverify"):
        body = {"intent_id": "x"} if path == "deploy" else {}
        assert client.post(f"/api/projects/p-auth/databricks/{path}", json=body).status_code == 403
    assert client.get("/api/projects/p-auth/databricks/deploy/runs/r1").status_code == 403


# --- regressions from the independent review -------------------------------------------------------

def test_api_expired_intent_is_409_not_running(tmp_path, proof, monkeypatch, api_client):
    from types import SimpleNamespace

    from sqlalchemy import update

    from semabridge.repository.orm.project_models import DatabricksDeployIntent as I
    from semabridge.repository.orm.session_factory import db_manager

    client, cfgs, c = api_client
    h = Harness(tmp_path, proof)
    cfgs[h.pid] = {"target": {"type": "databricks", "catalog": CAT, "schema": SCH,
                              "staging_schema": f"{CAT}.{STG}", "deploy_principals": [ADMINS]}}
    monkeypatch.setattr(c, "_connection", lambda target: SimpleNamespace(host=proof))
    monkeypatch.setattr(c, "_clients", lambda info, run_id=None: (h.live, h.writer, "wh1"))
    monkeypatch.setattr(c, "_plan", lambda pid, sub, cache=None: (h.tmp, h.plan, 1))
    monkeypatch.setattr(c, "_allow_list", lambda: [])
    iid = client.post(f"/api/projects/{h.pid}/databricks/deploy/preview", json={}).json()["intent"]["intent_id"]
    with db_manager.get_session() as s:
        s.execute(update(I).where(I.intent_id == iid).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    r = client.post(f"/api/projects/{h.pid}/databricks/deploy", json={"intent_id": iid})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "intent_expired" and not h.log


def test_readback_exception_records_applied_unverified_and_stops(tmp_path, proof):
    from semabridge.target_backends.databricks.deploy.live import LiveError

    h = Harness(tmp_path, proof)
    view = h.plan["views"][0]["name"]
    real = h.live.table_info
    created = {"done": False}

    def flaky(fqn):
        if created["done"] and normalize_name(fqn) == _fqn(view):
            raise LiveError("HTTP 500 while reading")
        return real(fqn)
    h.live.table_info = flaky
    real_apply = FakeWriter._apply

    def mark(self, sql, target):
        real_apply(self, sql, target)
        if sql.startswith("CREATE VIEW") and target == _fqn(view):
            created["done"] = True
    FakeWriter._apply = mark
    try:
        out = h.deploy()
    finally:
        FakeWriter._apply = real_apply
    r = next(x for x in out["results"] if x["name"] == view)
    assert r["outcome"] == "applied_unverified" and "read-back failed" in r["detail"]
    assert out["status"] in ("partial", "uncertain")
    st = store.owned_objects(h.pid, "")[_fqn(view)]
    assert st["outcome"] == "applied_unverified" and not st["verified"]


def test_reverify_requires_the_recorded_object_id(tmp_path, proof):
    pid = _pid()
    run = "rv-" + uuid.uuid4().hex[:8]
    store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table", identity=ME)
    body = "version: 1.1\nsource: a.b.c\n"
    store.record_object(run_id=run, project_id=pid, model_key="", kind="view", fqn=_fqn("v1"),
                        outcome="applied_unverified", uc_object_id=None,
                        details={"expected": {"definition_hash": D.definition_hash(body), "owner": ME}})
    store.finish_run(run, "partial", {})
    live = ExecLive({"tables": []})
    live.put_view("v1", body, oid="id-recreated")                  # same owner + YAML, different object
    assert reverify(pid, "", live)["results"][0]["outcome"] == "still_unverified"
    assert not store.owned_objects(pid, "")[_fqn("v1")]["verified"]


def test_unreadable_grants_before_alter_is_local_and_no_alter(tmp_path, proof):
    h = Harness(tmp_path, proof)
    assert h.deploy()["status"] == "succeeded"
    view = h.plan["views"][0]["name"]
    plan2 = copy.deepcopy(h.plan)
    plan2["views"][0]["measures"][0]["description"] = "changed"
    h.plan = _republish(h.tmp, plan2, 2)
    h.live.show_grants = lambda fqn: None
    out = h.deploy()
    r = next(x for x in out["results"] if x["name"] == view)
    assert r["outcome"] == "failed" and "grants" in r["detail"]
    assert not any(line.startswith("ALTER VIEW") for line in h.log)


def test_staging_drop_failure_is_not_success(tmp_path, proof):
    h = Harness(tmp_path, proof)
    real = FakeWriter.execute

    def drop_fails(self, sql):
        if sql.startswith("DROP VIEW"):
            validate_databricks_sql(sql, WRITE, self.plan)
            raise DatabricksStatementError("Statement st-d FAILED: permission", statement_id="st-d")
        return real(self, sql)
    FakeWriter.execute = drop_fails
    try:
        out = h.deploy()
    finally:
        FakeWriter.execute = real
    assert out["status"] == "partial"
    assert any(r["kind"] == "staging_view" and r["outcome"] == "failed" for r in out["results"])


def test_different_warehouse_or_tightened_settings_supersede(tmp_path, proof):
    h = Harness(tmp_path, proof)
    res = h.preview()
    h.live.warehouse_id = "another-warehouse"
    out = h.confirm(res["intent"]["intent_id"])
    assert out["status"] == "superseded" and "warehouse" in out["reason"] and not h.log
    h2 = Harness(tmp_path / "b", proof)
    res = h2.preview()
    out = confirm_intent(res["intent"]["intent_id"], project_id=h2.pid, model_key="", user_id="u1",
                         acknowledge_destination_access=False, plan=h2.plan,
                         current_revision=bundle.read_current(h2.tmp).get("plan_revision"), live=h2.live,
                         write_client=h2.writer, overrides={"deploy_principals": []})   # admins removed meanwhile
    assert out["status"] == "superseded" and not h2.log


def test_reconcile_records_names_and_settles_uncertain_run(tmp_path, proof):
    h = Harness(tmp_path, proof)
    view = h.plan["views"][0]["name"]
    h.failures[f"CREATE VIEW {_fqn(view)}"] = "apply_then_timeout"
    out = h.deploy()
    assert out["status"] == "uncertain"
    for sid in [f"st-{i}" for i in range(1, 10)]:
        h.live.statuses[sid] = "SUCCEEDED"
    reconcile_and_clean([store.lease_key(h.live.ms, CAT, SCH), store.lease_key(h.live.ms, CAT, STG)], h.live, h.writer)
    assert any(n["kind"] == "view" and n["target_name"] == view for n in store.deployed_names(h.pid, ""))
    assert store.run_info(out["run_id"])["status"] == "partial"


def test_stale_running_run_is_swept(tmp_path):
    pid = _pid()
    run = "st-" + uuid.uuid4().hex[:8]
    store.start_run(run_id=run, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                    catalog=CAT, schema=SCH, stub_form="empty_table")
    assert store.sweep_stale_runs(pid, "") == []                                   # too recent
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert store.sweep_stale_runs(pid, "", now=later) == [run]
    assert store.run_info(run)["status"] == "uncertain"


def test_view_without_owner_is_not_updatable(tmp_path, proof):
    h = Harness(tmp_path, proof)
    assert h.deploy()["status"] == "succeeded"
    view = h.plan["views"][0]["name"]
    h.live.infos[_fqn(view)]["owner"] = None
    plan2 = copy.deepcopy(h.plan)
    plan2["views"][0]["measures"][0]["description"] = "changed"
    h.plan = _republish(h.tmp, plan2, 2)
    pv = h.preview()["preview"]
    assert next(v for v in pv["views"] if v["name"] == view)["reasons"][0]["code"] == "not_owner"


def test_stub_readback_exception_records_and_stops(tmp_path, proof):
    from semabridge.target_backends.databricks.deploy.live import LiveError

    h = Harness(tmp_path, proof, stub="Store", create_stubs=True, publish_incomplete=True)
    real = h.live.table_info

    def flaky(fqn):
        if normalize_name(fqn) == _fqn("store") and "store" in h.live.existing:
            raise LiveError("HTTP 500 while reading")
        return real(fqn)
    res = h.preview()
    h.live.table_info = flaky
    out = h.confirm(res["intent"]["intent_id"], ack=True)
    assert out["status"] == "uncertain" and "read-back failed" in out["stopped"]
    assert len(h.log) == 1                                              # nothing written after the stop
    st = store.owned_objects(h.pid, "")[_fqn("store")]
    assert st["outcome"] == "applied_unverified" and st["kind"] == "stub_table"


# --- project settings safety net ---------------------------------------------------------------

def test_effective_target_completes_a_bare_target_block():
    from semabridge.api.controllers.mappings_controller import _effective_target, _target_block

    broken = {"targets": [{"type": "databricks", "catalog": "workspace", "schema": "semabridge_dev"}],
              "target": {"type": "databricks"}}
    assert _effective_target(broken) == {"type": "databricks", "catalog": "workspace", "schema": "semabridge_dev"}
    assert _target_block(broken) is broken["target"]                 # the writer still edits the stored block
    assert _effective_target({"target": {"type": "databricks", "schema": "x"},
                              "targets": [{"type": "databricks", "schema": "y", "catalog": "c"}]}) == {
        "type": "databricks", "schema": "x", "catalog": "c"}
    assert _effective_target({"targets": [{"type": "snowflake", "database": "D"}]}) == {"type": "snowflake", "database": "D"}


def test_preview_blocks_when_catalog_or_schema_is_missing(monkeypatch, api_client):
    client, cfgs, c = api_client
    cfgs["p-incomplete"] = {"target": {"type": "databricks", "staging_schema": f"{CAT}.{STG}"}}
    r = client.post("/api/projects/p-incomplete/databricks/deploy/preview", json={})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "project_settings_incomplete"


def test_global_default_staging_schema(monkeypatch):
    import semabridge.api.controllers.databricks_deploy_controller as c

    monkeypatch.setattr(c, "_global_staging_default", lambda: "workspace.global_staging")
    assert c._staging({"catalog": "workspace"}) == ("workspace", "global_staging")
    assert c._staging({"staging_schema": "workspace.mine"}) == ("workspace", "mine")      # the project wins
    monkeypatch.setattr(c, "_global_staging_default", lambda: "")
    assert c._staging({}) is None


# --- dry-run plans stored under the preview id -------------------------------------------------

def test_plan_under_preview_id_is_accepted_only_for_its_project(tmp_path, monkeypatch):
    import json

    from fastapi import HTTPException

    import semabridge.api.controllers.databricks_deploy_controller as c
    from semabridge.target_backends.databricks import replan

    monkeypatch.chdir(tmp_path)
    out = replan.output_dir("preview-abc123def456", None)
    out.mkdir(parents=True)
    plan = {"meta": {"revision": 5, "status": "valid", "source": "dry_run",
                     "cache_ref": {"project_id": "preview-abc123def456", "binding_project_id": "proj-a"}}}
    (out / replan.PLAN_JSON).write_text(json.dumps(plan), encoding="utf-8")
    (out / replan.STATE_FILE).write_text(json.dumps({"revision": 5, "status": "valid"}), encoding="utf-8")
    got_dir, got_plan, _ = c._plan("proj-a", None, "preview-abc123def456")
    assert got_plan["meta"]["revision"] == 5
    with pytest.raises(HTTPException) as e:
        c._plan("proj-b", None, "preview-abc123def456")              # someone else's dry run
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        c._plan("proj-a", None, "proj-b")                             # only this project or a preview id
    assert e.value.status_code == 400


def test_dry_run_reads_history_and_records_owner_of_the_real_project():
    from types import SimpleNamespace

    from semabridge.core.engine.targets.databricks import owning_project_id

    ctx = SimpleNamespace(project_id="preview-abc123def456", config_payload={"binding_project_id": "proj-a"})
    assert owning_project_id(ctx) == "proj-a"
    assert owning_project_id(SimpleNamespace(project_id="proj-x", config_payload={})) == "proj-x"


def test_calculated_column_is_never_a_join_key_on_stub_tables():
    """Store_Sales: Sales[ReportingPeriodID] is a calculated column; with stub tables
    the Time join must be left out (it would reference a column that does not exist)."""
    repo = Path(__file__).resolve().parents[2]
    pbix = repo / "Examples" / "planner_models" / "Store_Sales.pbix"
    if not pbix.exists():
        pytest.skip("Store_Sales.pbix not available")
    sys.path.insert(0, str(repo / "Tests" / "golden"))
    from test_snowflake_golden import _pbix_to_osi
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views
    from semabridge.target_backends.databricks.tables import plan_tables, rewrite_measure_columns
    from semabridge.target_rules import DATABRICKS_RULES

    osi, rc = _pbix_to_osi(pbix)
    sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi, row_counts=rc)
    tp = plan_tables(sml, {("workspace", "semabridge_dev"): {}}, source_schemas=[("workspace", "semabridge_dev")],
                     on_missing_source="stub")
    for m in sml.metrics:
        m.sql_expression = rewrite_measure_columns(m.sql_expression, tp.rewrite_map())
    plan = plan_metric_views(sml, tp, PlannerSettings(security_roles=0)).as_dict()
    sales = next(v for v in plan["views"] if v["source_dataset"] == "Sales")
    assert "Time" not in [j["dataset"] for j in sales["joins"]]
    assert any("calculated column" in n and "ReportingPeriodID" in n for n in sales["notes"])
    assert "REPORTINGPERIODID" not in json.dumps(sales["joins"])
