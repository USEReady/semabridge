"""Phase 3.3: guarded Databricks client, warehouse discovery, non-waking
connection test and permission audit (Jira CS-10 and the guard story).

Offline: a fake Databricks answers every HTTP call, so nothing reaches the
network. API shapes follow the official Databricks REST reference.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from semabridge.target_backends.databricks.auth import (
    DatabricksAuthError,
    DatabricksConnectionInfo,
    TokenProvider,
)
from semabridge.target_backends.databricks.client import (
    DatabricksSqlClient,
    DatabricksSqlError,
    DatabricksStatementError,
    DatabricksTimeoutError,
)
from semabridge.target_backends.databricks.connection import (
    audit_databricks_permissions,
    check_databricks_connection,
)
from semabridge.target_backends.databricks.guard import (
    READ,
    WRITE,
    DatabricksGuardError,
    DatabricksSqlGuard,
    WritePlan,
    normalize_name,
    validate_databricks_sql,
)
from semabridge.target_backends.databricks.http import HttpResponse, TransportError
from semabridge.target_backends.databricks.warehouses import Warehouse, choose_warehouse

TOKEN = "dapi-secret-token"
INFO = DatabricksConnectionInfo(host="https://dbc-1.cloud.databricks.com/", token=TOKEN, warehouse_id="wh1",
                                catalog="workspace", schema="semabridge_dev")


class FakeDatabricks:
    """Scripted responses per (method, path-prefix); records every call."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.routes: Dict[tuple, List[Any]] = {}

    def on(self, method: str, path: str, *responses: Any) -> "FakeDatabricks":
        self.routes.setdefault((method, path), []).extend(responses)
        return self

    def request(self, method, url, *, headers=None, json=None, data=None, params=None, timeout=30):
        path = url.split("cloud.databricks.com", 1)[1]
        self.calls.append({"method": method, "path": path, "headers": dict(headers or {}), "json": json,
                           "data": data, "params": params})
        for (m, p), queue in self.routes.items():
            if m == method and path.startswith(p) and queue:
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, Exception):
                    raise item
                return item
        return HttpResponse(status=404, body={"message": f"no route {method} {path}"})


def ok(body: Any) -> HttpResponse:
    return HttpResponse(status=200, body=body)


def succeeded(statement_id="s1", rows=None, columns=None, next_link=None) -> HttpResponse:
    columns = columns or [{"name": "x", "type_name": "INT", "position": 0}]
    result = {"chunk_index": 0, "data_array": rows if rows is not None else [["1"]]}
    if next_link:
        result["next_chunk_internal_link"] = next_link
    return ok({"statement_id": statement_id, "status": {"state": "SUCCEEDED"},
               "manifest": {"schema": {"columns": columns}, "truncated": False}, "result": result})


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: List[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(fake: FakeDatabricks, clock: Optional[Clock] = None, **kwargs) -> DatabricksSqlClient:
    clock = clock or Clock()
    return DatabricksSqlClient(INFO, transport=fake, sleep=clock.sleep, clock=clock, **kwargs)


# --- guard ---------------------------------------------------------------------------

PLAN = WritePlan.of(
    views=["workspace.semabridge_dev.store_sales_dbx"],
    staging_views=["workspace.semabridge_dev.store_sales_dbx__sbstg_ab12cd34"],
    grant_views=["workspace.semabridge_dev.store_sales_dbx"],
)
YAML_VIEW = "WITH METRICS LANGUAGE YAML AS $$\nversion: 1.1\nsource: workspace.sb_tests.sales\n$$"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from workspace.sb_tests.sales limit 5",
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        "SHOW SCHEMAS IN workspace",
        "SHOW GRANTS ON VIEW workspace.semabridge_dev.v",
        "DESCRIBE TABLE EXTENDED `workspace`.`sb_tests`.`sales` AS JSON",
        "DESC workspace.sb_tests.sales",
        "USE CATALOG workspace",
        "SELECT MEASURE(`Total Sales`) FROM workspace.semabridge_dev.v LIMIT 0",
        "SELECT 'drop table x; delete' AS s",          # keywords inside a string
        "SELECT `drop` FROM t -- DROP TABLE t\n",        # quoted name and a comment
        "SELECT 1;",                                     # one trailing semicolon is fine
    ],
)
def test_read_statements_allowed(sql) -> None:
    validate_databricks_sql(sql, READ)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE workspace.sb_tests.sales",
        "CREATE TABLE IF NOT EXISTS workspace.semabridge_dev.stub (x INT)",
        "CREATE OR REPLACE TABLE a.b.c AS SELECT 1",
        "ALTER TABLE workspace.sb_tests.sales ADD COLUMN y INT",
        "TRUNCATE TABLE workspace.sb_tests.sales",
        "CREATE SCHEMA workspace.new_one",
        "DROP SCHEMA workspace.sb_tests CASCADE",
        "INSERT INTO a.b.c VALUES (1)",
        "MERGE INTO a.b.c USING d ON true WHEN MATCHED THEN DELETE",
        "DELETE FROM a.b.c",
        "UPDATE a.b.c SET x = 1",
        "REVOKE SELECT ON TABLE a.b.c FROM `u`",
        "SET ansi_mode = false",
        "OPTIMIZE a.b.c",
        "SELECT 1; DROP TABLE t",                         # second statement
        "WITH t AS (SELECT 1) INSERT INTO a.b.c SELECT * FROM t",
    ],
)
def test_destructive_statements_always_refused(sql) -> None:
    for mode, plan in ((READ, None), (WRITE, PLAN)):
        with pytest.raises(DatabricksGuardError):
            validate_databricks_sql(sql, mode, plan)


