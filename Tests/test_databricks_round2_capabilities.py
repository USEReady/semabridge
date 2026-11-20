"""Offline unit & integration tests for Round 2 Databricks Metric View capabilities:

1. Native Unity Catalog Metric View window measures for DAX time intelligence
2. Optional materialization YAML section & plan() metadata
3. Multi-fact model deployment selection (separate_views / primary_fact_only / selected_facts)
"""

from __future__ import annotations

import re
import pytest
import yaml
from pydantic import SecretStr, ValidationError

from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.sml.models import (
    AggregationType,
    Cardinality,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)


def _cfg() -> DatabricksConfig:
    return DatabricksConfig(
        host="adb-1234567890.1.azuredatabricks.net",
        http_path="/sql/1.0/warehouses/test_wh",
        token=SecretStr("dapi_test_token"),
        catalog="main",
        schema_name="analytics",
    )


def _extract_yaml_from_view_sql(sql: str) -> dict:
    m = re.search(r"AS\s+\$\$\s*\n(.*?)\n\s*\$\$", sql, re.DOTALL)
    assert m is not None, f"Could not extract YAML from SQL:\n{sql}"
    doc = yaml.safe_load(m.group(1))
    assert isinstance(doc, dict)
    return doc


def _time_intel_model() -> SMLModel:
    sales = SMLDataset(
        unique_name="Sales",
        label="Sales",
        source_table="Sales",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="order_date", data_type=DataType.DATE),
            SMLColumn(unique_name="region", data_type=DataType.STRING),
            SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
        ],
    )
    metrics = [
        SMLMetric(
            unique_name="Revenue",
            dataset="Sales",
            expression="SUM('Sales'[amount])",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="YTD Revenue",
            dataset="Sales",
            expression="TOTALYTD([Revenue], 'Sales'[order_date])",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="MTD Revenue",
            dataset="Sales",
            expression="TOTALMTD(SUM('Sales'[amount]), 'Sales'[order_date])",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="QTD Revenue",
            dataset="Sales",
            expression="TOTALQTD([Revenue], 'Sales'[order_date])",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="Revenue SPLY",
            dataset="Sales",
            expression="CALCULATE([Revenue], SAMEPERIODLASTYEAR('Sales'[order_date]))",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="Prior Month Revenue",
            dataset="Sales",
            expression="CALCULATE([Revenue], PREVIOUSMONTH('Sales'[order_date]))",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="Prior Quarter Revenue",
            dataset="Sales",
            expression="CALCULATE([Revenue], PREVIOUSQUARTER('Sales'[order_date]))",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="Prior Year Revenue",
            dataset="Sales",
            expression="CALCULATE([Revenue], PREVIOUSYEAR('Sales'[order_date]))",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="Revenue DateAdd LY",
            dataset="Sales",
            expression="CALCULATE([Revenue], DATEADD('Sales'[order_date], -1, YEAR))",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="Revenue DateAdd 3M",
            dataset="Sales",
            expression="CALCULATE([Revenue], DATEADD('Sales'[order_date], -3, MONTH))",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="Rolling 30D Revenue",
            dataset="Sales",
            expression="CALCULATE([Revenue], DATESINPERIOD('Sales'[order_date], LASTDATE('Sales'[order_date]), -30, DAY))",
            aggregation=AggregationType.NONE,
        ),
        SMLMetric(
            unique_name="Rolling 12M Revenue",
            dataset="Sales",
            expression="CALCULATE([Revenue], DATESINPERIOD('Sales'[order_date], MAX('Sales'[order_date]), -12, MONTH))",
            aggregation=AggregationType.NONE,
        ),
    ]
    return SMLModel(
        unique_name="TimeIntelModel",
        label="TimeIntelModel",
        datasets=[sales],
        metrics=metrics,
        relationships=[],
    )


