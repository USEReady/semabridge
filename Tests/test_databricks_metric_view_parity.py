"""Parity SQL for a Databricks project's deployed metric view."""
from __future__ import annotations

from semabridge.api.routers import reconciliation_router as rr
from semabridge.reconciliation import databricks_metric_view as dmv

VIEW = "workspace.default.Competitive_Marketing_Analysis_More_Synonyms_updated_metric_view"
COLUMNS = ["Manufacturer", "Product_Category", "Sales_Fact_PRODUCT_KEY", "Total_OTHER_Units_YTD", "Total_Units"]


def test_names_resolve_against_view_columns():
    measure, dims = dmv.resolve_names("Total_OTHER_Units_YTD", ["PRODUCT.MANUFACTURER", "Sales_Fact.PRODUCT_KEY"], COLUMNS)
    assert measure == "Total_OTHER_Units_YTD"
    assert dims == ["Manufacturer", "Sales_Fact_PRODUCT_KEY"]
    assert dmv.resolve_measure("Total Units", COLUMNS) == "Total_Units"


def test_names_fall_back_without_columns():
    measure, dims = dmv.resolve_names("Total Units", ["PRODUCT.MANUFACTURER"], [])
    assert (measure, dims) == ("Total_Units", ["MANUFACTURER"])


def test_project_view_detection():
    assert dmv.is_project_metric_view("p", VIEW)
    assert dmv.is_project_metric_view("p", "cat.sch.sales_view", ["cat.sch.SALES_VIEW"])
    assert not dmv.is_project_metric_view("p", "cat.sch.raw_sales", ["cat.sch.sales_view"])


def _gen(monkeypatch, kind, view=VIEW, columns=COLUMNS):
    from semabridge.ai import bi_truth_service

    monkeypatch.setattr(bi_truth_service, "_detect_project_target", lambda pid: "DATABRICKS")
    monkeypatch.setattr(rr, "_validate_generate_queries_request", lambda req, kind: None)
    monkeypatch.setattr(dmv, "project_deployed_views", lambda pid: [VIEW])
    monkeypatch.setattr(dmv, "describe_columns", lambda pid, v, owner_id=None: columns)
    return rr.generate_reconciliation_queries(rr.GenerateQueriesRequest(
        metric_name="Total_OTHER_Units_YTD",
        metric_sql_expression='SUM(CASE WHEN PRODUCT."ISVANARSDEL" = \'No\' THEN SALESFACT."UNITS" END)',
        target_table_or_view=view, target_kind=kind,
        dimensions=["PRODUCT.MANUFACTURER"], project_id="proj-nat1",
    ))


def test_table_kind_on_deployed_metric_view_still_uses_measure(monkeypatch):
    out = _gen(monkeypatch, "table")
    sql = out["sql_query"]
    assert out["target_kind"] == "metric_view"
    assert "MEASURE(`Total_OTHER_Units_YTD`)" in sql
    assert "`Manufacturer`" in sql
    assert '"' not in sql  # no Snowflake-quoted identifiers


def test_plain_table_on_databricks_is_left_alone(monkeypatch):
    out = _gen(monkeypatch, "table", view="workspace.default.raw_sales")
    assert out["target_kind"] == "table"
    assert "MEASURE(" not in out["sql_query"]


def test_snowflake_semantic_view_uses_table_metric_not_the_formula(monkeypatch):
    from types import SimpleNamespace

    from semabridge.ai import bi_truth_service

    monkeypatch.setattr(bi_truth_service, "_detect_project_target", lambda pid: "SNOWFLAKE")
    dom = SimpleNamespace(
        concepts=[SimpleNamespace(name="SALES", logical_name="sales", attributes=[])],
        metrics=[SimpleNamespace(name="Regular_Sales_Dollars", logical_name="regular_sales_dollars",
                                 label=None, concept="SALES")],
    )
    monkeypatch.setattr(rr, "_get_project_domains_for_validation", lambda pid: [dom])
    out = rr.generate_reconciliation_queries(rr.GenerateQueriesRequest(
        metric_name="Regular_Sales_Dollars",
        metric_sql_expression='SUM(SALES."SUM_REGULAR_SALES_DOLLARS")',
        target_table_or_view="DB.SCH.STORE_SALES_SEMANTIC", target_kind="semantic_view",
        project_id="proj-sf",
    ))
    assert "sales.regular_sales_dollars" in out["sql_query"]
    assert "SUM(" not in out["sql_query"]


def test_double_qualified_view_is_cleaned(monkeypatch):
    out = _gen(monkeypatch, "table", view='main.semabridge."' + VIEW + '"')
    assert "`main`" not in out["sql_query"]
    assert "MEASURE(`Total_OTHER_Units_YTD`)" in out["sql_query"]