def test_write_statements_need_write_mode_and_a_planned_object() -> None:
    create = f"CREATE OR REPLACE VIEW `workspace`.`semabridge_dev`.`store_sales_dbx__sbstg_ab12cd34` {YAML_VIEW}"
    with pytest.raises(DatabricksGuardError):
        validate_databricks_sql(create, READ)
    assert validate_databricks_sql(create, WRITE, PLAN).kind == "CREATE_METRIC_VIEW"
    alter = "ALTER VIEW workspace.semabridge_dev.store_sales_dbx AS $$\nversion: 1.1\n$$"
    assert validate_databricks_sql(alter, WRITE, PLAN).object_name == "workspace.semabridge_dev.store_sales_dbx"
    drop = "DROP VIEW IF EXISTS workspace.semabridge_dev.store_sales_dbx__sbstg_ab12cd34"
    assert validate_databricks_sql(drop, WRITE, PLAN).kind == "DROP_STAGING_VIEW"
    grant = "GRANT SELECT ON VIEW workspace.semabridge_dev.store_sales_dbx TO `analysts`"
    assert validate_databricks_sql(grant, WRITE, PLAN).kind == "GRANT"


@pytest.mark.parametrize(
    "sql",
    [
        f"CREATE OR REPLACE VIEW workspace.semabridge_dev.someone_elses_view {YAML_VIEW}",   # not planned
        "CREATE OR REPLACE VIEW workspace.semabridge_dev.store_sales_dbx AS SELECT 1",      # not a metric view
        f"CREATE VIEW store_sales_dbx {YAML_VIEW}",                                          # not qualified
        "DROP VIEW workspace.semabridge_dev.store_sales_dbx",                                # live view
        "GRANT SELECT ON VIEW workspace.sb_tests.sales TO `x`",                              # not our view
        "CREATE MATERIALIZED VIEW workspace.semabridge_dev.store_sales_dbx AS SELECT 1",
    ],
)
def test_unplanned_writes_refused(sql) -> None:
    with pytest.raises(DatabricksGuardError):
        validate_databricks_sql(sql, WRITE, PLAN)


def test_yaml_body_is_never_inspected() -> None:
    sql = ("CREATE OR REPLACE VIEW workspace.semabridge_dev.store_sales_dbx WITH METRICS LANGUAGE YAML AS $$\n"
           "source: SELECT 1; DROP TABLE x -- 'quote\nmeasures: []\n$$")
    assert validate_databricks_sql(sql, WRITE, PLAN).kind == "CREATE_METRIC_VIEW"