def _two_fact_model() -> SMLModel:
    sales = SMLDataset(
        unique_name="Sales",
        label="Sales",
        source_table="Sales",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="order_date", data_type=DataType.DATE),
            SMLColumn(unique_name="product_id", data_type=DataType.INTEGER),
            SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER),
            SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
        ],
    )
    inventory = SMLDataset(
        unique_name="Inventory",
        label="Inventory",
        source_table="Inventory",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="product_id", data_type=DataType.INTEGER),
            SMLColumn(unique_name="qty_on_hand", data_type=DataType.INTEGER),
        ],
    )
    product = SMLDataset(
        unique_name="Product",
        label="Product",
        source_table="Product",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="product_id", data_type=DataType.INTEGER),
            SMLColumn(unique_name="product_name", data_type=DataType.STRING),
        ],
    )
    customer = SMLDataset(
        unique_name="Customer",
        label="Customer",
        source_table="Customer",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER),
            SMLColumn(unique_name="customer_name", data_type=DataType.STRING),
        ],
    )
    return SMLModel(
        unique_name="RetailMultiFact",
        label="RetailMultiFact",
        datasets=[sales, inventory, product, customer],
        relationships=[
            SMLRelationship(
                unique_name="sales_product",
                from_dataset="Sales",
                from_columns=["product_id"],
                to_dataset="Product",
                to_columns=["product_id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            SMLRelationship(
                unique_name="sales_customer",
                from_dataset="Sales",
                from_columns=["customer_id"],
                to_dataset="Customer",
                to_columns=["customer_id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            SMLRelationship(
                unique_name="inv_product",
                from_dataset="Inventory",
                from_columns=["product_id"],
                to_dataset="Product",
                to_columns=["product_id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Sales",
                dataset="Sales",
                source_column="amount",
                aggregation=AggregationType.SUM,
            ),
            SMLMetric(
                unique_name="Total Qty",
                dataset="Inventory",
                source_column="qty_on_hand",
                aggregation=AggregationType.SUM,
            ),
        ],
    )


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeStatementsSession:
    """Minimal offline Databricks Statements API stub for plan/publish parity tests."""

    def __init__(self, existing_tables: dict[str, list[str]] | None = None):
        self.existing_tables = {
            k.lower(): list(v) for k, v in (existing_tables or {}).items()
        }
        self.executed: list[str] = []

    def post(self, url: str, headers=None, json=None, timeout=None):
        stmt = str((json or {}).get("statement") or "").strip()
        self.executed.append(stmt)
        upper = stmt.upper()

        if upper.startswith("SHOW SCHEMAS"):
            return _FakeResponse({
                "status": {"state": "SUCCEEDED"},
                "result": {"data_array": [["analytics"]]},
            })
        if upper.startswith("SHOW TABLES IN"):
            m = re.search(r"IN\s+`?([^`.\s]+)`?\.`?([^`.\s]+)`?", stmt, re.IGNORECASE)
            key = f"{m.group(1)}.{m.group(2)}".lower() if m else ""
            rows = [[t, t, False] for t in self.existing_tables.get(key, [])]
            return _FakeResponse({
                "status": {"state": "SUCCEEDED"},
                "result": {"data_array": rows},
            })
        if upper.startswith("DESCRIBE TABLE EXTENDED"):
            if "__sbstg_" in stmt:
                return _FakeResponse({
                    "status": {"state": "SUCCEEDED"},
                    "result": {"data_array": [["Type", "VIEW", ""]]},
                })
            return _FakeResponse({
                "status": {"state": "FAILED", "error": {"message": "TABLE_OR_VIEW_NOT_FOUND"}},
            })
        if upper.startswith("SHOW GRANTS"):
            return _FakeResponse({
                "status": {"state": "SUCCEEDED"},
                "result": {"data_array": []},
            })
        return _FakeResponse({
            "status": {"state": "SUCCEEDED"},
            "result": {"data_array": []},
        })


# ── 1. Window Measures for DAX Time Intelligence ────────────────────────────


def test_window_dax_translator_patterns():
    publisher = DatabricksPublisher(_cfg())
    translator = publisher._measure_translator
    ref_map = {"REVENUE": "SUM(`amount`)"}

    cases = [
        ("TOTALYTD([Revenue], 'Date'[OrderDate])", "cumulative", "orderdate"),
        ("TOTALMTD([Revenue], 'Date'[OrderDate])", "cumulative", "orderdate"),
        ("TOTALQTD(SUM('Sales'[amount]), 'Date'[OrderDate])", "cumulative", "orderdate"),
        ("CALCULATE([Revenue], DATESYTD('Date'[OrderDate]))", "cumulative", "orderdate"),
        ("CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[OrderDate]))", "trailing 1 year", "orderdate"),
        ("CALCULATE([Revenue], PREVIOUSMONTH('Date'[OrderDate]))", "trailing 1 month", "orderdate"),
        ("CALCULATE([Revenue], PREVIOUSQUARTER('Date'[OrderDate]))", "trailing 1 quarter", "orderdate"),
        ("CALCULATE([Revenue], PREVIOUSYEAR('Date'[OrderDate]))", "trailing 1 year", "orderdate"),
        ("CALCULATE([Revenue], DATEADD('Date'[OrderDate], -1, YEAR))", "trailing 1 year", "orderdate"),
        ("CALCULATE([Revenue], DATEADD('Date'[OrderDate], -3, MONTH))", "trailing 3 month", "orderdate"),
        ("CALCULATE([Revenue], DATEADD('Date'[OrderDate], -1, QUARTER))", "trailing 1 quarter", "orderdate"),
        ("CALCULATE([Revenue], DATEADD('Date'[OrderDate], 1, MONTH))", "leading 1 month", "orderdate"),
        ("CALCULATE([Revenue], DATESINPERIOD('Date'[OrderDate], LASTDATE('Date'[OrderDate]), -30, DAY))", "trailing 30 day", "orderdate"),
        ("CALCULATE([Revenue], DATESINPERIOD('Date'[OrderDate], MAX('Date'[OrderDate]), -12, MONTH))", "trailing 12 month", "orderdate"),
    ]

    for dax, expected_range, expected_order in cases:
        result = translator.try_window_dax_to_metric_view(dax, measure_sql_map=ref_map)
        assert result is not None, f"Expected window translation for: {dax}"
        assert "OVER" not in result["expr"].upper(), f"Window measure expr must not use SQL OVER(): {result}"
        assert len(result["window"]) == 1
        assert result["window"][0]["range"] == expected_range, f"Mismatch for {dax}"
        assert result["window"][0]["order"] == expected_order, f"Order mismatch for {dax}"
        assert result["window"][0]["semiadditive"] == "last"


def test_window_measures_emitted_in_yaml_and_plan_without_placeholders():
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            measure_view_type="metric_view",
            enable_low_confidence_drafts=True,
        )
    )
    publisher = DatabricksPublisher(_cfg(), behavior=behavior)
    model = _time_intel_model()

    stmts, created, skipped, _ = publisher.generate_measure_view_statements(model)
    assert created == 1
    assert skipped == 0
    assert len(stmts) == 1

    doc = _extract_yaml_from_view_sql(stmts[0])
    measures_by_name = {m["name"]: m for m in doc["measures"]}
    dim_names = {d["name"] for d in doc["dimensions"]}

    # No CAST(NULL AS DOUBLE) placeholders for any time-intelligence measure
    for m in doc["measures"]:
        assert "CAST(NULL" not in str(m["expr"]).upper(), f"Unexpected placeholder for {m['name']}"

    # Base measure has no window
    assert "window" not in measures_by_name["Revenue"]

    expected_ranges = {
        "YTD_Revenue": "cumulative",
        "MTD_Revenue": "cumulative",
        "QTD_Revenue": "cumulative",
        "Revenue_SPLY": "trailing 1 year",
        "Prior_Month_Revenue": "trailing 1 month",
        "Prior_Quarter_Revenue": "trailing 1 quarter",
        "Prior_Year_Revenue": "trailing 1 year",
        "Revenue_DateAdd_LY": "trailing 1 year",
        "Revenue_DateAdd_3M": "trailing 3 month",
        "Rolling_30D_Revenue": "trailing 30 day",
        "Rolling_12M_Revenue": "trailing 12 month",
    }
    for name, expected_range in expected_ranges.items():
        assert name in measures_by_name, f"Missing measure {name}; found {list(measures_by_name)}"
        w = measures_by_name[name].get("window")
        assert isinstance(w, list) and len(w) == 1
        assert w[0]["range"] == expected_range
        assert w[0]["semiadditive"] == "last"
        # Order dimension matches an emitted dimension in the view
        assert w[0]["order"] in dim_names

    # Verify plan() surfaces window measures cleanly with status="ok" and window metadata
    plan = publisher.plan(model, lookup=False)
    assert len(plan["views"]) == 1
    plan_measures = {m["name"]: m for m in plan["views"][0]["measures"]}
    assert plan_measures["Revenue"]["status"] == "ok"
    assert "window" not in plan_measures["Revenue"]
    for name, expected_range in expected_ranges.items():
        pm = plan_measures[name]
        assert pm["status"] == "ok"
        assert pm["window"][0]["range"] == expected_range


# ── 2. Optional Materialization ─────────────────────────────────────────────


def test_materialization_behavior_defaults_and_yaml_off_vs_on():
    default_behavior = DatabricksBehavior()
    assert default_behavior.enable_materialization is False
    assert default_behavior.materialization_schedule == "every 1 day"
    assert default_behavior.materialization_mode == "relaxed"

    model = _time_intel_model()

    # OFF (default): YAML and plan() omit materialization completely
    pub_off = DatabricksPublisher(_cfg(), behavior=ConnectorBehavior())
    stmts_off, _, _, _ = pub_off.generate_measure_view_statements(model)
    doc_off = _extract_yaml_from_view_sql(stmts_off[0])
    assert "materialization" not in doc_off

    plan_off = pub_off.plan(model, lookup=False)
    assert "materialization" not in plan_off
    assert "materialization" not in plan_off["views"][0]

    # ON: YAML emits valid materialization section and plan() surfaces it
    behavior_on = ConnectorBehavior(
        databricks=DatabricksBehavior(
            enable_materialization=True,
            materialization_schedule="every 1 hour",
            materialization_mode="relaxed",
        )
    )
    pub_on = DatabricksPublisher(_cfg(), behavior=behavior_on)
    stmts_on, _, _, _ = pub_on.generate_measure_view_statements(model)
    doc_on = _extract_yaml_from_view_sql(stmts_on[0])

    assert "materialization" in doc_on
    mat = doc_on["materialization"]
    assert mat["schedule"] == "every 1 hour"
    assert mat["mode"] == "relaxed"
    assert isinstance(mat["materialized_views"], list) and len(mat["materialized_views"]) == 1
    mv_entry = mat["materialized_views"][0]
    assert mv_entry["type"] == "aggregated"
    assert set(mv_entry["dimensions"]) == {d["name"] for d in doc_on["dimensions"]}
    assert set(mv_entry["measures"]) == {m["name"] for m in doc_on["measures"]}

    plan_on = pub_on.plan(model, lookup=False)
    assert plan_on["materialization"] == {
        "enabled": True,
        "schedule": "every 1 hour",
        "mode": "relaxed",
    }
    assert plan_on["views"][0]["materialization"]["enabled"] is True
    assert plan_on["views"][0]["materialization"]["schedule"] == "every 1 hour"
    assert len(plan_on["views"][0]["materialization"]["materialized_views"]) == 1


# ── 3. Multi-Fact Model Selection ───────────────────────────────────────────


def test_multi_fact_behavior_defaults_and_validation():
    default_behavior = DatabricksBehavior()
    assert default_behavior.multi_fact_mode == "separate_views"
    assert default_behavior.selected_fact_tables == []

    with pytest.raises(ValidationError):
        DatabricksBehavior(multi_fact_mode="invalid_mode")


def test_multi_fact_separate_views_default():
    model = _two_fact_model()
    pub = DatabricksPublisher(_cfg(), behavior=ConnectorBehavior())

    plan = pub.plan(model, lookup=False)
    assert len(plan["views"]) == 2
    assert all(not v["skipped"] for v in plan["views"])
    assert {v["fact"] for v in plan["views"]} == {"Sales", "Inventory"}

    stmts, created, _, _ = pub.generate_measure_view_statements(model)
    assert created == 2
    assert len(stmts) == 2


def test_multi_fact_primary_fact_only_plan_generate_and_publish_parity():
    model = _two_fact_model()
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            multi_fact_mode="primary_fact_only",
        )
    )
    pub = DatabricksPublisher(_cfg(), behavior=behavior)

    # plan() shows Sales active (2 relationships vs Inventory's 1) and Inventory skipped
    plan = pub.plan(model, lookup=False)
    by_fact = {v["fact"]: v for v in plan["views"]}
    assert set(by_fact.keys()) == {"Sales", "Inventory"}
    assert by_fact["Sales"]["skipped"] is False
    assert by_fact["Inventory"]["skipped"] is True
    assert "primary_fact_only" in by_fact["Inventory"]["reason"]
    assert "Sales" in by_fact["Inventory"]["reason"]

    # Direct generate_measure_view_statements returns only the primary fact view
    stmts, created, _, _ = pub.generate_measure_view_statements(model)
    assert created == 1
    assert len(stmts) == 1
    assert "Sales" in stmts[0]

    # publish() deploys ONLY the non-skipped primary fact view (parity with plan)
    fake = _FakeStatementsSession(existing_tables={"main.analytics": ["Sales", "Inventory", "Product", "Customer"]})
    pub.session = fake
    pub.publish(model)

    live_creates = [
        s for s in fake.executed
        if s.startswith("CREATE OR REPLACE VIEW") and "__sbstg_" not in s
    ]
    assert len(live_creates) == 1
    assert "Sales" in live_creates[0]
    assert "Inventory" not in live_creates[0]


