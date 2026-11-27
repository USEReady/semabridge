"""Tests for Databricks Platform Adapter and Registry Integration.

Verifies:
1. Registry resolution for DATABRICKS platform
2. Metric View YAML parsing (embedded in DDL and raw YAML)
3. SQL View DDL parsing (projections, aliases, alter comments)
4. DESCRIBE JSON records parsing
5. Edge cases: backticks in identifiers, multiline YAML formulas, description normalization
6. Integration with Comparison Engine (expression drift, description drift, deletion suppression)
7. Integration with Canonical Baseline Store
"""

from __future__ import annotations

import json
import pytest

from semabridge.governance.drift.adapters.databricks import DatabricksAdapter
from semabridge.governance.drift.adapters.registry import (
    get_adapter,
    is_platform_supported,
)
from semabridge.governance.drift.baseline_store import (
    get_active_baseline,
    save_deploy_baseline,
)
from semabridge.governance.drift.comparison import compare_views
from semabridge.governance.drift.models import (
    DriftCategory,
    ObjectKind,
)


def test_registry_lookup_databricks():
    """Verify DATABRICKS platform is registered and resolvable."""
    assert is_platform_supported("DATABRICKS") is True
    assert is_platform_supported("databricks") is True

    adapter = get_adapter("DATABRICKS")
    assert isinstance(adapter, DatabricksAdapter)
    assert adapter.platform_name == "DATABRICKS"


def test_databricks_parse_metric_view_yaml_ddl():
    """Verify parsing Databricks metric view DDL containing embedded YAML."""
    raw_ddl = """
    CREATE OR REPLACE VIEW `main`.`analytics`.`sales_metric_view`
    WITH METRICS LANGUAGE YAML AS $$
    version: 1.1
    comment: "Governed Sales Metric View"
    source: `main`.`analytics`.`orders`
    dimensions:
      - name: order_date
        expr: o_orderdate
        comment: "Date order was placed"
      - name: customer_nation
        expr: customer.nation.n_name
        comment: "Customer nationality"
    measures:
      - name: total_revenue
        expr: "sum(o_totalprice)"
        comment: "Sum of total price from orders"
      - name: order_count
        expr: "count(o_orderkey)"
        comment: "Total count of orders"
    $$
    """
    adapter = DatabricksAdapter()
    view_def = adapter.parse_definition(
        raw_text=raw_ddl,
        view_name="main.analytics.sales_metric_view",
        source="live",
    )

    assert view_def.platform == "DATABRICKS"
    assert view_def.view_name == "main.analytics.sales_metric_view"
    assert len(view_def.objects) == 5

    metrics = [o for o in view_def.objects if o.kind == ObjectKind.METRIC]
    dimensions = [o for o in view_def.objects if o.kind == ObjectKind.DIMENSION]
    facts = [o for o in view_def.objects if o.kind == ObjectKind.FACT]

    assert len(metrics) == 2
    assert len(dimensions) == 2
    assert len(facts) == 1
    assert facts[0].name == "sales_metric_view"
    assert facts[0].expression == "main.analytics.orders"

    rev_metric = next(m for m in metrics if m.name == "total_revenue")
    assert rev_metric.expression == "sum(o_totalprice)"
    assert rev_metric.description == "Sum of total price from orders"

    date_dim = next(d for d in dimensions if d.name == "order_date")
    assert date_dim.expression == "o_orderdate"
    assert date_dim.description == "Date order was placed"


def test_databricks_parse_raw_yaml_and_multiline():
    """Verify parsing raw YAML metric view definitions with folded multiline expressions."""
    raw_yaml = """
    version: 1.1
    comment: Model Metric View
    dimensions:
      - name: order_status_label
        expr: |-
          CASE o_orderstatus
            WHEN 'O' THEN 'Open'
            WHEN 'F' THEN 'Fulfilled'
            ELSE 'Unknown'
          END
        comment: Clean status label
    measures:
      - name: filtered_sales
        expr: "sum(case when o_orderstatus = 'O' then o_totalprice else 0 end)"
        comment: Sales for open orders only
    """
    adapter = DatabricksAdapter()
    view_def = adapter.parse_definition(
        raw_text=raw_yaml,
        view_name="catalog.schema.model_metric_view",
        source="baseline",
    )

    assert len(view_def.objects) == 2
    dim = next(o for o in view_def.objects if o.kind == ObjectKind.DIMENSION)
    assert dim.name == "order_status_label"
    assert "CASE o_orderstatus" in dim.expression
    assert dim.description == "Clean status label"

    m = next(o for o in view_def.objects if o.kind == ObjectKind.METRIC)
    assert m.name == "filtered_sales"
    assert m.description == "Sales for open orders only"