def test_names_normalize_and_plans_are_per_run() -> None:
    assert normalize_name("`Workspace`.`Sem``x`.View") == "workspace.sem`x.view"
    with pytest.raises(ValueError):
        DatabricksSqlGuard(WRITE)  # write mode without a plan
    other_run = WritePlan.of(views=["workspace.semabridge_dev.other"])
    create = f"CREATE OR REPLACE VIEW workspace.semabridge_dev.store_sales_dbx {YAML_VIEW}"
    with pytest.raises(DatabricksGuardError):
        validate_databricks_sql(create, WRITE, other_run)


# --- client ---------------------------------------------------------------------------


def test_refused_statement_never_reaches_the_network() -> None:
    fake = FakeDatabricks()
    with pytest.raises(DatabricksGuardError):
        client(fake).execute("DROP TABLE workspace.sb_tests.sales")
    assert fake.calls == []


def test_successful_statement_request_and_rows() -> None:
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements", succeeded(
        columns=[{"name": "n", "type_name": "INT", "position": 0}, {"name": "s", "type_name": "STRING", "position": 1}],
        rows=[["3", "a"], [None, "b"]],
    ))
    result = client(fake, query_tags={"run_id": "r1"}).execute("SELECT n, s FROM t")
    assert result.rows == [{"n": 3, "s": "a"}, {"n": None, "s": "b"}]
    body = fake.calls[0]["json"]
    assert body["warehouse_id"] == "wh1" and body["catalog"] == "workspace" and body["schema"] == "semabridge_dev"
    assert body["wait_timeout"] == "10s" and body["on_wait_timeout"] == "CONTINUE"
    assert {"key": "app", "value": "semabridge"} in body["query_tags"]
    assert {"key": "run_id", "value": "r1"} in body["query_tags"]
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_polls_until_finished() -> None:
    clock = Clock()
    fake = (FakeDatabricks()
            .on("POST", "/api/2.0/sql/statements", ok({"statement_id": "s9", "status": {"state": "PENDING"}}))
            .on("GET", "/api/2.0/sql/statements/s9",
                ok({"statement_id": "s9", "status": {"state": "RUNNING"}}), succeeded("s9")))
    assert client(fake, clock).execute("SELECT 1").scalar() == 1
    assert [c["method"] for c in fake.calls] == ["POST", "GET", "GET"]


def test_failed_state_with_http_200_is_an_error() -> None:
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements", ok({
        "statement_id": "s2",
        "status": {"state": "FAILED", "error": {"error_code": "BAD_REQUEST",
                                                 "message": "[TABLE_OR_VIEW_NOT_FOUND] x", "sql_state": "42P01"}},
    }))
    with pytest.raises(DatabricksStatementError) as info:
        client(fake).execute("SELECT * FROM missing")
    assert (info.value.error_code, info.value.sql_state, info.value.statement_id) == ("BAD_REQUEST", "42P01", "s2")
    assert TOKEN not in str(info.value)


def test_deadline_cancels_the_statement() -> None:
    clock = Clock()
    running = ok({"statement_id": "s3", "status": {"state": "RUNNING"}})
    fake = (FakeDatabricks().on("POST", "/api/2.0/sql/statements/s3/cancel", ok({}))
            .on("POST", "/api/2.0/sql/statements", running).on("GET", "/api/2.0/sql/statements/s3", running))
    with pytest.raises(DatabricksTimeoutError):
        client(fake, clock, deadline_seconds=20).execute("SELECT 1")
    assert any(c["path"].endswith("/s3/cancel") for c in fake.calls)


def test_rate_limit_is_retried_with_retry_after() -> None:
    clock = Clock()
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements",
                               HttpResponse(status=429, body={"message": "slow down"}, retry_after=2.0), succeeded())
    assert client(fake, clock).execute("SELECT 1").scalar() == 1
    assert len(fake.calls) == 2 and 1.5 <= clock.slept[0] <= 2.5


def test_real_errors_are_not_retried() -> None:
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements", HttpResponse(status=400, body={"message": "bad"}))
    with pytest.raises(DatabricksSqlError) as info:
        client(fake).execute("SELECT 1")
    assert info.value.http_status == 400 and len(fake.calls) == 1


