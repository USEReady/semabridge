"""Offline end-to-end regression tests for safe Databricks deploy path (Requirements 1-6).

Uses a fake Databricks SQL Statements API attached to ``publisher.session`` so that
every SQL statement runs through the real ``execute_statements`` choke point and
the real ``validate_databricks_sql`` SQL guard. Never touches a real workspace.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

import pytest

from semabridge.connectors.databricks_publisher import (
    DatabricksPublishError,
    DatabricksPublisher,
    SEMABRIDGE_VIEW_COMMENT,
    databricks_object_name,
)
from semabridge.connectors.sql_guard import (
    SQLOwnershipSecurityError,
    SQLSecurityGuardError,
)
from semabridge.connectors.staged_deploy import persist_deploy_events
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import (
    AggregationType,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)


def _cfg(catalog: str = "main", schema: str = "analytics") -> DatabricksConfig:
    return DatabricksConfig(
        host="https://fake-workspace.cloud.databricks.com",
        token="fake-token",
        warehouse_id="wh-1234",
        catalog=catalog,
        schema_name=schema,
    )


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeStatementsSession:
    """Fake ``requests.Session`` for Databricks ``/api/2.0/sql/statements``."""

    def __init__(
        self,
        *,
        existing_schemas: set[str] | None = None,
        existing_tables: dict[str, list[str]] | None = None,
        describe_objects: dict[str, list[list[str]]] | None = None,
        live_grants: dict[str, list[tuple[str, str]]] | None = None,
        drop_grants_on_replace: bool = False,
        fail_on: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self.executed: list[str] = []
        self.existing_schemas = {s.lower() for s in (existing_schemas if existing_schemas is not None else {"main.analytics"})}
        # schema_fq (lower) -> list of table names
        self.existing_tables = {
            k.lower(): list(v) for k, v in (existing_tables or {"main.analytics": ["Sales", "Customer"]}).items()
        }
        # fqn (lower, unquoted) -> DESCRIBE EXTENDED data_array rows
        self.describe_objects = {k.lower(): v for k, v in (describe_objects or {}).items()}
        # fqn (lower, unquoted) -> list of (principal, action_type)
        self.live_grants: dict[str, list[tuple[str, str]]] = {
            k.lower(): list(v) for k, v in (live_grants or {}).items()
        }
        self.drop_grants_on_replace = drop_grants_on_replace
        self.fail_on = fail_on

    def post(self, url: str, headers: Any = None, json: dict[str, Any] | None = None, timeout: int = 60) -> _FakeResponse:
        _ = url, headers, timeout
        sql = str((json or {}).get("statement") or "").strip()
        self.executed.append(sql)

        if self.fail_on is not None:
            err = self.fail_on(sql)
            if err:
                return _FakeResponse({
                    "status": {"state": "FAILED", "error": {"error_code": "BAD_REQUEST", "message": err}},
                })

        upper = sql.upper()

        # SHOW SCHEMAS IN `cat` LIKE 'sch'
        m_sch = re.match(r"SHOW\s+SCHEMAS\s+IN\s+`([^`]+)`\s+LIKE\s+'([^']+)'", sql, re.IGNORECASE)
        if m_sch:
            key = f"{m_sch.group(1)}.{m_sch.group(2)}".lower()
            rows = [[m_sch.group(2)]] if key in self.existing_schemas else []
            return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": rows}})

        # SHOW TABLES IN `cat`.`sch` LIKE 'tbl'
        m_tbl_like = re.match(r"SHOW\s+TABLES\s+IN\s+`([^`]+)`\.`([^`]+)`\s+LIKE\s+'([^']+)'", sql, re.IGNORECASE)
        if m_tbl_like:
            cat, sch, tbl = m_tbl_like.group(1), m_tbl_like.group(2), m_tbl_like.group(3)
            fq = f"{cat}.{sch}.{tbl}".lower()
            if fq in self.describe_objects:
                return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": [[sch, tbl, "false"]]}})
            return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": []}})

        # SHOW TABLES IN `cat`.`sch`
        m_tbls = re.match(r"SHOW\s+TABLES\s+IN\s+`([^`]+)`\.`([^`]+)`$", sql, re.IGNORECASE)
        if m_tbls:
            cat, sch = m_tbls.group(1), m_tbls.group(2)
            key = f"{cat}.{sch}".lower()
            names = self.existing_tables.get(key, [])
            rows = [[sch, n, "false"] for n in names]
            return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": rows}})

        # SHOW COLUMNS IN `cat`.`sch`.`tbl`
        if upper.startswith("SHOW COLUMNS"):
            return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": []}})

        # DESCRIBE [TABLE] EXTENDED `cat`.`sch`.`obj`
        m_desc = re.match(r"DESCRIBE\s+(?:TABLE\s+)?EXTENDED\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)", sql, re.IGNORECASE)
        if m_desc:
            fq = m_desc.group(1).replace("`", "").lower()
            rows = self.describe_objects.get(fq, [["Type", "VIEW", ""]])
            return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": rows}})

        # SHOW GRANTS ON VIEW `cat`.`sch`.`obj`
        m_grants = re.match(r"SHOW\s+GRANTS\s+ON\s+VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)", sql, re.IGNORECASE)
        if m_grants:
            fq = m_grants.group(1).replace("`", "").lower()
            if fq not in self.live_grants:
                return _FakeResponse({
                    "status": {
                        "state": "FAILED",
                        "error": {"error_code": "TABLE_OR_VIEW_NOT_FOUND", "message": f"View {fq} not found"},
                    }
                })
            rows = [[p, act, "VIEW", fq] for p, act in self.live_grants[fq]]
            return _FakeResponse({
                "status": {"state": "SUCCEEDED"},
                "manifest": {
                    "schema": {
                        "columns": [
                            {"name": "Principal"},
                            {"name": "ActionType"},
                            {"name": "ObjectType"},
                            {"name": "ObjectKey"},
                        ]
                    }
                },
                "result": {"data_array": rows},
            })

        # CREATE OR REPLACE VIEW
        m_create_v = re.match(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)", sql, re.IGNORECASE)
        if m_create_v:
            fq = m_create_v.group(1).replace("`", "").lower()
            if self.drop_grants_on_replace and "__sbstg_" not in fq and fq in self.live_grants:
                self.live_grants[fq] = []
            return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": []}})

        # GRANT <priv> ON VIEW <fq> TO `<principal>`
        m_grant = re.match(
            r"GRANT\s+([A-Za-z0-9_\s]+?)\s+ON\s+VIEW\s+(`[^`]+`\.`[^`]+`\.`[^`]+`)\s+TO\s+`?([^`]+)`?",
            sql,
            re.IGNORECASE,
        )
        if m_grant:
            priv = m_grant.group(1).strip().upper()
            fq = m_grant.group(2).replace("`", "").lower()
            princ = m_grant.group(3).strip()
            self.live_grants.setdefault(fq, []).append((princ, priv))
            return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": []}})

        return _FakeResponse({"status": {"state": "SUCCEEDED"}, "result": {"data_array": []}})


def _two_fact_model() -> SMLModel:
    return SMLModel(
        unique_name="RetailModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="revenue", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="Inventory",
                columns=[
                    SMLColumn(unique_name="sku_id", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="qty_on_hand", data_type=DataType.INTEGER),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Revenue",
                dataset="Sales",
                source_column="revenue",
                aggregation=AggregationType.SUM,
            ),
            SMLMetric(
                unique_name="Total_Qty",
                dataset="Inventory",
                source_column="qty_on_hand",
                aggregation=AggregationType.SUM,
            ),
        ],
    )


def _star_model() -> SMLModel:
    return SMLModel(
        unique_name="StarModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="revenue", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="Customer",
                columns=[
                    SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="region", data_type=DataType.STRING),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Revenue",
                dataset="Sales",
                source_column="revenue",
                aggregation=AggregationType.SUM,
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="rel_sales_customer",
                from_dataset="Sales",
                from_columns=["customer_id"],
                to_dataset="Customer",
                to_columns=["customer_id"],
            )
        ],
    )


# ── Requirement 1: NEVER drop or create real tables ──────────────────────────


def test_existing_table_at_target_name_is_never_dropped_and_other_views_continue():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
            enable_destructive_sync_operations=True,  # even if True, NEVER drop tables!
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    fake = FakeStatementsSession(
        existing_tables={"main.analytics": ["Sales", "Inventory"]},
        describe_objects={
            "main.analytics.mv_retailmodel_sales": [
                ["Type", "MANAGED", ""],
                ["Provider", "delta", ""],
            ]
        },
    )
    publisher.session = fake

    publisher.publish(_two_fact_model())

    # Zero DROP TABLE or CREATE TABLE statements
    assert not any("DROP TABLE" in s.upper() for s in fake.executed)
    assert not any("CREATE TABLE" in s.upper() for s in fake.executed)

    # Sales view was blocked with the exact clear error
    blocked_events = [e for e in publisher.deploy_events if e["action"] == "blocked"]
    assert len(blocked_events) == 1
    assert "is an existing table; SemaBridge never drops tables. Choose another view name" in blocked_events[0]["detail"]

    # Inventory view still succeeded!
    promoted = [e for e in publisher.deploy_events if e["action"] == "promoted"]
    assert any("mv_RetailModel_Inventory" in e["object_name"] for e in promoted)
    assert not any("mv_RetailModel_Sales" in e["object_name"] for e in promoted)


def test_plain_view_to_metric_view_upgrade_drops_only_verified_semabridge_view():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    fake = FakeStatementsSession(
        existing_tables={"main.analytics": ["Sales", "Inventory"]},
        describe_objects={
            # Sales is a SemaBridge-owned plain VIEW -> allowed to drop & upgrade
            "main.analytics.mv_retailmodel_sales": [
                ["Type", "VIEW", ""],
                ["Comment", SEMABRIDGE_VIEW_COMMENT, ""],
                ["Table Properties", "[semabridge.managed=true]", ""],
            ],
            # Inventory is a user-created plain VIEW (no SemaBridge marker) -> MUST NOT drop
            "main.analytics.mv_retailmodel_inventory": [
                ["Type", "VIEW", ""],
                ["Comment", "Hand-crafted BI view", ""],
            ],
        },
    )
    publisher.session = fake

    publisher.publish(_two_fact_model())

    # Sales plain VIEW was dropped AFTER staging validation and upgraded
    drop_views = [s for s in fake.executed if s.upper().startswith("DROP VIEW")]
    assert any("`main`.`analytics`.`mv_RetailModel_Sales`" in s and "__sbstg_" not in s for s in drop_views)
    assert not any("`main`.`analytics`.`mv_RetailModel_Inventory`" in s for s in drop_views)

    # Inventory was blocked
    blocked = [e for e in publisher.deploy_events if e["action"] == "blocked"]
    assert any("mv_RetailModel_Inventory" in e["object_name"] and "not managed by SemaBridge" in e["detail"] for e in blocked)


def test_sql_views_include_semabridge_ownership_marker():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="sql_view",
            measure_view_mode="combined",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    stmts, created, _, _ = publisher.generate_measure_view_statements(
        _two_fact_model(), view_type_override="sql_view"
    )
    assert created == 2
    for stmt in stmts:
        assert f"COMMENT '{SEMABRIDGE_VIEW_COMMENT}'" in stmt
        assert "TBLPROPERTIES ('semabridge.managed' = 'true')" in stmt


def test_missing_source_table_skips_affected_view_without_creating_table():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    # Only Sales exists; Inventory is missing!
    fake = FakeStatementsSession(existing_tables={"main.analytics": ["Sales"]})
    publisher.session = fake

    publisher.publish(_two_fact_model())

    assert not any("CREATE TABLE" in s.upper() for s in fake.executed)
    skipped_events = [e for e in publisher.deploy_events if e["action"] == "skipped"]
    assert any("mv_RetailModel_Inventory" in e["object_name"] and "Missing source/join table" in e["detail"] for e in skipped_events)
    promoted = [e for e in publisher.deploy_events if e["action"] == "promoted"]
    assert any("mv_RetailModel_Sales" in e["object_name"] for e in promoted)


def test_target_schema_missing_fails_by_default_and_creates_only_when_enabled():
    behavior_default = ConnectorBehavior(
        databricks=DatabricksBehavior(measure_view_type="metric_view", measure_view_mode="combined")
    )
    pub_default = DatabricksPublisher(_cfg(), behavior=behavior_default)
    fake_missing = FakeStatementsSession(existing_schemas=set(), existing_tables={"main.analytics": ["Sales"]})
    pub_default.session = fake_missing

    with pytest.raises(DatabricksPublishError, match="does not exist"):
        pub_default.publish(_two_fact_model())
    assert not any("CREATE SCHEMA" in s.upper() for s in fake_missing.executed)

    # With create_schema_if_not_exists=True -> CREATE SCHEMA IF NOT EXISTS is allowed for target schema
    behavior_allow = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
            create_schema_if_not_exists=True,
        )
    )
    pub_allow = DatabricksPublisher(_cfg(), behavior=behavior_allow)
    fake_allow = FakeStatementsSession(existing_schemas=set(), existing_tables={"main.analytics": ["Sales", "Inventory"]})
    pub_allow.session = fake_allow

    pub_allow.publish(_two_fact_model())
    assert any("CREATE SCHEMA IF NOT EXISTS `main`.`analytics`" in s for s in fake_allow.executed)


# ── Requirement 2: Metadata table off by default & scoped to _semabridge_metadata ─


def test_metadata_table_off_by_default_and_scoped_when_enabled():
    assert DatabricksBehavior().create_metadata_table is False

    # Default publish executes zero CREATE TABLE / INSERT INTO
    pub_default = DatabricksPublisher(
        _cfg(),
        behavior=ConnectorBehavior(databricks=DatabricksBehavior(measure_view_type="metric_view", measure_view_mode="combined")),
    )
    fake_default = FakeStatementsSession(existing_tables={"main.analytics": ["Sales", "Inventory"]})
    pub_default.session = fake_default
    pub_default.publish(_two_fact_model())

    assert not any("CREATE OR REPLACE TABLE" in s.upper() or "CREATE TABLE" in s.upper() for s in fake_default.executed)
    assert not any("INSERT INTO" in s.upper() for s in fake_default.executed)

    # Enabled -> writes ONLY to _semabridge_metadata table
    pub_meta = DatabricksPublisher(
        _cfg(),
        behavior=ConnectorBehavior(
            databricks=DatabricksBehavior(
                measure_view_type="metric_view",
                measure_view_mode="combined",
                create_metadata_table=True,
            )
        ),
    )
    fake_meta = FakeStatementsSession(existing_tables={"main.analytics": ["Sales", "Inventory"]})
    pub_meta.session = fake_meta
    pub_meta.publish(_two_fact_model())

    table_writes = [s for s in fake_meta.executed if "CREATE OR REPLACE TABLE" in s.upper() or s.upper().startswith("INSERT INTO")]
    assert len(table_writes) >= 2
    for stmt in table_writes:
        assert "_semabridge_metadata" in stmt
    assert not any("DROP TABLE" in s.upper() for s in fake_meta.executed)


# ── Requirement 3: SQL guard choke point in execute_statements ───────────────


def test_execute_statements_choke_point_blocks_dangerous_sql_before_http():
    publisher = DatabricksPublisher(_cfg())
    fake = FakeStatementsSession()
    publisher.session = fake

    for bad_sql in [
        "DROP TABLE IF EXISTS `main`.`analytics`.`Sales`",
        "CREATE TABLE IF NOT EXISTS `main`.`analytics`.`Sales` (id INT) USING DELTA",
        "INSERT INTO `main`.`analytics`.`Sales` VALUES (1)",
        "UPDATE `main`.`analytics`.`Sales` SET revenue = 0",
        "DELETE FROM `main`.`analytics`.`Sales`",
        "ALTER TABLE `main`.`analytics`.`Sales` ADD COLUMNS (x STRING)",
        "TRUNCATE TABLE `main`.`analytics`.`Sales`",
        "COPY INTO `main`.`analytics`.`Sales` FROM '/tmp/x'",
        "CALL some_proc()",
    ]:
        with pytest.raises(SQLSecurityGuardError):
            publisher.execute_statements([bad_sql])

    with pytest.raises(SQLOwnershipSecurityError):
        publisher.execute_statements(["CREATE OR REPLACE VIEW `main`.`analytics`.`unregistered_view` AS SELECT 1"])

    # Nothing ever reached HTTP!
    assert fake.executed == []


# ── Requirement 4: Staged deploy, grant preservation, cleanup, deploy_events ─


def test_staged_deploy_validates_preserves_grants_cleans_up_and_persists_events():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    live_key = "main.analytics.mv_starmodel_sales"
    fake = FakeStatementsSession(
        existing_tables={"main.analytics": ["Sales", "Customer"]},
        live_grants={live_key: [("analysts", "SELECT"), ("finance_team", "SELECT")]},
        drop_grants_on_replace=True,  # simulate grants lost on replace
    )
    publisher.session = fake

    publisher.publish(_star_model())

    # 1. Staging view created with __sbstg_<token>
    stage_creates = [s for s in fake.executed if "CREATE OR REPLACE VIEW" in s and "__sbstg_" in s]
    assert len(stage_creates) == 1

    # 2. Validation ran: DESCRIBE TABLE EXTENDED + SELECT MEASURE(...) LIMIT 0
    #    (never `SELECT *` on a METRIC_VIEW — Databricks rejects it with METRIC_VIEW_MISSING_MEASURE_FUNCTION)
    assert any(s.startswith("DESCRIBE TABLE EXTENDED") and "__sbstg_" in s for s in fake.executed)
    assert not any(s.startswith("SELECT * FROM") and "__sbstg_" in s for s in fake.executed)
    assert any(s.startswith("SELECT MEASURE(") and "__sbstg_" in s and "LIMIT 0" in s for s in fake.executed)

    # 3. Grants captured and missing grants re-applied after live promote
    grant_stmts = [s for s in fake.executed if s.startswith("GRANT SELECT ON VIEW `main`.`analytics`.`mv_StarModel_Sales`")]
    assert len(grant_stmts) == 2
    assert any("`analysts`" in g for g in grant_stmts)
    assert any("`finance_team`" in g for g in grant_stmts)

    # 4. Staging view dropped in cleanup
    assert any(s.startswith("DROP VIEW IF EXISTS") and "__sbstg_" in s for s in fake.executed)

    # 5. Deploy events recorded & persistable to run_deploy_events
    from semabridge.repository.orm.models import Base
    from semabridge.repository.orm.session_factory import db_manager

    Base.metadata.create_all(bind=db_manager.get_engine())
    actions = [e["action"] for e in publisher.deploy_events]
    for expected_action in ("staged", "validated", "grants_captured", "promoted", "grants_restored", "cleaned"):
        assert expected_action in actions
    persisted = persist_deploy_events(
        publisher.deploy_events,
        run_id="run-dbx-staged-1",
        project_id="proj-dbx-1",
        model="StarModel",
    )
    assert persisted == len(publisher.deploy_events)


def test_staged_deploy_failure_leaves_live_view_untouched_and_drops_staging():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    def _fail_measure_check(sql: str) -> Optional[str]:
        if sql.startswith("SELECT MEASURE("):
            return "MEASURE evaluation failed on staging view"
        return None

    fake = FakeStatementsSession(
        existing_tables={"main.analytics": ["Sales", "Customer"]},
        fail_on=_fail_measure_check,
    )
    publisher.session = fake

    publisher.publish(_star_model())

    # Staging was created AND cleaned up
    assert any("CREATE OR REPLACE VIEW" in s and "__sbstg_" in s for s in fake.executed)
    assert any("DROP VIEW IF EXISTS" in s and "__sbstg_" in s for s in fake.executed)

    # Live view was NEVER created/replaced!
    live_replaces = [
        s for s in fake.executed
        if "CREATE OR REPLACE VIEW `main`.`analytics`.`mv_StarModel_Sales`" in s and "__sbstg_" not in s
    ]
    assert live_replaces == []

    actions = [e["action"] for e in publisher.deploy_events]
    assert "stage_failed" in actions
    assert "cleaned" in actions
    assert "promoted" not in actions


def test_staged_deploy_can_be_disabled_via_env(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_STAGED_DEPLOY", "false")
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    fake = FakeStatementsSession(existing_tables={"main.analytics": ["Sales", "Customer"]})
    publisher.session = fake

    publisher.publish(_star_model())

    assert not any("__sbstg_" in s for s in fake.executed)
    assert any("CREATE OR REPLACE VIEW `main`.`analytics`.`mv_StarModel_Sales`" in s for s in fake.executed)


# ── Requirement 5: plan() shape, offline zero-SQL, and plan == deploy ────────


def test_plan_offline_executes_zero_sql_and_matches_exact_shape():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
            enable_cross_table_joins=True,
            enable_metric_view_joins=True,
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    fake = FakeStatementsSession()
    publisher.session = fake

    plan = publisher.plan(_star_model(), lookup=False)

    # Zero SQL executed when lookup=False
    assert fake.executed == []

    # Exact top-level keys
    assert set(plan.keys()) == {"target", "views", "missing_tables", "blocked", "warnings"}
    assert plan["target"] == "main.analytics"
    assert len(plan["views"]) == 1

    v = plan["views"][0]
    assert set(v.keys()) == {
        "name",
        "kind",
        "fact",
        "source_table",
        "joins",
        "dimensions",
        "measures",
        "skipped",
        "reason",
    }
    assert v["name"] == "main.analytics.mv_StarModel_Sales"
    assert v["kind"] == "metric_view"
    assert v["fact"] == "Sales"
    assert v["source_table"] == "main.analytics.Sales"
    assert v["skipped"] is False
    assert v["reason"] == ""
    assert len(v["measures"]) == 1
    assert set(v["measures"][0].keys()) == {"name", "expr", "status", "reason"}
    assert v["measures"][0]["status"] == "ok"


def test_plan_lookup_true_is_read_only_and_detects_missing_and_blocked():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    fake = FakeStatementsSession(
        existing_tables={"main.analytics": ["Sales"]},  # Inventory is missing
        describe_objects={
            "main.analytics.mv_retailmodel_sales": [["Type", "MANAGED", ""]],
        },
    )
    publisher.session = fake

    plan = publisher.plan(_two_fact_model(), lookup=True)

    # Every statement executed during lookup=True must be read-only
    for stmt in fake.executed:
        assert stmt.upper().startswith(("SHOW ", "DESCRIBE ", "DESC ", "SELECT "))

    assert "main.analytics.Inventory" in plan["missing_tables"]
    assert any("is an existing table; SemaBridge never drops tables" in b for b in plan["blocked"])
    assert all(v["skipped"] is True for v in plan["views"])


def test_plan_equals_deploy_same_views_and_measures():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
        )
    )
    model = _two_fact_model()
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)

    plan = publisher.plan(model, lookup=False)

    fake = FakeStatementsSession(existing_tables={"main.analytics": ["Sales", "Inventory"]})
    publisher.session = fake
    publisher.publish(model)

    # Extract promoted live view names and measures from executed SQL
    live_creates = [
        s for s in fake.executed
        if s.startswith("CREATE OR REPLACE VIEW") and "__sbstg_" not in s
    ]
    deployed_names = sorted(
        re.search(r"VIEW\s+`([^`]+)`\.`([^`]+)`\.`([^`]+)`", s).expand(r"\1.\2.\3")
        for s in live_creates
    )
    planned_names = sorted(v["name"] for v in plan["views"] if not v["skipped"])
    assert deployed_names == planned_names

    # Same measures in each deployed view as in plan()
    import yaml

    for v in plan["views"]:
        matching_sql = next(s for s in live_creates if v["name"].rsplit(".", 1)[-1] in s)
        yaml_match = re.search(r"WITH METRICS LANGUAGE YAML AS \$\$\n(.*?)\n\$\$", matching_sql, re.S)
        assert yaml_match is not None
        deployed_doc = yaml.safe_load(yaml_match.group(1))
        deployed_measure_names = [m["name"] for m in deployed_doc.get("measures", [])]
        planned_measure_names = [m["name"] for m in v["measures"]]
        assert deployed_measure_names == planned_measure_names


# ── Requirement 6: databricks_object_name helper ─────────────────────────────


def test_databricks_object_name_helper():
    assert databricks_object_name("main", "analytics", "mv_sales") == "`main`.`analytics`.`mv_sales`"
    assert databricks_object_name("main", "analytics", "mv_sales", quoted=False) == "main.analytics.mv_sales"


def test_configured_semabridge_public_source_rebinds_to_target_catalog_schema():
    """Legacy behavior.yaml source_catalog='semabridge' / source_schema='public' must
    rebind to the real Databricks target catalog.schema when semabridge.public does not exist."""
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            source_catalog="semabridge",
            source_schema="public",
            measure_view_type="metric_view",
            measure_view_mode="combined",
        )
    )
    cfg = DatabricksConfig(
        host="test.cloud.databricks.com",
        token="dapi-test-token",
        warehouse_id="wh-1",
        catalog="workspace",
        schema_name="default",
    )
    publisher = DatabricksPublisher(cfg, behavior=behavior)
    ds = SMLDataset(unique_name="Events", label="Events", columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)])

    # Offline plan mode rebinds legacy semabridge.public -> workspace.default
    publisher._offline_plan_mode = True
    assert publisher._resolve_source_table(ds) == "`workspace`.`default`.`Events`"

    # Live mode: semabridge.public does not exist, workspace.default.Events exists -> rebinds
    publisher._offline_plan_mode = False
    publisher.session = FakeStatementsSession(existing_tables={"workspace.default": ["Events"]})
    assert publisher._resolve_source_table(ds) == "`workspace`.`default`.`Events`"

    # Live mode: neither exists -> still reports workspace.default.Events (not semabridge.public.Events)
    publisher._schema_cache.clear()
    publisher._table_exists_cache.clear()
    publisher.session = FakeStatementsSession(existing_tables={"workspace.default": []})
    assert publisher._resolve_source_table(ds) == "`workspace`.`default`.`Events`"


def test_zero_views_deployed_raises_deployment_error():
    """When 0/N views are deployed (all skipped or failed), engine deployment must fail
    instead of reporting a misleading green SUCCESS."""
    from types import SimpleNamespace
    from semabridge.core.engine.deployment.databricks import (
        DeploymentError,
        _raise_if_databricks_fallback_failed,
    )

    ctx = SimpleNamespace(project_id="proj-dbt4")

    # 0/1 deployed, 1 skipped due to missing source tables -> MUST raise DeploymentError
    with pytest.raises(DeploymentError, match="No Databricks views were deployed.*0/1 deployed, 1 skipped"):
        _raise_if_databricks_fallback_failed(
            None,
            ctx,
            {
                "sql_fallback_state": "NOT_TRIGGERED",
                "deploy_summary": {
                    "planned_views": 1,
                    "deployable_views": 0,
                    "views_deployed": 0,
                    "views_failed": 0,
                    "views_skipped": 1,
                    "skipped_reasons": ["`workspace`.`default`.`mv_1`: Missing source/join table(s): workspace.default.Events"],
                    "failed_reasons": [],
                    "missing_source_tables": ["`workspace`.`default`.`Events`"],
                },
            },
        )

    # Partial success (1/2 deployed, 1 skipped) -> allowed
    _raise_if_databricks_fallback_failed(
        None,
        ctx,
        {
            "sql_fallback_state": "NOT_TRIGGERED",
            "deploy_summary": {
                "planned_views": 2,
                "deployable_views": 1,
                "views_deployed": 1,
                "views_failed": 0,
                "views_skipped": 1,
                "skipped_reasons": ["`workspace`.`default`.`mv_2`: Missing source/join table(s)"],
                "failed_reasons": [],
                "missing_source_tables": [],
            },
        },
    )


def test_schema_first_stub_deploys_metric_view_without_physical_tables():
    """With on_missing_source='stub', Databricks deploys the Metric View using an inline SQL
    SELECT CAST(NULL AS ...) WHERE 1=0 source when physical tables do not exist yet,
    with zero CREATE TABLE statements, and binds to the real tables on the next sync once they exist."""
    import yaml as _yaml
    from semabridge.connectors.sql_guard import DatabricksSQLGuardPolicy, validate_databricks_sql

    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            measure_view_mode="combined",
            model_artifact_mode="per_model",
            enable_cross_table_joins=True,
            enable_metric_view_joins=True,
            on_missing_source="stub",
        )
    )
    cfg = DatabricksConfig(
        host="test.cloud.databricks.com",
        token="dapi-test-token",
        warehouse_id="wh-1",
        catalog="workspace",
        schema_name="default",
    )
    model = _star_model()

    # 1) Zero physical tables exist in workspace.default -> deploys inline SQL stub!
    pub_stub = DatabricksPublisher(cfg, behavior=behavior)
    fake_empty = FakeStatementsSession(
        existing_schemas={"workspace.default"},
        existing_tables={"workspace.default": []},
    )
    pub_stub.session = fake_empty

    pub_stub.publish(model)

    assert not any("CREATE TABLE" in s.upper() for s in fake_empty.executed)
    assert not any("DROP TABLE" in s.upper() for s in fake_empty.executed)

    live_creates = [
        s for s in fake_empty.executed
        if s.startswith("CREATE OR REPLACE VIEW") and "__sbstg_" not in s
    ]
    assert len(live_creates) == 1
    ddl = live_creates[0]

    # Guard must allow the rewritten inline-SQL metric view DDL
    validate_databricks_sql(
        ddl,
        DatabricksSQLGuardPolicy(
            target_catalog="workspace",
            target_schema="default",
            allowed_views={"workspace.default.starmodel_metric_view"},
        ),
    )

    yaml_match = re.search(r"WITH METRICS LANGUAGE YAML AS \$\$\n(.*?)\n\$\$", ddl, re.S)
    assert yaml_match is not None
    doc = _yaml.safe_load(yaml_match.group(1))
    assert str(doc["source"]).upper().startswith("SELECT ")
    assert "WHERE 1=0" in str(doc["source"]).upper()
    assert not doc.get("joins")
    assert "[Schema-First Stub" in str(doc.get("comment") or "")
    # No dimension expr may reference a dropped join alias (e.g. customers.region)
    for d in doc.get("dimensions") or []:
        assert "." not in str(d.get("expr") or "")
    # The stub is reported as structure only, never as a deployed view with
    # data. It does not trip the zero-views gate (it is reported as a warning).
    summary = pub_stub.get_last_publish_summary()["deploy_summary"]
    assert summary["views_deployed"] == 0
    assert summary["views_stubbed"] == 1
    assert summary["stubbed_views"][0]["view"].replace("`", "").lower() == "workspace.default.starmodel_metric_view"
    assert summary["stubbed_views"][0]["missing_source_tables"]
    assert summary["views_skipped"] == 0

    # 2) Once physical tables exist in workspace.default -> binds to real tables (no stub)
    pub_real = DatabricksPublisher(cfg, behavior=behavior)
    fake_real = FakeStatementsSession(
        existing_schemas={"workspace.default"},
        existing_tables={"workspace.default": ["Sales", "Customer"]},
    )
    pub_real.session = fake_real

    pub_real.publish(model)
    live_real = [
        s for s in fake_real.executed
        if s.startswith("CREATE OR REPLACE VIEW") and "__sbstg_" not in s
    ]
    assert len(live_real) == 1
    yaml_real = re.search(r"WITH METRICS LANGUAGE YAML AS \$\$\n(.*?)\n\$\$", live_real[0], re.S)
    doc_real = _yaml.safe_load(yaml_real.group(1))
    assert "WHERE 1=0" not in str(doc_real["source"]).upper()
    assert "Sales" in str(doc_real["source"])