def test_databricks_parse_sql_view_ddl_and_alter_comments():
    """Verify parsing Databricks standard SQL views with ALTER VIEW comments."""
    raw_sql = """
    CREATE OR REPLACE VIEW catalog.schema.finance_summary AS
    SELECT
        o_orderdate AS order_date,
        c_name AS customer_name,
        sum(amount) AS total_amount,
        avg(discount) AS avg_discount
    FROM catalog.schema.orders
    GROUP BY ALL;

    ALTER VIEW catalog.schema.finance_summary ALTER COLUMN order_date COMMENT 'Order timestamp';
    ALTER VIEW catalog.schema.finance_summary ALTER COLUMN total_amount COMMENT 'Total transaction amount';
    """
    adapter = DatabricksAdapter()
    view_def = adapter.parse_definition(
        raw_text=raw_sql,
        view_name="catalog.schema.finance_summary",
        source="live",
    )

    assert len(view_def.objects) == 5
    metrics = {o.name: o for o in view_def.objects if o.kind == ObjectKind.METRIC}
    dimensions = {o.name: o for o in view_def.objects if o.kind == ObjectKind.DIMENSION}
    facts = {o.name: o for o in view_def.objects if o.kind == ObjectKind.FACT}

    assert "orders" in facts
    assert facts["orders"].expression == "catalog.schema.orders"

    assert "total_amount" in metrics
    assert metrics["total_amount"].expression == "sum(amount)"
    assert metrics["total_amount"].description == "Total transaction amount"

    assert "avg_discount" in metrics
    assert metrics["avg_discount"].expression == "avg(discount)"

    assert "order_date" in dimensions
    assert dimensions["order_date"].expression == "o_orderdate"
    assert dimensions["order_date"].description == "Order timestamp"


def test_databricks_parse_describe_json():
    """Verify parsing Databricks DESCRIBE JSON records."""
    records = [
        {"col_name": "order_id", "data_type": "string", "comment": "Identifier"},
        {"col_name": "revenue_metric", "data_type": "double", "comment": "Total revenue"},
        {"col_name": "avg_price", "data_type": "avg(price)", "comment": "Average unit price"},
    ]
    raw_json = json.dumps({"describe_records": records})

    adapter = DatabricksAdapter()
    view_def = adapter.parse_definition(
        raw_text=raw_json,
        view_name="catalog.schema.describe_view",
    )

    assert len(view_def.objects) == 3
    obj_by_name = {o.name: o for o in view_def.objects}
    assert obj_by_name["order_id"].kind == ObjectKind.DIMENSION
    assert obj_by_name["revenue_metric"].kind == ObjectKind.METRIC
    assert obj_by_name["avg_price"].kind == ObjectKind.METRIC


def test_databricks_describe_json_unknown_kind_not_metric():
    """An object whose kind cannot be determined is reported as UNKNOWN, not METRIC."""
    records = [
        {"col_name": "mystery_col", "data_type": "", "comment": ""},
        {"col_name": "weird_col", "data_type": "custom_opaque_type", "comment": ""},
    ]
    adapter = DatabricksAdapter()
    view_def = adapter.parse_definition(
        raw_text=json.dumps({"describe_records": records}),
        view_name="catalog.schema.describe_unknown",
        source="live",
    )
    assert [o.kind for o in view_def.objects] == [ObjectKind.UNKNOWN, ObjectKind.UNKNOWN]


def test_databricks_comparison_backticks_and_formatting():
    """Verify that backtick-quoted identifiers in Databricks do not trigger false drift."""
    baseline_yaml = """
    version: 1.1
    measures:
      - name: total_sales
        expr: "sum(amount)"
        comment: "Total sales revenue"
    dimensions:
      - name: order_date
        expr: o_orderdate
        comment: "Order date"
    """
    # Live uses backticks and different whitespace/casing
    live_yaml = """
    version: 1.1
    measures:
      - name: `total_sales`
        expr: "SUM(`amount`)"
        comment: "Total sales revenue  "
    dimensions:
      - name: `order_date`
        expr: `o_orderdate`
        comment: "Order date"
    """
    adapter = DatabricksAdapter()
    base_def = adapter.parse_definition(baseline_yaml, "cat.sch.sales_view", "baseline")
    live_def = adapter.parse_definition(live_yaml, "cat.sch.sales_view", "live")

    findings = compare_views(baseline=base_def, live=live_def)
    assert len(findings) == 0, f"Expected 0 findings but got: {findings}"