def test_ambiguous_network_failure_retries_reads_but_not_writes() -> None:
    lost = TransportError("read timeout", sent=True)
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements", lost, succeeded())
    assert client(fake).execute("SELECT 1").scalar() == 1  # a read is safe to resubmit

    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements", lost, succeeded())
    guard = DatabricksSqlGuard(WRITE, PLAN)
    with pytest.raises(DatabricksSqlError):
        client(fake, guard=guard).execute(f"CREATE OR REPLACE VIEW workspace.semabridge_dev.store_sales_dbx {YAML_VIEW}")
    assert len(fake.calls) == 1  # the write is not re-submitted

    never_sent = TransportError("connection refused", sent=False)
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements", never_sent, succeeded())
    client(fake, guard=guard).execute(f"CREATE OR REPLACE VIEW workspace.semabridge_dev.store_sales_dbx {YAML_VIEW}")
    assert len(fake.calls) == 2  # never reached the server: safe to retry


def test_result_chunks_are_followed() -> None:
    fake = (FakeDatabricks()
            .on("POST", "/api/2.0/sql/statements",
                succeeded(rows=[["1"], ["2"]], next_link="/api/2.0/sql/statements/s1/result/chunks/1"))
            .on("GET", "/api/2.0/sql/statements/s1/result/chunks/1", ok({"chunk_index": 1, "data_array": [["3"]]})))
    assert [r["x"] for r in client(fake).execute("SELECT x FROM t").rows] == [1, 2, 3]


def test_no_warehouse_means_no_statement() -> None:
    info = DatabricksConnectionInfo(host="dbc-1.cloud.databricks.com", token=TOKEN)
    with pytest.raises(DatabricksSqlError, match="No SQL warehouse"):
        DatabricksSqlClient(info, transport=FakeDatabricks()).execute("SELECT 1")


# --- auth -------------------------------------------------------------------------------


def test_service_principal_token_exchange_is_cached() -> None:
    TokenProvider._oauth_cache.clear()
    fake = FakeDatabricks().on("POST", "/oidc/v1/token", ok({"access_token": "oauth-1", "expires_in": 3600}))
    info = DatabricksConnectionInfo(host="dbc-1.cloud.databricks.com", auth_type="service_principal",
                                    client_id="cid", client_secret="csecret")
    tokens = TokenProvider(info, fake, clock=lambda: 1000.0)
    assert tokens.token() == "oauth-1" and tokens.token() == "oauth-1"
    assert len(fake.calls) == 1
    assert fake.calls[0]["data"]["grant_type"] == "client_credentials" and fake.calls[0]["data"]["scope"] == "all-apis"
    assert "csecret" not in repr(info)


def test_missing_credentials_fail_clearly() -> None:
    with pytest.raises(DatabricksAuthError):
        TokenProvider(DatabricksConnectionInfo(host="dbc-1.cloud.databricks.com")).token()
    with pytest.raises(DatabricksAuthError):
        DatabricksConnectionInfo(host="")


def test_connection_info_from_env_without_warehouse() -> None:
    info = DatabricksConnectionInfo.from_env({"DATABRICKS_HOST": "https://dbc-9.cloud.databricks.com",
                                              "DATABRICKS_TOKEN": TOKEN})
    assert info.host == "dbc-9.cloud.databricks.com" and info.warehouse_id is None


# --- warehouses -------------------------------------------------------------------------


def _wh(i, state, serverless=True, name=None):
    return Warehouse(id=i, name=name or i, state=state, serverless=serverless, warehouse_type="PRO")


def test_warehouse_choice_prefers_running_then_starting_then_stopped_and_serverless() -> None:
    assert choose_warehouse([_wh("a", "STOPPED"), _wh("b", "RUNNING", False), _wh("c", "STARTING")]).id == "b"
    assert choose_warehouse([_wh("a", "STOPPED"), _wh("c", "STARTING")]).id == "c"
    assert choose_warehouse([_wh("a", "STOPPED", False), _wh("b", "STOPPED", True)]).id == "b"
    assert choose_warehouse([_wh("a", "DELETED"), _wh("b", "DELETING")]) is None