def test_multi_fact_selected_facts_case_insensitive_and_publish_parity():
    model = _two_fact_model()
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            multi_fact_mode="selected_facts",
            selected_fact_tables=["inventory"],  # intentionally lowercase
        )
    )
    pub = DatabricksPublisher(_cfg(), behavior=behavior)

    plan = pub.plan(model, lookup=False)
    by_fact = {v["fact"]: v for v in plan["views"]}
    assert by_fact["Inventory"]["skipped"] is False
    assert by_fact["Sales"]["skipped"] is True
    assert "selected_facts" in by_fact["Sales"]["reason"]

    stmts, created, _, _ = pub.generate_measure_view_statements(model)
    assert created == 1
    assert len(stmts) == 1
    assert "Inventory" in stmts[0]

    fake = _FakeStatementsSession(existing_tables={"main.analytics": ["Sales", "Inventory", "Product", "Customer"]})
    pub.session = fake
    pub.publish(model)

    live_creates = [
        s for s in fake.executed
        if s.startswith("CREATE OR REPLACE VIEW") and "__sbstg_" not in s
    ]
    assert len(live_creates) == 1
    assert "Inventory" in live_creates[0]
    assert "Sales" not in live_creates[0]


def test_multi_fact_selected_facts_empty_skips_all():
    model = _two_fact_model()
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            multi_fact_mode="selected_facts",
            selected_fact_tables=[],
        )
    )
    pub = DatabricksPublisher(_cfg(), behavior=behavior)

    plan = pub.plan(model, lookup=False)
    assert len(plan["views"]) == 2
    assert all(v["skipped"] is True for v in plan["views"])
    assert all("selected_facts" in v["reason"] for v in plan["views"])