def test_databricks_comparison_drift_detection():
    """Verify detection of expression drift, description drift, addition, and deletion in Databricks."""
    base_yaml = """
    version: 1.1
    measures:
      - name: revenue
        expr: "sum(price * quantity)"
        comment: "Revenue calculation"
      - name: active_users
        expr: "count(user_id)"
        comment: "Old documentation description"
      - name: retired_metric
        expr: "count(order_id)"
        comment: "To be deleted"
    """
    live_yaml = """
    version: 1.1
    measures:
      - name: revenue
        expr: "sum(price * quantity * (1 - discount))"
        comment: "Revenue calculation"
      - name: active_users
        expr: "count(user_id)"
        comment: "Updated documentation description"
      - name: new_margin_kpi
        expr: "avg(margin)"
        comment: "Newly added KPI"
    """
    adapter = DatabricksAdapter()
    base_def = adapter.parse_definition(base_yaml, "cat.sch.metric_view", "baseline")
    live_def = adapter.parse_definition(live_yaml, "cat.sch.metric_view", "live")

    findings = compare_views(baseline=base_def, live=live_def)
    # Reason: Databricks identifiers are natively lower-case; findings preserve
    # platform-reported casing, and matching/identity is case-insensitive.
    finding_tuples = {(f.name.lower(), f.category) for f in findings}

    assert ("revenue", DriftCategory.EXPRESSION_DRIFT) in finding_tuples
    assert ("active_users", DriftCategory.DESCRIPTION_DRIFT) in finding_tuples
    assert ("retired_metric", DriftCategory.DELETION) in finding_tuples
    assert ("new_margin_kpi", DriftCategory.ADDITION) in finding_tuples


def test_databricks_not_deployed_suppression():
    """Verify that dropped/remediated Databricks metrics suppress deletion drift."""
    base_yaml = """
    version: 1.1
    measures:
      - name: valid_metric
        expr: "sum(amount)"
      - name: remediated_measure
        expr: "cast(null as double)"
    """
    live_yaml = """
    version: 1.1
    measures:
      - name: valid_metric
        expr: "sum(amount)"
    """
    adapter = DatabricksAdapter()
    base_def = adapter.parse_definition(base_yaml, "cat.sch.metric_view", "baseline")
    base_def.not_deployed_objects = ["remediated_measure"]

    live_def = adapter.parse_definition(live_yaml, "cat.sch.metric_view", "live")

    findings = compare_views(baseline=base_def, live=live_def)
    assert len(findings) == 0, f"Expected deletion drift to be suppressed, got {findings}"


def test_databricks_baseline_store_roundtrip():
    """Verify saving and retrieving a Databricks baseline in the database."""
    yaml_text = """
    version: 1.1
    comment: Databricks Baseline
    measures:
      - name: orders_total
        expr: count(order_key)
        comment: Count of orders
    """
    save_deploy_baseline(
        project_id="proj-databricks-demo",
        view_name="analytics.sales_mv",
        definition_text=yaml_text,
        platform="DATABRICKS",
        not_deployed_objects=["skipped_kpi"],
    )

    baseline = get_active_baseline("proj-databricks-demo", "analytics.sales_mv")
    assert baseline is not None
    assert baseline["platform"] == "DATABRICKS"
    assert baseline["version"] == 1
    assert "skipped_kpi".upper() in baseline["not_deployed_objects"]

    adapter = get_adapter(baseline["platform"])
    view_def = adapter.parse_definition(
        raw_text=baseline["definition_text"],
        view_name="analytics.sales_mv",
        source="baseline",
    )
    assert len(view_def.objects) == 1
    assert view_def.objects[0].name == "orders_total"
    assert view_def.objects[0].expression == "count(order_key)"


def test_databricks_malformed_yaml_raises_unparseable_definition():
    """Malformed YAML raises ValueError with 'not checked: unparseable definition'."""
    adapter = DatabricksAdapter()
    bad_yaml = """
    version: 1.1
    measures:
      - name: broken
        expr: [unclosed list
    """
    with pytest.raises(ValueError, match="not checked: unparseable definition"):
        adapter.parse_definition(bad_yaml, "catalog.schema.broken_mv")


def test_databricks_fetch_auth_error_401():
    """HTTP 401 raises RuntimeError with 'warehouse unavailable: authentication failed'."""
    import sys
    from unittest.mock import MagicMock, patch
    adapter = DatabricksAdapter()
    mock_db_sql = MagicMock()
    mock_db_sql.connect.side_effect = Exception("HTTP 401 Unauthorized: Invalid access token")
    mock_databricks = MagicMock()
    mock_databricks.sql = mock_db_sql
    with patch.dict(sys.modules, {"databricks": mock_databricks, "databricks.sql": mock_db_sql}):
        with pytest.raises(RuntimeError, match="warehouse unavailable: authentication failed"):
            adapter.fetch_definition_text(
                view_name="cat.sch.view_a",
                connection_config={"host": "dbc-test.cloud.databricks.com", "token": "dapi_fake"},
            )