# --- non-waking connection test ---------------------------------------------------------

ME = ok({"userName": "nimish@example.com", "displayName": "Nimish", "groups": [{"display": "data_engineers"}]})
WAREHOUSES = ok({"warehouses": [
    {"id": "w-stopped", "name": "Starter", "state": "STOPPED", "warehouse_type": "PRO", "enable_serverless_compute": True},
    {"id": "w-gone", "name": "Old", "state": "DELETED"},
]})


def test_connection_check_uses_no_sql_and_auto_discovers() -> None:
    info = DatabricksConnectionInfo(host="dbc-1.cloud.databricks.com", token=TOKEN)
    fake = FakeDatabricks().on("GET", "/api/2.0/preview/scim/v2/Me", ME).on("GET", "/api/2.0/sql/warehouses", WAREHOUSES)
    result = check_databricks_connection(info, client=DatabricksSqlClient(info, transport=fake))
    assert result["status"] == "success"
    assert result["warehouse"]["id"] == "w-stopped" and result["warehouse_source"] == "discovered"
    assert "start on the first query" in result["message"]
    assert all(c["method"] == "GET" for c in fake.calls)  # nothing that could start the warehouse
    assert not any("/sql/statements" in c["path"] for c in fake.calls)


def test_connection_check_reports_bad_token() -> None:
    fake = FakeDatabricks().on("GET", "/api/2.0/preview/scim/v2/Me", HttpResponse(status=401, body={"message": "invalid token"}))
    result = check_databricks_connection(INFO, client=DatabricksSqlClient(INFO, transport=fake))
    assert result["status"] == "failed" and "401" in result["message"] and TOKEN not in result["message"]


# --- permission audit -----------------------------------------------------------------------

UC = "/api/2.1/unity-catalog"


def _effective(*assignments):
    return ok({"privilege_assignments": [
        {"principal": who, "privileges": [{"privilege": p} for p in privs]} for who, privs in assignments
    ]})


def _audit(fake):
    return audit_databricks_permissions(INFO, client=DatabricksSqlClient(INFO, transport=fake))


def test_audit_ok_via_group_and_inherited_grants() -> None:
    fake = (FakeDatabricks().on("GET", "/api/2.0/preview/scim/v2/Me", ME)
            .on("GET", f"{UC}/catalogs/workspace", ok({"owner": "admins"}))
            .on("GET", f"{UC}/schemas/workspace.semabridge_dev", ok({"owner": "admins"}))
            .on("GET", f"{UC}/effective-permissions/catalog/workspace", _effective(("data_engineers", ["USE_CATALOG"])))
            .on("GET", f"{UC}/effective-permissions/schema/workspace.semabridge_dev",
                _effective(("data_engineers", ["USE SCHEMA", "CREATE TABLE"]))))
    report = _audit(fake)
    assert report["status"] == "ok" and report["remediation_sql"] == []
    assert all(c["method"] == "GET" for c in fake.calls)


def test_audit_missing_privileges_prints_grants() -> None:
    fake = (FakeDatabricks().on("GET", "/api/2.0/preview/scim/v2/Me", ME)
            .on("GET", f"{UC}/catalogs/workspace", ok({"owner": "admins"}))
            .on("GET", f"{UC}/schemas/workspace.semabridge_dev", ok({"owner": "admins"}))
            .on("GET", f"{UC}/effective-permissions/catalog/workspace", _effective(("nimish@example.com", ["USE_CATALOG"])))
            .on("GET", f"{UC}/effective-permissions/schema/workspace.semabridge_dev",
                _effective(("nimish@example.com", ["USE_SCHEMA"]))))
    report = _audit(fake)
    assert report["status"] == "missing"
    assert report["remediation_sql"] == [
        "GRANT CREATE TABLE ON SCHEMA `workspace`.`semabridge_dev` TO `nimish@example.com`;"
    ]


