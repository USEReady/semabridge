"""Offline unit tests for Step 6 Workspace Capability Probe, Step 7 Unity Catalog
Loose Table/Column Matcher, Dialect/Text Measure Translation, and Deploy Hardening.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
import yaml

from semabridge.connectors.databricks_measure_translation import DatabricksMeasureTranslator
from semabridge.connectors.databricks_probe import (
    clear_probe_cache,
    probe_databricks_capabilities,
)
from semabridge.connectors.databricks_publisher import (
    DatabricksPublishError,
    DatabricksPublisher,
)
from semabridge.connectors.databricks_table_matcher import match_databricks_tables
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.concurrency.error_classifier import ErrorClassifier
from semabridge.core.concurrency.models import RetryConfig
from semabridge.core.concurrency.retry_manager import RetryManager
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import (
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)


def _cfg(catalog: str = "workspace", schema: str = "default") -> DatabricksConfig:
    return DatabricksConfig(
        host="dbc-test.cloud.databricks.com",
        token="dummy-token",
        warehouse_id="wh-123",
        catalog=catalog,
        schema_name=schema,
    )


def _retry_mgr() -> RetryManager:
    return RetryManager(
        config=RetryConfig(max_retries=1, base_delay=0.001, max_delay=0.001),
        classifier=ErrorClassifier(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Step 6: Workspace Capability Probe
# ─────────────────────────────────────────────────────────────────────────────


class TestDatabricksWorkspaceProbe:
    def setup_method(self):
        clear_probe_cache()

    def teardown_method(self):
        clear_probe_cache()

    def test_probe_succeeds_and_detects_genie_ready_for_pro_and_serverless(self):
        executed: list[str] = []

        def _exec(sql: str):
            executed.append(sql)
            return []

        caps_pro = probe_databricks_capabilities(
            _exec,
            warehouse_info={"warehouse_type": "PRO", "state": "RUNNING", "id": "wh-pro"},
            host="dbc-a.cloud.databricks.com",
        )
        assert caps_pro.probed is True
        assert caps_pro.measure_references is True
        assert caps_pro.window_measures is True
        assert caps_pro.genie_ready is True
        assert caps_pro.warehouse_state == "RUNNING"
        assert caps_pro.probe_errors == []
        # Verify probe SQL never mutates user tables
        for sql in executed:
            upper = sql.upper()
            assert "DROP TABLE" not in upper
            assert "ALTER TABLE" not in upper
            assert "DELETE " not in upper
            assert "INSERT " not in upper
            assert "MERGE " not in upper

        caps_serverless = probe_databricks_capabilities(
            _exec,
            warehouse_info={"warehouse_type": "CLASSIC", "enable_serverless_compute": True, "id": "wh-sl"},
            host="dbc-a.cloud.databricks.com",
        )
        assert caps_serverless.genie_ready is True

        caps_classic = probe_databricks_capabilities(
            _exec,
            warehouse_info={"warehouse_type": "CLASSIC", "enable_serverless_compute": False, "id": "wh-cl"},
            host="dbc-a.cloud.databricks.com",
        )
        assert caps_classic.genie_ready is False

    def test_probe_tolerates_errors_gracefully_and_records_probe_errors(self):
        def _failing_exec(sql: str):
            if "window:" in sql:
                raise RuntimeError("UNSUPPORTED_FEATURE: window measures not available")
            return []

        caps = probe_databricks_capabilities(
            _failing_exec,
            warehouse_info={"warehouse_type": "SERVERLESS", "id": "wh-err"},
            host="dbc-err.cloud.databricks.com",
        )
        assert caps.probed is True
        assert caps.measure_references is True
        assert caps.window_measures is False
        assert caps.genie_ready is True
        assert len(caps.probe_errors) == 1
        assert "window_measures" in caps.probe_errors[0]

    def test_probe_caches_by_host_and_warehouse_id_and_clear_cache_resets(self):
        call_count = 0

        def _counting_exec(sql: str):
            nonlocal call_count
            call_count += 1
            return []

        probe_databricks_capabilities(
            _counting_exec,
            warehouse_info={"warehouse_type": "PRO", "id": "wh-1"},
            host="dbc-cache.cloud.databricks.com",
        )
        first_calls = call_count
        assert first_calls == 2

        # Second call with same (host, warehouse_id) hits cache -> 0 new SQL calls
        probe_databricks_capabilities(
            _counting_exec,
            warehouse_info={"warehouse_type": "PRO", "id": "wh-1"},
            host="dbc-cache.cloud.databricks.com",
        )
        assert call_count == first_calls

        # Clear cache -> executes probes again
        clear_probe_cache()
        probe_databricks_capabilities(
            _counting_exec,
            warehouse_info={"warehouse_type": "PRO", "id": "wh-1"},
            host="dbc-cache.cloud.databricks.com",
        )
        assert call_count == first_calls * 2

    def test_probe_skipped_when_allow_probe_false(self):
        called = False

        def _exec(sql: str):
            nonlocal called
            called = True
            return []

        caps = probe_databricks_capabilities(
            _exec,
            warehouse_info={"warehouse_type": "PRO"},
            allow_probe=False,
        )
        assert called is False
        assert caps.probed is False
        assert caps.genie_ready is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. Step 7: Unity Catalog Loose Table & Column Matcher
# ─────────────────────────────────────────────────────────────────────────────


class TestDatabricksTableMatcher:
    def test_saved_overrides_always_win(self):
        model = SMLModel(
            unique_name="SalesModel",
            datasets=[
                SMLDataset(
                    unique_name="Customer_Table",
                    source_database="semabridge",
                    source_schema="public",
                    source_table="Customer_Table",
                    columns=[SMLColumn(unique_name="Customer ID", data_type=DataType.INTEGER)],
                )
            ],
        )
        uc_tables = [
            {
                "catalog": "workspace",
                "schema": "default",
                "name": "customer_table",
                "full_name": "workspace.default.customer_table",
                "columns": ["customer_id"],
            },
            {
                "catalog": "workspace",
                "schema": "gold",
                "name": "dim_customers_override",
                "full_name": "workspace.gold.dim_customers_override",
                "columns": ["customer_id"],
            },
        ]
        report = match_databricks_tables(
            model,
            uc_tables,
            target_catalog="workspace",
            target_schema="default",
            saved_overrides={"Customer_Table": "workspace.gold.dim_customers_override"},
        )
        assert "Customer_Table" in report.matched
        matched = report.matched["Customer_Table"]
        assert matched.full_name == "workspace.gold.dim_customers_override"
        assert matched.match_reason == "saved_override"
        assert matched.column_matches == {"Customer ID": "customer_id"}

    def test_pbix_foreign_snowflake_schema_matches_target_databricks_loose_name_and_columns(self):
        model = SMLModel(
            unique_name="PBIXModel",
            datasets=[
                SMLDataset(
                    unique_name="Customer_Table",
                    source_database="semabridge",
                    source_schema="public",
                    source_table="Customer_Table",
                    columns=[
                        SMLColumn(unique_name="Customer ID", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="First_Name", data_type=DataType.STRING),
                    ],
                )
            ],
        )
        uc_tables = [
            {
                "catalog": "workspace",
                "schema": "default",
                "name": "customer_table",
                "full_name": "workspace.default.customer_table",
                "columns": ["customer_id", "first_name", "email"],
            }
        ]
        report = match_databricks_tables(
            model,
            uc_tables,
            target_catalog="workspace",
            target_schema="default",
        )
        assert "Customer_Table" in report.matched
        matched = report.matched["Customer_Table"]
        assert matched.full_name == "workspace.default.customer_table"
        assert matched.quoted_fqn == "`workspace`.`default`.`customer_table`"
        assert matched.column_matches == {
            "Customer ID": "customer_id",
            "First_Name": "first_name",
        }
        assert report.missing_facts == []
        assert report.missing_lookups == []
        assert report.ambiguous == {}

    def test_column_overlap_matches_renamed_table_and_marks_close_ties_ambiguous(self):
        # 1. Clear column-overlap winner
        model = SMLModel(
            unique_name="OverlapModel",
            datasets=[
                SMLDataset(
                    unique_name="FactRevenue",
                    is_fact=True,
                    columns=[
                        SMLColumn(unique_name="OrderID", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="GrossAmount", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="NetAmount", data_type=DataType.DECIMAL),
                    ],
                ),
                SMLDataset(
                    unique_name="MissingDim",
                    is_fact=False,
                    columns=[SMLColumn(unique_name="UnmatchedCol", data_type=DataType.STRING)],
                ),
                SMLDataset(
                    unique_name="MissingFact",
                    is_fact=True,
                    columns=[SMLColumn(unique_name="OtherUnmatchedCol", data_type=DataType.INTEGER)],
                ),
            ],
        )
        uc_tables = [
            {
                "catalog": "workspace",
                "schema": "default",
                "name": "orders_gold_v2",
                "full_name": "workspace.default.orders_gold_v2",
                "columns": ["order_id", "gross_amount", "net_amount"],
            }
        ]
        report = match_databricks_tables(
            model, uc_tables, target_catalog="workspace", target_schema="default"
        )
        assert report.matched["FactRevenue"].full_name == "workspace.default.orders_gold_v2"
        assert report.matched["FactRevenue"].match_reason == "column_overlap"
        assert report.missing_lookups == ["MissingDim"]
        assert report.missing_facts == ["MissingFact"]

        # 2. Close tie -> marked ambiguous instead of silently guessing
        uc_tied = [
            {
                "catalog": "workspace",
                "schema": "default",
                "name": "orders_2025",
                "full_name": "workspace.default.orders_2025",
                "columns": ["order_id", "gross_amount", "net_amount"],
            },
            {
                "catalog": "workspace",
                "schema": "default",
                "name": "orders_2026",
                "full_name": "workspace.default.orders_2026",
                "columns": ["order_id", "gross_amount", "net_amount"],
            },
        ]
        tied_report = match_databricks_tables(
            model, uc_tied, target_catalog="workspace", target_schema="default"
        )
        assert "FactRevenue" not in tied_report.matched
        assert "FactRevenue" in tied_report.ambiguous
        assert set(tied_report.ambiguous["FactRevenue"]) == {
            "workspace.default.orders_2025",
            "workspace.default.orders_2026",
        }

    def test_publisher_resolves_foreign_snowflake_source_to_matched_databricks_table(self):
        model = SMLModel(
            unique_name="PBIXSales",
            datasets=[
                SMLDataset(
                    unique_name="Customer_Table",
                    source_database="semabridge",
                    source_schema="public",
                    source_table="Customer_Table",
                    columns=[SMLColumn(unique_name="Customer ID", data_type=DataType.INTEGER)],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(catalog="workspace", schema="default"))
        uc_tables = [
            {
                "catalog": "workspace",
                "schema": "default",
                "name": "customer_table",
                "full_name": "workspace.default.customer_table",
                "columns": ["customer_id"],
            }
        ]
        report = match_databricks_tables(
            model, uc_tables, target_catalog="workspace", target_schema="default"
        )
        publisher.apply_table_match_report(report)

        resolved = publisher._resolve_source_table(model.datasets[0])
        assert resolved == "`workspace`.`default`.`customer_table`"

    def test_publisher_live_rebind_foreign_source_when_target_table_exists(self):
        ds = SMLDataset(
            unique_name="Customer_Table",
            source_database="semabridge",
            source_schema="public",
            source_table="Customer_Table",
            columns=[SMLColumn(unique_name="Customer_ID", data_type=DataType.INTEGER)],
        )
        publisher = DatabricksPublisher(_cfg(catalog="workspace", schema="default"))
        publisher._offline_plan_mode = False

        def _fake_exists(fqn: str) -> bool:
            return fqn == "`workspace`.`default`.`Customer_Table`"

        with patch.object(publisher, "_check_source_table_exists", side_effect=_fake_exists):
            resolved = publisher._resolve_source_table(ds)

        assert resolved == "`workspace`.`default`.`Customer_Table`"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Dialect Rendering & Text Measures
# ─────────────────────────────────────────────────────────────────────────────


class TestDatabricksDialectAndTextMeasures:
    def _translator(self) -> DatabricksMeasureTranslator:
        return DatabricksMeasureTranslator(
            behavior=DatabricksBehavior(),
            sanitize_identifier=lambda s: "".join(c if c.isalnum() or c == "_" else "_" for c in str(s or "")).strip("_"),
            build_aggregation_sql=lambda m: f"SUM(`{m.source_column}`)" if m.source_column else None,
            distinct_count_expression=lambda col: f"COUNT(DISTINCT {col})",
        )

    def test_normalize_dialect_sql_replaces_snowflake_float_casts(self):
        tr = self._translator()
        assert tr.normalize_dialect_sql("(`revenue`)::FLOAT / NULLIF((`cost`)::FLOAT, 0)") == (
            "CAST(`revenue` AS DOUBLE) / NULLIF(CAST(`cost` AS DOUBLE), 0)"
        )
        assert tr.normalize_dialect_sql("SUM(`amount`)::FLOAT") == "CAST(SUM(`amount`) AS DOUBLE)"

    def test_measure_references_render_databricks_measure_fn(self):
        tr = self._translator()
        sql_map = {
            tr.measure_ref_key("Total Sales"): "SUM(`sales_amount`)",
            tr.measure_ref_key("Total Cost"): "SUM(`cost_amount`)",
        }
        sql = tr.try_contextual_dax_to_sql(
            "[Total Sales] - [Total Cost]",
            sql_map,
            use_measure_refs=True,
        )
        assert sql == "(MEASURE(`Total Sales`)) - (MEASURE(`Total Cost`))"

        div_sql = tr.try_contextual_dax_to_sql(
            "DIVIDE([Total Sales], [Total Cost], 0)",
            sql_map,
            use_measure_refs=True,
        )
        assert div_sql == "COALESCE((MEASURE(`Total Sales`)) / NULLIF((MEASURE(`Total Cost`)), 0), 0)"

    def test_text_measures_format_and_ampersand_concat(self):
        tr = self._translator()
        # Standalone FORMAT(...)
        fmt_sql = tr.try_simple_dax_to_sql('FORMAT(SUM(\'Sales\'[Amount]), "#,##0.00")')
        assert fmt_sql == "FORMAT_NUMBER(sum(`amount`), '#,##0.00')"

        # Column concatenation with `&`
        concat_cols = tr.try_simple_dax_to_sql("'Customer'[FirstName] & \" \" & 'Customer'[LastName]")
        assert concat_cols == "CONCAT(`firstname`, ' ', `lastname`)"

        # Contextual text measure with `&` + `FORMAT([Measure], ...)` + MEASURE(`...`)
        sql_map = {tr.measure_ref_key("Total Sales"): "SUM(`sales_amount`)"}
        text_measure = tr.try_contextual_dax_to_sql(
            '"$" & FORMAT([Total Sales], "#,##0")',
            sql_map,
            use_measure_refs=True,
        )
        assert text_measure == "CONCAT('$', FORMAT_NUMBER(MEASURE(`Total Sales`), '#,##0'))"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Deploy Hardening
# ─────────────────────────────────────────────────────────────────────────────


class TestDatabricksDeployHardening:
    def _sample_metric_view_sql(self, fqn: str = "`workspace`.`default`.`mv_sales`") -> str:
        return (
            f"CREATE OR REPLACE VIEW {fqn} WITH METRICS LANGUAGE YAML AS $$\n"
            f"version: 1.1\n"
            f"source: workspace.default.sales\n"
            f"comment: SemaBridge sales view\n"
            f"measures:\n"
            f"  - name: good_revenue\n"
            f"    expr: SUM(`revenue`)\n"
            f"  - name: bad_measure_1\n"
            f"    expr: INVALID_FUNC(`x`)\n"
            f"  - name: bad_measure_2\n"
            f"    expr: ANOTHER_BAD(`y`)\n"
            f"$$"
        )

    def test_per_measure_rejection_retry_staged_strips_bad_measures_and_comments(self):
        publisher = DatabricksPublisher(_cfg())
        live_fqn = "`workspace`.`default`.`mv_sales`"
        entry = {
            "sql": self._sample_metric_view_sql(live_fqn),
            "live_fqn": live_fqn,
            "is_metric": True,
            "needs_view_drop_before_promote": False,
        }
        submitted_yaml_sqls: list[str] = []

        def _mock_execute(statements: list[str], *args, **kwargs):
            sql = statements[0]
            if "WITH METRICS LANGUAGE YAML" in sql:
                submitted_yaml_sqls.append(sql)
                payload = yaml.safe_load(sql.split("$$")[1]) or {}
                m_names = {m["name"] for m in payload.get("measures", []) if isinstance(m, dict)}
                if "bad_measure_1" in m_names:
                    raise DatabricksPublishError(
                        "INVALID_METRIC_VIEW_MEASURE: measure 'bad_measure_1' failed validation"
                    )
                if "bad_measure_2" in m_names:
                    raise DatabricksPublishError(
                        "INVALID_METRIC_VIEW_MEASURE: error evaluating measure `bad_measure_2`"
                    )
            return [{"result": {"data_array": []}}]

        with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
            out_fqn, ok, err, _ = publisher._deploy_single_view_staged(
                entry, _retry_mgr(), token="abc12345"
            )

        assert ok is True
        assert out_fqn == live_fqn
        assert err == ""
        # Final promoted SQL has only good_revenue; omissions go to the run
        # report (publisher._omitted_measures), the business comment is kept:
        promoted_sql = submitted_yaml_sqls[-1]
        yaml_part = promoted_sql.split("$$")[1]
        payload = yaml.safe_load(yaml_part)
        measure_names = [m["name"] for m in payload["measures"]]
        assert measure_names == ["good_revenue"]
        assert payload["comment"] == "SemaBridge sales view"
        assert [o["name"] for o in publisher._omitted_measures] == ["bad_measure_1", "bad_measure_2"]

        omitted_events = [e for e in publisher.deploy_events if e["action"] == "measure_omitted"]
        assert len(omitted_events) == 2

    def test_per_measure_rejection_retry_direct_caps_at_5_retries(self):
        publisher = DatabricksPublisher(_cfg())
        live_fqn = "`workspace`.`default`.`mv_many_bad`"
        measures_yaml = "\n".join(
            [f"  - name: bad_{i}\n    expr: BAD({i})" for i in range(1, 8)]
            + ["  - name: good_one\n    expr: SUM(`x`)"]
        )
        view_sql = (
            f"CREATE OR REPLACE VIEW {live_fqn} WITH METRICS LANGUAGE YAML AS $$\n"
            f"version: 1.1\n"
            f"source: workspace.default.sales\n"
            f"measures:\n{measures_yaml}\n$$"
        )
        entry = {
            "sql": view_sql,
            "live_fqn": live_fqn,
            "is_metric": True,
            "needs_view_drop_before_promote": False,
        }

        def _always_reject_next_bad(statements: list[str], *args, **kwargs):
            sql = statements[0]
            if "WITH METRICS LANGUAGE YAML" in sql:
                payload = yaml.safe_load(sql.split("$$")[1]) or {}
                m_names = {m["name"] for m in payload.get("measures", []) if isinstance(m, dict)}
                for i in range(1, 8):
                    if f"bad_{i}" in m_names:
                        raise DatabricksPublishError(f"measure 'bad_{i}' is invalid")
            return []

        with patch.object(publisher, "execute_statements", side_effect=_always_reject_next_bad):
            _, ok, _, _ = publisher._deploy_single_view_direct(entry, _retry_mgr())

        # 7 bad measures > 5 max retries -> fails after exactly 5 measure_omitted retries
        assert ok is False
        omitted_events = [e for e in publisher.deploy_events if e["action"] == "measure_omitted"]
        assert len(omitted_events) == 5

    def test_no_silent_sql_fallback_by_default_and_honors_allow_sql_view_fallback(self):
        default_behavior = DatabricksBehavior()
        assert default_behavior.allow_sql_view_fallback is False
        assert default_behavior.cleanup_orphan_views_on_copy is True

        pub_default = DatabricksPublisher(_cfg(), behavior=ConnectorBehavior())
        assert pub_default._allow_metric_deploy_sql_fallback() is False

        pub_enabled = DatabricksPublisher(
            _cfg(),
            behavior=ConnectorBehavior(
                databricks=DatabricksBehavior(allow_sql_view_fallback=True)
            ),
        )
        assert pub_enabled._allow_metric_deploy_sql_fallback() is True

    def test_target_concurrency_lock_serializes_same_schema_and_allows_different_schemas(self):
        active_same = 0
        max_active_same = 0
        counter_lock = threading.Lock()

        def _run_publish(catalog: str, schema: str, barrier: threading.Barrier | None = None):
            pub = DatabricksPublisher(_cfg(catalog=catalog, schema=schema))
            model = SMLModel(unique_name="LockTest", datasets=[], metrics=[])

            def _slow_locked(sml_model, *, sync_mode=None):
                nonlocal active_same, max_active_same
                if barrier is not None:
                    barrier.wait(timeout=2.0)
                with counter_lock:
                    active_same += 1
                    max_active_same = max(max_active_same, active_same)
                time.sleep(0.05)
                with counter_lock:
                    active_same -= 1
                return f"databricks://{catalog}/{schema}/LockTest"

            with patch.object(pub, "_publish_locked", side_effect=_slow_locked):
                pub.publish(model)

        # Two threads targeting the SAME catalog.schema must serialize (max_active_same == 1)
        t1 = threading.Thread(target=_run_publish, args=("lock_cat", "lock_sch"))
        t2 = threading.Thread(target=_run_publish, args=("lock_cat", "lock_sch"))
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        assert max_active_same == 1

        # Two threads targeting DIFFERENT schemas can run concurrently (barrier succeeds)
        active_same = 0
        max_active_same = 0
        barrier = threading.Barrier(2)
        t3 = threading.Thread(target=_run_publish, args=("lock_cat", "sch_a", barrier))
        t4 = threading.Thread(target=_run_publish, args=("lock_cat", "sch_b", barrier))
        t3.start()
        t4.start()
        t3.join(timeout=5.0)
        t4.join(timeout=5.0)
        assert max_active_same == 2

    def test_orphan_view_cleanup_copy_mode_safety_and_merge_skip(self):
        publisher = DatabricksPublisher(_cfg(catalog="workspace", schema="default"))
        dropped_sqls: list[str] = []

        def _mock_execute(statements: list[str], *args, **kwargs):
            sql = statements[0]
            if sql.startswith("SHOW VIEWS"):
                return [
                    {
                        "result": {
                            "data_array": [
                                ["default", "mv_current", "false"],
                                ["default", "mv_orphan_managed", "false"],
                                ["default", "user_manual_view", "false"],
                                ["default", "user_fact_table", "false"],
                            ]
                        }
                    }
                ]
            if sql.startswith("DESCRIBE EXTENDED"):
                if "mv_orphan_managed" in sql:
                    return [
                        {
                            "result": {
                                "data_array": [
                                    ["Type", "VIEW"],
                                    ["Table Properties", "[semabridge.managed=true]"],
                                ]
                            }
                        }
                    ]
                if "user_manual_view" in sql:
                    return [
                        {
                            "result": {
                                "data_array": [
                                    ["Type", "VIEW"],
                                    ["Comment", "Manually created analyst view"],
                                ]
                            }
                        }
                    ]
                if "user_fact_table" in sql:
                    return [
                        {
                            "result": {
                                "data_array": [
                                    ["Type", "MANAGED"],
                                    ["Table Properties", "[semabridge.managed=true]"],
                                ]
                            }
                        }
                    ]
            if sql.startswith("DROP VIEW IF EXISTS"):
                dropped_sqls.append(sql)
                return []
            return []

        current_views = {"`workspace`.`default`.`mv_current`"}

        # 1. merge mode -> skipped completely (0 drops)
        with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
            dropped_merge = publisher._cleanup_orphan_semabridge_views(
                current_views, sync_mode="merge"
            )
        assert dropped_merge == []
        assert dropped_sqls == []

        # 2. copy mode -> drops ONLY mv_orphan_managed (never current, never user view, never table)
        with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
            dropped_copy = publisher._cleanup_orphan_semabridge_views(
                current_views, sync_mode="copy"
            )
        assert dropped_copy == ["`workspace`.`default`.`mv_orphan_managed`"]
        assert dropped_sqls == ["DROP VIEW IF EXISTS `workspace`.`default`.`mv_orphan_managed`"]

    def test_no_such_catalog_exception_caches_negative_and_rebinds_to_target_schema(self):
        publisher = DatabricksPublisher(_cfg(catalog="workspace", schema="default"))
        publisher._offline_plan_mode = False
        show_semabridge_calls = 0

        def _mock_execute(statements: list[str], *args, **kwargs):
            nonlocal show_semabridge_calls
            sql = statements[0]
            if sql == "SHOW TABLES IN `semabridge`.`public`":
                show_semabridge_calls += 1
                raise DatabricksPublishError(
                    "[NO_SUCH_CATALOG_EXCEPTION] Catalog 'semabridge' was not found. SQLSTATE: 42704"
                )
            if sql == "SHOW TABLES IN `workspace`.`default`":
                return [
                    {
                        "result": {
                            "data_array": [
                                ["default", "Sales", "false"],
                                ["default", "Store", "false"],
                                ["default", "Item", "false"],
                                ["default", "District", "false"],
                                ["default", "Time", "false"],
                            ]
                        }
                    }
                ]
            return []

        datasets = [
            SMLDataset(
                unique_name=name,
                source_database="semabridge",
                source_schema="public",
                source_table=name,
                columns=[SMLColumn(unique_name="ID", data_type=DataType.INTEGER)],
            )
            for name in ("Sales", "Store", "Item", "District", "Time")
        ]

        with patch.object(publisher, "execute_statements", side_effect=_mock_execute):
            assert publisher._check_source_table_exists("`semabridge`.`public`.`Sales`") is False
            resolved = [publisher._resolve_source_table(ds) for ds in datasets]

        assert show_semabridge_calls == 1
        assert resolved == [f"`workspace`.`default`.`{name}`" for name in ("Sales", "Store", "Item", "District", "Time")]