def test_databricks_roundtrip_applied_vs_fetched_zero_findings():
    """Roundtrip: Applied YAML and fetched YAML produce zero drift findings."""
    adapter = DatabricksAdapter()
    yaml_text = """
    version: 1.1
    source: `catalog`.`schema`.`orders`
    dimensions:
      - name: order_status
        expr: status_col
        comment: Order status
    measures:
      - name: gross_sales
        expr: sum(price * qty)
        comment: Gross revenue
    """
    view_name = "catalog.schema.orders_metric_view"
    base_def = adapter.parse_definition(yaml_text, view_name, source="baseline")
    live_def = adapter.parse_definition(yaml_text, view_name, source="live")

    findings = compare_views(baseline=base_def, live=live_def)
    assert len(findings) == 0


def test_databricks_all_four_drift_categories():
    """Verify all 4 drift categories (ADDITION, DELETION, EXPRESSION_DRIFT, DESCRIPTION_DRIFT) on Databricks."""
    adapter = DatabricksAdapter()
    view_name = "catalog.schema.finance_mv"

    base_yaml = """
    version: 1.1
    measures:
      - name: metric_to_delete
        expr: count(id)
      - name: metric_expr_drift
        expr: sum(amount)
        comment: Original formula
      - name: metric_desc_drift
        expr: avg(cost)
        comment: Baseline documentation
    """

    live_yaml = """
    version: 1.1
    measures:
      - name: metric_expr_drift
        expr: sum(amount * 1.05)
        comment: Original formula
      - name: metric_desc_drift
        expr: avg(cost)
        comment: Updated documentation in warehouse
      - name: metric_new_addition
        expr: count(distinct customer_id)
        comment: Added directly in warehouse
    """

    base_def = adapter.parse_definition(base_yaml, view_name, source="baseline")
    live_def = adapter.parse_definition(live_yaml, view_name, source="live")

    findings = compare_views(baseline=base_def, live=live_def)
    cats = {f.category: f for f in findings}

    # Reason: Databricks identifiers are lower-case; findings preserve platform-reported
    # casing and matching is case-insensitive.
    assert DriftCategory.DELETION in cats
    assert cats[DriftCategory.DELETION].name.lower() == "metric_to_delete"

    assert DriftCategory.ADDITION in cats
    assert cats[DriftCategory.ADDITION].name.lower() == "metric_new_addition"

    assert DriftCategory.EXPRESSION_DRIFT in cats
    assert cats[DriftCategory.EXPRESSION_DRIFT].name.lower() == "metric_expr_drift"

    assert DriftCategory.DESCRIPTION_DRIFT in cats
    assert cats[DriftCategory.DESCRIPTION_DRIFT].name.lower() == "metric_desc_drift"


def test_databricks_missing_config_raises_structured_error():
    """Missing catalog or schema raises structured error with MISSING_WAREHOUSE_CONFIG and ignores os.environ."""
    import os
    from unittest.mock import patch

    adapter = DatabricksAdapter()

    # 1. When catalog and schema are missing and cannot be resolved from view_name
    with pytest.raises(ValueError, match="MISSING_WAREHOUSE_CONFIG"):
        adapter._resolve_connection_config({"host": "dbc.databricks.com", "token": "token123"}, view_name="unqualified_view")

    # 2. When os.environ defines catalog/schema, adapter strictly ignores them
    with patch.dict(os.environ, {"DATABRICKS_CATALOG": "fake_cat", "DATABRICKS_SCHEMA": "fake_sch"}):
        with pytest.raises(ValueError, match="MISSING_WAREHOUSE_CONFIG"):
            adapter._resolve_connection_config({"host": "dbc.databricks.com", "token": "token123"}, view_name="unqualified_view")

    # 3. Correct 3-part view_name resolves catalog and schema
    cfg = adapter._resolve_connection_config({"host": "dbc.databricks.com", "token": "token123"}, view_name="my_cat.my_sch.my_view")
    assert cfg["catalog"] == "my_cat"
    assert cfg["schema"] == "my_sch"


def test_databricks_unparseable_sql_raises_not_checked():
    """Unparseable SQL view DDL raises ValueError with 'not checked: unparseable definition'."""
    adapter = DatabricksAdapter()
    broken_sql = "CREATE VIEW broken AS SELECT &&& $$$ FROM WHERE"
    with pytest.raises(ValueError, match="not checked: unparseable definition"):
        adapter.parse_definition(broken_sql, view_name="cat.sch.broken")


