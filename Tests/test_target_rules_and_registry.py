"""Tests for TargetRules (Snowflake vs Databricks), dynamic registration, and connector registry API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.core.target_rules import (
    _TARGET_RULES_FACTORIES,
    DatabricksDialect,
    SQLDialect,
    SnowflakeDialect,
    TargetCapabilities,
    TargetPolicies,
    TargetRules,
    get_target_rules,
    list_registered_sources,
    list_registered_targets,
    register_target_rules,
)


class TestSnowflakeVsDatabricksRules:
    """Verify SnowflakeRules and DatabricksRules policies, capabilities, and SQL dialects."""

    def test_policies_comparison(self):
        sf = get_target_rules("snowflake")
        dbx = get_target_rules("databricks")

        assert sf.policies.target_type == "snowflake"
        assert sf.policies.display_name == "Snowflake"
        assert sf.policies.view_kind_label == "Snowflake Semantic View"
        assert sf.policies.ai_assistant_name == "Snowflake Cortex Analyst"
        assert sf.policies.text_measures_allowed is False
        assert sf.policies.name_style == "uppercase"
        assert sf.policies.add_default_calendar_table is True
        assert sf.policies.declined_measure_strategy == "null_placeholder"
        assert sf.policies.allow_silent_sql_view_fallback is False
        assert sf.policies.supports_staged_deploy is True
        assert sf.policies.supports_materialization is False
        assert sf.policies.supports_multi_fact_split is False

        assert dbx.policies.target_type == "databricks"
        assert dbx.policies.display_name == "Databricks"
        assert dbx.policies.view_kind_label == "Databricks Unity Catalog Metric View"
        assert dbx.policies.ai_assistant_name == "Databricks AI/BI Genie"
        assert dbx.policies.text_measures_allowed is True
        assert dbx.policies.name_style == "keep_original"
        assert dbx.policies.add_default_calendar_table is False
        assert dbx.policies.declined_measure_strategy == "omit_and_comment"
        assert dbx.policies.allow_silent_sql_view_fallback is False
        assert dbx.policies.supports_staged_deploy is True
        assert dbx.policies.supports_materialization is True
        assert dbx.policies.supports_multi_fact_split is True

    def test_capabilities_comparison(self):
        sf = get_target_rules("snowflake")
        dbx = get_target_rules("databricks")

        assert sf.capabilities.probed is True
        assert sf.capabilities.measure_references is False
        assert sf.capabilities.window_measures is False
        assert sf.capabilities.cortex_ready is True
        assert sf.capabilities.genie_ready is False

        assert dbx.capabilities.probed is False
        assert dbx.capabilities.measure_references is True
        assert dbx.capabilities.window_measures is True
        assert dbx.capabilities.cortex_ready is False
        assert dbx.capabilities.genie_ready is False

        sf_dict = sf.capabilities.to_dict()
        assert sf_dict["cortex_ready"] is True
        assert sf_dict["probe_errors"] == []

    def test_snowflake_dialect(self):
        d = get_target_rules("snowflake").dialect
        assert isinstance(d, SnowflakeDialect)
        assert d.name == "snowflake"

        assert d.quote_identifier("AMOUNT") == '"AMOUNT"'
        assert d.quote_identifier('A"B') == '"A""B"'
        assert d.column("SALES", "AMOUNT") == 'SALES."AMOUNT"'
        assert d.column("", "AMOUNT") == '"AMOUNT"'
        assert d.measure_reference("Total Sales") == '"Total Sales"'
        assert d.measure_reference("Total Sales", inline_sql='SUM(SALES."AMOUNT")') == '(SUM(SALES."AMOUNT"))'
        assert d.cast_float("x") == "(x)::FLOAT"
        assert d.safe_divide("a", "b") == "(a) / NULLIF((b), 0)"
        assert d.concat("first_name", "' '", "last_name") == "CONCAT(first_name, ' ', last_name)"
        assert d.format_number("amt", "#,##0.00") == "TO_VARCHAR(amt, '#,##0.00')"
        assert d.count_rows() == "COUNT(*)"

    def test_databricks_dialect(self):
        d = get_target_rules("databricks").dialect
        assert isinstance(d, DatabricksDialect)
        assert d.name == "databricks"

        assert d.quote_identifier("Total Sales") == "`Total Sales`"
        assert d.quote_identifier("a`b") == "`a``b`"
        assert d.column("source", "amount") == "source.amount"
        assert d.column("source", "Net Revenue") == "source.`Net Revenue`"
        assert d.column("", "amount") == "amount"
        assert d.measure_reference("Total Sales") == "MEASURE(`Total Sales`)"
        assert d.measure_reference("Total Sales", inline_sql="SUM(amount)") == "MEASURE(`Total Sales`)"
        assert d.cast_float("x") == "CAST(x AS DOUBLE)"
        assert d.safe_divide("a", "b") == "(a) / NULLIF((b), 0)"
        assert d.concat("first_name", "' '", "last_name") == "CONCAT(first_name, ' ', last_name)"
        assert d.format_number("amt", "#,##0.00") == "FORMAT_NUMBER(amt, '#,##0.00')"
        assert d.count_rows() == "COUNT(1)"

    def test_unknown_target_raises_key_error(self):
        with pytest.raises(KeyError, match="Unsupported target connector"):
            get_target_rules("unknown_warehouse_xyz")


class TestConnectorRegistryAPIAndDynamicRegistration:
    """Verify GET /api/connectors/registry endpoints and dynamic target registration."""

    def test_get_connector_registry_endpoint(self):
        client = TestClient(app)
        resp = client.get("/api/connectors/registry")
        assert resp.status_code == 200
        data = resp.json()

        assert "sources" in data and "targets" in data
        source_ids = {s["id"] for s in data["sources"]}
        target_ids = {t["id"] for t in data["targets"]}

        assert {"pbix", "fabric", "snowflake", "databricks", "tableau"}.issubset(source_ids)
        assert {"snowflake", "databricks"}.issubset(target_ids)
        assert len(data["sources"]) == len(list_registered_sources())
        assert len(data["targets"]) == len(list_registered_targets())

    def test_get_single_target_descriptor_and_404(self):
        client = TestClient(app)

        resp = client.get("/api/connectors/registry/targets/databricks")
        assert resp.status_code == 200
        dbx = resp.json()
        assert dbx["id"] == "databricks"
        assert dbx["label"] == "Databricks"
        assert dbx["sql_dialect"] == "databricks"
        assert dbx["policies"]["supports_materialization"] is True
        assert dbx["policies"]["supports_multi_fact_split"] is True
        assert dbx["capabilities"]["measure_references"] is True

        sf_resp = client.get("/api/connectors/registry/targets/snowflake")
        assert sf_resp.status_code == 200
        assert sf_resp.json()["id"] == "snowflake"

        not_found = client.get("/api/connectors/registry/targets/nonexistent_connector")
        assert not_found.status_code == 404

    def test_dynamic_bigquery_registration_shows_up_in_registry_and_api(self):
        class BigQueryDialect(SQLDialect):
            name = "bigquery"

            def quote_identifier(self, identifier: str) -> str:
                return f"`{identifier}`"

            def column(self, table_alias: str, column_name: str) -> str:
                col = self.quote_identifier(column_name)
                return f"{table_alias}.{col}" if table_alias else col

            def measure_reference(self, measure_name: str, inline_sql=None) -> str:
                return f"({inline_sql})" if inline_sql else self.quote_identifier(measure_name)

            def cast_float(self, expr: str) -> str:
                return f"CAST({expr} AS FLOAT64)"

        try:
            @register_target_rules("bigquery")
            def build_bigquery_rules() -> TargetRules:
                return TargetRules(
                    policies=TargetPolicies(
                        target_type="bigquery",
                        display_name="Google BigQuery",
                        view_kind_label="BigQuery Semantic Layer",
                        ai_assistant_name="Gemini in BigQuery",
                        text_measures_allowed=True,
                        name_style="snake_case",
                    ),
                    capabilities=TargetCapabilities(probed=False, measure_references=False),
                    dialect=BigQueryDialect(),
                )

            target_ids = [t["id"] for t in list_registered_targets()]
            assert "bigquery" in target_ids

            client = TestClient(app)
            reg_resp = client.get("/api/connectors/registry")
            assert reg_resp.status_code == 200
            api_target_ids = [t["id"] for t in reg_resp.json()["targets"]]
            assert "bigquery" in api_target_ids

            bq_resp = client.get("/api/connectors/registry/targets/bigquery")
            assert bq_resp.status_code == 200
            bq = bq_resp.json()
            assert bq["id"] == "bigquery"
            assert bq["label"] == "Google BigQuery"
            assert bq["sql_dialect"] == "bigquery"
            assert bq["policies"]["name_style"] == "snake_case"
        finally:
            _TARGET_RULES_FACTORIES.pop("bigquery", None)

        assert "bigquery" not in [t["id"] for t in list_registered_targets()]


class TestTargetSpecificAdaptation:
    """Verify Databricks vs Snowflake isolation for drop reasons, SML conversion, and mapping engine."""

    def test_adapt_target_message_preserves_snowflake_and_rewrites_databricks(self):
        from semabridge.core.target_rules import adapt_target_message

        msg = (
            "BY_DESIGN_EXCLUDED: expression's root operation produces a string value, not a numeric aggregate "
            "— Snowflake semantic-view metrics must be aggregate expressions. This field should be modeled as a dimension attribute instead."
        )
        assert adapt_target_message(msg, "snowflake") == msg
        assert adapt_target_message(msg, None) == msg

        dbx_msg = adapt_target_message(msg, "databricks")
        assert "Snowflake" not in dbx_msg
        assert "Databricks Unity Catalog metric-view measures must be aggregate expressions" in dbx_msg

        kpi_msg = "KPI on measure 'TotalSalesTY' not migrated — Snowflake semantic views have no KPI object"
        assert adapt_target_message(kpi_msg, "snowflake") == kpi_msg
        dbx_kpi = adapt_target_message(kpi_msg, "databricks")
        assert "Snowflake" not in dbx_kpi
        assert "Databricks Unity Catalog metric views have no KPI object" in dbx_kpi

    def test_drop_ledger_adapts_for_databricks_and_keeps_snowflake(self):
        from semabridge.core.drop_ledger import DropLedger, DropStage

        reason = "sort order of 'Open Month' by 'Open Month No' not carried — Snowflake semantic views have no sort-by-column"

        sf_ledger = DropLedger()
        sf_ledger.record("column_property", "Open Month", DropStage.EXTRACTION, reason, by_design=True)
        assert sf_ledger.records[0].reason == reason
        assert sf_ledger.to_json()[0]["reason"] == reason

        dbx_ledger = DropLedger(target_type="databricks")
        dbx_ledger.record("column_property", "Open Month", DropStage.EXTRACTION, reason, by_design=True)
        assert "Snowflake" not in dbx_ledger.records[0].reason
        assert "Databricks Unity Catalog metric views have no sort-by-column" in dbx_ledger.records[0].reason
        assert "Snowflake" not in dbx_ledger.to_json()[0]["reason"]

    def test_extract_model_entities_adapts_databricks_reasons_and_casts(self):
        from semabridge.api.services.project_mapping_engine import extract_model_entities

        model = {
            "unique_name": "SalesModel",
            "datasets": [
                {
                    "unique_name": "Sales",
                    "columns": [{"unique_name": "Amount", "data_type": "decimal"}],
                }
            ],
            "metrics": [
                {
                    "unique_name": "RatioMetric",
                    "dataset": "Sales",
                    "expression": "DIVIDE(SUM(Sales[Amount]), 2)",
                    "sql_expression": "(SUM(Sales.Amount))::FLOAT",
                    "sync_enabled": True,
                },
                {
                    "unique_name": "MsgMetric",
                    "dataset": "Sales",
                    "expression": '"Hi"',
                    "sql_expression": "",
                    "sync_enabled": False,
                    "sync_failure_reason": (
                        "BY_DESIGN_EXCLUDED: expression's root operation produces a string value, not a numeric aggregate "
                        "— Snowflake semantic-view metrics must be aggregate expressions."
                    ),
                },
            ],
        }

        dbx_entities = extract_model_entities(model, target_connector="databricks")
        by_name = {e["source_name"]: e for e in dbx_entities if e["entity_kind"] == "metric"}
        assert "::FLOAT" not in by_name["RatioMetric"]["target_expression"]
        assert by_name["RatioMetric"]["target_expression"] == "CAST(SUM(Sales.Amount) AS DOUBLE)"
        assert "Snowflake" not in by_name["MsgMetric"]["sync_failure_reason"]
        assert "Databricks Unity Catalog metric-view measures" in by_name["MsgMetric"]["sync_failure_reason"]

        sf_entities = extract_model_entities(model, target_connector="snowflake")
        sf_by_name = {e["source_name"]: e for e in sf_entities if e["entity_kind"] == "metric"}
        assert sf_by_name["RatioMetric"]["target_expression"] == "(SUM(Sales.Amount))::FLOAT"
        assert "Snowflake semantic-view metrics" in sf_by_name["MsgMetric"]["sync_failure_reason"]
