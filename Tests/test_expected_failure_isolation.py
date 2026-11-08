"""Expected-failure isolation for Snowflake semantic-view emission.

Product rule under test: anything Snowflake cannot accept becomes an
EXPECTED FAILURE isolated to that one item (metric / dimension /
relationship / table) with a specific reason; it never fails the whole view,
is never silently dropped, and is never reported as converted. All tests use
fake cursors -- no Snowflake connection.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.snowflake_emitter_parts.semantic_view_items import (
    SemanticViewDDL,
    escape_comment_literal,
    is_null_placeholder_expr,
    select_conflicting_relationships,
)
from semabridge.core.drop_ledger import classify_drop_reason
from semabridge.core.reconciliation import _extract_deployed_metric_names
from semabridge.utils.identifiers import IdentifierSanitizer
from Tests.test_metric_drop_ledger_coverage import _builder, _model
from Tests.test_snowflake_emitter_findings_regressions import _build_emitter_for_ddl_loop

DDL = """CREATE OR REPLACE SEMANTIC VIEW "DB"."SCH"."MODEL"
TABLES (
  ORDERS AS "DB"."SCH"."ORDERS" PRIMARY KEY ("ID"),
  CUSTOMERS AS "DB"."SCH"."CUSTOMERS" PRIMARY KEY ("ID")
)
RELATIONSHIPS (
  ORDERS_CUSTOMERS AS ORDERS ("CUSTOMER_ID") REFERENCES CUSTOMERS ("ID"),
  ORDERS_GHOST AS ORDERS ("GHOST_ID") REFERENCES CUSTOMERS ("ID")
)
DIMENSIONS (
  CUSTOMERS."REGION" AS CUSTOMERS."REGION",
  CUSTOMERS."SEGMENT" AS CUSTOMERS."SEGMENT",
  ORDERS."ORDER_DATE" AS ORDERS."ORDER_DATE"
)
METRICS (
  ORDERS."TOTAL" AS SUM(ORDERS."AMT"),
  PRIVATE ORDERS."HELPER_SUM" AS SUM(ORDERS."AMT"),
  ORDERS."BALANCE" NON ADDITIVE BY ( ORDERS."ORDER_DATE" ASC NULLS LAST ) AS SUM(ORDERS."AMT"),
  "DOUBLE_TOTAL" AS ORDERS."TOTAL" * 2,
  ORDERS."BAD" AS SUM(ORDERS."NO_SUCH_COL")
);"""

LIVE_COLUMNS = [
    ("SCH", "ORDERS", "ID"), ("SCH", "ORDERS", "CUSTOMER_ID"), ("SCH", "ORDERS", "AMT"),
    ("SCH", "ORDERS", "ORDER_DATE"),
    ("SCH", "CUSTOMERS", "ID"), ("SCH", "CUSTOMERS", "REGION"),
]


def _fake_info_schema_cursor(rows=LIVE_COLUMNS):
    cur = MagicMock()
    cur.fetchall.return_value = list(rows)
    return cur


def _ledger(emitter):
    return {(r["entity_kind"], r["entity_name"]): r for r in emitter.drop_ledger.to_json()}


# --- defect 1: static pre-deploy check isolates per item -----------------------
def test_static_check_isolates_each_unresolved_item_instead_of_failing():
    emitter = _build_emitter_for_ddl_loop()
    (out,) = emitter._validate_ddl_against_information_schema(_fake_info_schema_cursor(), [DDL])
    view = SemanticViewDDL(out)

    bad = view.find("METRICS", "BAD")
    assert bad.is_null_placeholder and "does not exist" in bad.comment
    assert view.find("DIMENSIONS", "SEGMENT") is None          # missing column -> dropped
    assert view.find("RELATIONSHIPS", "ORDERS_GHOST") is None  # missing FK column -> dropped
    # Everything resolvable is untouched.
    assert view.find("METRICS", "TOTAL").expr == 'SUM(ORDERS."AMT")'
    assert view.find("DIMENSIONS", "REGION") is not None
    assert view.find("RELATIONSHIPS", "ORDERS_CUSTOMERS") is not None

    led = _ledger(emitter)
    assert led[("metric", "BAD")]["reason"].startswith("placeholder: ")
    assert ("dimension", "CUSTOMERS.SEGMENT") in led
    assert ("relationship", "ORDERS_GHOST") in led
    assert all(not r["by_design"] for r in led.values())  # data issues are unexpected


# --- defect 2: bounded CREATE remediation ---------------------------------------
def _wire(emitter, monkeypatch, fail_with, ddl=DDL):
    conn = MagicMock()
    monkeypatch.setattr(emitter.connection_manager, "get_connection", lambda: (conn, True))
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_duplicate_mappings", lambda *a, **k: None)
    monkeypatch.setattr(emitter.schema_manager, "refresh_schema_cache", lambda: None)
    monkeypatch.setattr(emitter.schema_manager, "_ensure_schema_exists", lambda cur: None)
    monkeypatch.setattr(emitter.schema_manager, "_fetch_schema_metadata", lambda cur: {"DUMMY": set()})
    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls", lambda model: [ddl])
    # Isolate the CREATE loop from the post-create checks.
    monkeypatch.setattr(emitter, "_run_cortex_safety_validation", lambda cur, q, sql, p: sql)
    executed = []

    def fake_execute_sql(cursor, sql, context="", **kwargs):
        if context.startswith("DDL[0]"):
            executed.append(sql)
            error = fail_with(sql)
            if error:
                raise error

    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql)
    return executed


def _model_stub():
    from semabridge.sml.models import SMLModel
    return SMLModel(unique_name="M", label="M", datasets=[], metrics=[], relationships=[], dimensions=[])


def test_create_failure_on_a_relationship_is_isolated_and_retried(monkeypatch):
    emitter = _build_emitter_for_ddl_loop()
    executed = _wire(
        emitter, monkeypatch,
        lambda sql: RuntimeError("SQL compilation error: relationship 'ORDERS_GHOST' references a column that is not unique")
        if "ORDERS_GHOST AS" in sql else None,
    )
    assert emitter.deploy(_model_stub(), sync_mode="copy") is True
    assert len(executed) == 2
    assert "ORDERS_GHOST" not in executed[-1]
    assert ("relationship", "ORDERS_GHOST") in _ledger(emitter)


def test_create_remediation_is_capped(monkeypatch):
    emitter = _build_emitter_for_ddl_loop()
    executed = _wire(emitter, monkeypatch, lambda sql: RuntimeError("010220: Invalid metric 'ORDERS.TOTAL'"))
    assert emitter.deploy(_model_stub(), sync_mode="copy") is False
    # TOTAL nulled once (and DOUBLE_TOTAL with it), then no further progress -> stop.
    assert len(executed) == 2
    led = _ledger(emitter)
    assert ("metric", "TOTAL") in led and ("metric", "DOUBLE_TOTAL") in led
    assert "depends on [TOTAL]" in led[("metric", "DOUBLE_TOTAL")]["reason"]


# --- defect 3: post-create problems never fail a live view ------------------------
def test_post_create_dimension_and_yaml_failures_become_warnings():
    emitter = _build_emitter_for_ddl_loop()
    emitter.cortex_validation_results = []
    emitter.deployment_warnings = []
    emitter._dropped_metrics = []
    calls = []

    def fake_execute_sql(cursor, sql, context="", **kwargs):
        calls.append(context)
        if context == 'cortex_safety:dim:CUSTOMERS."REGION"':
            raise RuntimeError("dimension REGION cannot be resolved")
        if context.startswith("cortex_safety:verify_yaml"):
            raise RuntimeError("YAML verify failed")

    emitter.connection_manager._execute_sql = fake_execute_sql
    cur = MagicMock()
    cur.fetchone.return_value = ("yaml: x",)
    out = emitter._run_cortex_safety_validation(cur, '"DB"."SCH"."MODEL"', DDL, "SML")

    assert SemanticViewDDL(out).find("DIMENSIONS", "REGION") is None
    summary = emitter.cortex_validation_results[-1]
    assert summary["status"] == "deployed_with_warnings"
    assert any("YAML" in w for w in summary["warnings"])
    assert summary["dropped_dimensions"] == ["REGION"]
    assert ("dimension", "CUSTOMERS.REGION") in _ledger(emitter)
    # PRIVATE metrics and NULL placeholders are never queried directly.
    assert not any("HELPER_SUM" in c for c in calls)


# --- defect 4: semi-additive metric is never shipped as additive -------------------
def test_non_additive_metric_without_emitted_dimension_is_nulled_by_design():
    builder = _builder()
    builder.emitted_dimensions = {"SALESFACT": ["OTHER_DIM"]}
    metric = SimpleNamespace(
        unique_name="Closing Balance", dataset="SalesFact", expression="",
        sql_expression=None, source_column="Balance", aggregation=SimpleNamespace(value="sum"),
        non_additive_dimension=("SalesFact", "Snapshot Date"),
    )
    lines = builder.build_for_osi(
        _model([metric]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"BALANCE"}},
        alias_by_raw={}, all_physical_col_names={"BALANCE"}, emittable_metric_name_set=set(),
    )
    assert "CAST(NULL AS DOUBLE)" in lines[0] and "SUM(" not in lines[0]
    rec = builder.drop_ledger.to_json()[0]
    assert rec["by_design"] is True and "NON ADDITIVE BY" in rec["reason"]


# --- defects 5, 8: robust NULL detection; every metric syntax -----------------
def test_null_detection_and_all_metric_syntaxes():
    nulled = SnowflakeEmitter._null_metric_in_ddl(DDL, 'ORDERS."BALANCE"', "boom */ it's")
    live, dead = _extract_deployed_metric_names(nulled)
    assert dead == {"BALANCE"}
    assert {"TOTAL", "HELPER_SUM", "DOUBLE_TOTAL", "BAD"} <= live
    assert "/*" not in nulled and "COMMENT = 'rejected by Cortex validation: boom * / it''s'" in nulled
    for expr in [
        "CAST(NULL AS DOUBLE) /* rejected */",
        "cast( null as double ) COMMENT = 'x'",
        "CAST(NULL AS DOUBLE) WITH SYNONYMS = ('a') COMMENT = 'it''s'",
        "(CAST(NULL AS NUMBER(38,2)))",
    ]:
        assert is_null_placeholder_expr(expr), expr
    assert not is_null_placeholder_expr("COALESCE(CAST(NULL AS DOUBLE), SUM(X.\"A\"))")
    dims, mets = SnowflakeEmitter._extract_semantic_view_elements(DDL)
    assert 'ORDERS."BALANCE"' in mets and '"DOUBLE_TOTAL"' in mets
    assert 'ORDERS."HELPER_SUM"' not in mets  # PRIVATE cannot be queried


# --- defect 6: taxonomy ----------------------------------------------------------
def test_placeholder_reason_is_specific_and_classified():
    builder = _builder()
    by_design = SimpleNamespace(
        unique_name="Ranked", dataset="SalesFact", expression="RANKX(ALL(T), [X])", sql_expression=None,
        source_column=None, aggregation=None, sync_enabled=True,
        sync_failure_reason="BY_DESIGN_EXCLUDED: ranking over the whole table needs a window metric",
    )
    reason, flag = builder._placeholder_reason(by_design, None)
    assert flag is True and "ranking" in reason
    bug = SimpleNamespace(unique_name="X", expression="SUM(T[A])", sync_failure_reason="translator crashed: KeyError")
    reason, flag = builder._placeholder_reason(bug, None)
    assert flag is False and "KeyError" in reason
    assert classify_drop_reason("placeholder: rejected by Snowflake: invalid identifier 'A'") is False
    assert classify_drop_reason("Target column(s) ['X'] not declared as PRIMARY KEY or UNIQUE") is True


def test_relationship_to_non_key_is_dropped_by_design():
    schema = SimpleNamespace(_resolve_physical_column_name=lambda ds, c, **k: c.upper())
    sanitizer = SimpleNamespace(to_snowflake_relationship_name=lambda n: n.upper())
    builder = RelationshipsClauseBuilder(IdentifierSanitizer(), schema, sanitizer)
    rel = SimpleNamespace(unique_name="r", is_active=True, from_dataset="F", to_dataset="D",
                          from_columns=["k"], to_columns=["k"])
    lines = builder._build_relationships([rel], {"F": "F", "D": "D"}, {"F": object(), "D": object()},
                                         {}, {}, {}, is_osi=True)
    assert lines == []
    assert builder.drop_ledger.to_json()[0]["by_design"] is True


# --- defect 7: final-pass nulls are ledgered ---------------------------------------
def test_final_pass_nested_aggregate_null_is_ledgered():
    builder = _builder()
    builder._ddl_name_to_unique = {"NESTED": "Nested Metric"}
    builder._dataset_aliases = {}
    lines = builder._prune_unresolved_metric_lines(
        ['  T."NESTED" AS MAX(SUM(T."A"))'], {"NESTED"},
    )
    assert "CAST(NULL AS DOUBLE)" in lines[0]
    rec = builder.drop_ledger.to_json()[0]
    assert rec["entity_name"] == "Nested Metric" and rec["by_design"] is True


# --- defect 9: run report counts ---------------------------------------------------
def test_run_report_outcome_counts_match_live_ddl():
    from semabridge.api.services.run_report_service import _merge_metric_outcomes, _metric_outcome_summary

    ddl = SnowflakeEmitter._null_metric_in_ddl(DDL, 'ORDERS."BAD"', "x")
    dropped = [
        {"entity_kind": "metric", "entity_name": "BAD", "reason": "placeholder: rejected by Snowflake: invalid identifier 'X'",
         "detail": "raw error", "by_design": False, "stage": "ddl_deployment"},
        {"entity_kind": "metric", "entity_name": "Ranked", "reason": "placeholder: not applicable: rank function (RANKX)",
         "by_design": True, "stage": "dax_translation"},
    ]
    out = _merge_metric_outcomes([_metric_outcome_summary(dropped, ddl)])
    live, _dead = _extract_deployed_metric_names(ddl)
    assert out["converted"] == len(live)
    assert out["expected_count"] == 1 and out["unexpected_count"] == 1
    assert "raw error" in out["unexpected"][0]["reason"]
    assert out["expected_by_reason"][0]["items"] == ["Ranked"]


# --- defect 10: PRIVATE only for flagged helpers -------------------------------------
def test_user_metric_ending_in_sum_is_not_private():
    builder = _builder()
    metric = SimpleNamespace(
        unique_name="Revenue_SUM", dataset="SalesFact", expression="", sql_expression=None,
        source_column="Revenue", aggregation=SimpleNamespace(value="sum"),
    )
    lines = builder.build_for_osi(
        _model([metric]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"REVENUE"}},
        alias_by_raw={}, all_physical_col_names={"REVENUE"}, emittable_metric_name_set=set(),
    )
    assert not lines[0].strip().startswith("PRIVATE")


# --- defect 11: fan-out ------------------------------------------------------------
def test_fan_out_checks():
    b = MetricsClauseBuilder.__new__(MetricsClauseBuilder)
    rels = [SimpleNamespace(from_dataset="ORDERS", to_dataset="CUSTOMERS", from_columns=["CUSTOMER_ID"], to_columns=["ID"])]
    aliases = {"ORDERS": "ORDERS", "CUSTOMERS": "CUSTOMERS"}
    assert b._rewrite_foreign_table_columns_in_aggregates('COUNT(CUSTOMERS."ID")', "ORDERS", rels, aliases) \
        == 'COUNT(DISTINCT ORDERS."CUSTOMER_ID")'
    cols = {"ORDERS": {"ID", "CUSTOMER_ID", "AMT"}, "CUSTOMERS": {"ID", "CREDIT"}}
    reason = b._run_predeploy_checks("R", "ORDERS", 'SUM(CUSTOMERS."CREDIT")', None, cols, aliases, rels, {"R"})
    assert reason and "fan-out" in reason and classify_drop_reason(reason)
    assert b._run_predeploy_checks("R", "ORDERS", 'MAX(CUSTOMERS."CREDIT")', None, cols, aliases, rels, {"R"}) is None


# --- defect 12: cycles / multiple paths ----------------------------------------------
def test_relationship_cycles_and_multiple_paths_drop_lowest_priority():
    edges = [("SALES", "DATE"), ("SALES", "PRODUCT"), ("PRODUCT", "DATE"), ("DATE", "SALES")]
    dropped = select_conflicting_relationships(edges, [0, 0, 1, 1])
    assert set(dropped) == {2, 3}  # the inactive (priority 1) edges go
    assert "multiple relationship paths" in dropped[2] and "cycle" in dropped[3]


# --- defect 13: comment escaping -----------------------------------------------------
def test_comment_escaping():
    assert escape_comment_literal("a\\b it's */") == "a\\\\b it''s * /"
    assert MetricsClauseBuilder._clean_comment("C:\\path it's /* x */") == "C:\\\\path it''s / * x * /"