def test_audit_owner_and_all_privileges_count() -> None:
    fake = (FakeDatabricks().on("GET", "/api/2.0/preview/scim/v2/Me", ME)
            .on("GET", f"{UC}/catalogs/workspace", ok({"owner": "nimish@example.com"}))
            .on("GET", f"{UC}/schemas/workspace.semabridge_dev", ok({"owner": "someone"}))
            .on("GET", f"{UC}/effective-permissions/catalog/workspace", _effective())
            .on("GET", f"{UC}/effective-permissions/schema/workspace.semabridge_dev",
                _effective(("nimish@example.com", ["ALL_PRIVILEGES"]))))
    assert _audit(fake)["status"] == "ok"


def test_audit_missing_schema() -> None:
    fake = (FakeDatabricks().on("GET", "/api/2.0/preview/scim/v2/Me", ME)
            .on("GET", f"{UC}/catalogs/workspace", ok({"owner": "nimish@example.com"}))
            .on("GET", f"{UC}/effective-permissions/catalog/workspace", _effective()))
    report = _audit(fake)
    assert report["status"] == "missing"
    assert report["remediation_sql"] == ["CREATE SCHEMA IF NOT EXISTS `workspace`.`semabridge_dev`;"]


# --- API service wiring ------------------------------------------------------------------------


def test_service_connection_test_uses_the_non_waking_check(monkeypatch) -> None:
    import semabridge.api.services.connection_domain_service as service
    from semabridge.repository.credential_manager import CredentialManager
    from semabridge.target_backends.databricks import connection

    # The Settings connection test is databricks_connection_service.
    # run_databricks_connection_test (REST metadata only, never starts the
    # warehouse, lists catalogs/schemas for the wizard); it runs first.
    from semabridge.api.services import databricks_connection_service as dcs

    seen = []
    monkeypatch.setattr(dcs, "run_databricks_connection_test",
                        lambda user_id=0: seen.append(user_id) or {"status": "success", "message": "ok"})
    assert asyncio.run(service.test_connection("databricks")) == {"status": "success", "message": "ok"}
    assert seen == [0]
    del CredentialManager, connection


def test_service_permission_audit(monkeypatch) -> None:
    import semabridge.api.services.connection_domain_service as service
    from semabridge.repository.credential_manager import CredentialManager
    from semabridge.target_backends.databricks import connection

    monkeypatch.setattr(CredentialManager, "inject_credentials_to_env", lambda self, s, user_id=0: 1)
    monkeypatch.setattr(service, "_databricks_connection_info", lambda: INFO)
    monkeypatch.setattr(connection, "audit_databricks_permissions",
                        lambda info, catalog, schema: {"status": "ok", "catalog": catalog, "schema": schema})
    result = asyncio.run(service.audit_databricks_permissions(catalog="workspace", schema="semabridge_dev"))
    assert result == {"status": "ok", "catalog": "workspace", "schema": "semabridge_dev"}


def test_server_errors_resubmit_reads_but_never_writes() -> None:
    """3.8b review: after 502/503/504 a write may already have run -- it is not
    resubmitted (the deploy treats the error as an uncertain outcome). 429 means
    the request was rejected, so a write may be retried."""
    from semabridge.target_backends.databricks.guard import WRITE, DatabricksSqlGuard, WritePlan

    write_sql = "CREATE VIEW `workspace`.`semabridge_dev`.`v` WITH METRICS LANGUAGE YAML AS $$\nversion: 1.1\n$$"
    guard = DatabricksSqlGuard(WRITE, WritePlan.of(views=["workspace.semabridge_dev.v"]))
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements", HttpResponse(status=503, body={"message": "x"}),
                               succeeded())
    with pytest.raises(DatabricksSqlError) as info:
        client(fake, guard=guard).execute(write_sql)
    assert info.value.http_status == 503 and len(fake.calls) == 1
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements", HttpResponse(status=503, body={"message": "x"}),
                               succeeded())
    assert client(fake).execute("SELECT 1").scalar() == 1 and len(fake.calls) == 2      # reads still retried
    fake = FakeDatabricks().on("POST", "/api/2.0/sql/statements",
                               HttpResponse(status=429, body={"message": "slow"}, retry_after=0.1), succeeded())
    client(fake, guard=guard).execute(write_sql)
    assert len(fake.calls) == 2
