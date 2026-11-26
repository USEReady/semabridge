"""Databricks metric-view planner (Phase 3.6): exact-outcome tests.

Each case builds a small model (tables, relationships, measures with their
DAX and the converter-style SQL) and checks where every measure goes, or why
it is left out. The cases are the worked examples from the design reviews
plus the baseline's minimum acceptance cases.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import pytest

from semabridge.sml.models import (
    Cardinality,
    CrossFilterDirection,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)
from semabridge.target_backends.databricks.planner import (
    Capability,
    PlannerError,
    PlannerSettings,
    plan_metric_views,
)
from semabridge.target_backends.databricks.planner import sqltext
from semabridge.target_backends.databricks.tables import TableBinding, TablePlan, UcTable, rewrite_measure_columns

CALC = "calculate_filter_semantics_difference"
B_ON = frozenset({Capability.ONE_TO_MANY_JOINS})


# --- model builders --------------------------------------------------------------------------


def col(name: str, hidden: bool = False, calculated: bool = False) -> SMLColumn:
    return SMLColumn(unique_name=name, label=name, is_hidden=hidden, is_calculated=calculated)


def table(name: str, *cols, fact: bool = False) -> SMLDataset:
    return SMLDataset(unique_name=name, label=name, columns=[c if isinstance(c, SMLColumn) else col(c) for c in cols],
                      is_fact=fact)


def rel(many: str, many_col: str, one: str, one_col: str, active: bool = True, card: str = "many-to-one",
        both: bool = False) -> SMLRelationship:
    return SMLRelationship(
        unique_name=f"{many}_{one}_{many_col}", from_dataset=many, from_columns=[many_col], to_dataset=one,
        to_columns=[one_col], cardinality=Cardinality(card), is_active=active,
        cross_filter=CrossFilterDirection.BOTH if both else CrossFilterDirection.SINGLE,
    )


def measure(name: str, home: str, dax: str, sql: Optional[str], flags: Sequence[str] = (), ok: bool = True) -> SMLMetric:
    return SMLMetric(unique_name=name, label=name, dataset=home, expression=dax, sql_expression=sql,
                     sync_enabled=ok and sql is not None, advisory_categories=list(flags),
                     sync_failure_reason=None if ok else "declined by the converter")


def model(tables: List[SMLDataset], rels: List[SMLRelationship], measures: List[SMLMetric]) -> SMLModel:
    return SMLModel(unique_name="m", datasets=tables, relationships=rels, metrics=measures)


def store_sales(measures: List[SMLMetric], extra_rels: Sequence[SMLRelationship] = ()) -> SMLModel:
    return model(
        [table("Store", "StoreID", "StoreName", "Capacity", "RegionID"),
         table("Sales", "SaleID", "StoreID", "ProductID", "Amount", "Qty", fact=True),
         table("Product", "ProductID", "Category", "Price")],
        [rel("Sales", "StoreID", "Store", "StoreID"), rel("Sales", "ProductID", "Product", "ProductID"), *extra_rels],
        measures,
    )


REVENUE = measure("Total Revenue", "Sales", "SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`::DOUBLE)")
CAPACITY = measure("Total Capacity", "Store", "SUM(Store[Capacity])", "SUM(STORE.`CAPACITY`::DOUBLE)")
STORES = measure("Total Stores", "Store", "COUNTROWS(Store)", "COUNT(*)")


def placed_in(result, name: str) -> Optional[str]:
    hit = result.placed(name)
    return hit[0].name if hit else None


def reason(result, name: str) -> Optional[str]:
    lo = result.left_out_reason(name)
    return lo.reason if lo else None


# --- Placement A -----------------------------------------------------------------------------


def test_sum_over_fact_goes_to_fact_view_with_store_dimension():
    r = plan_metric_views(store_sales([REVENUE]))
    assert placed_in(r, "Total Revenue") == "sales_metrics"
    v = r.view("sales_metrics")
    assert {j.dataset for j in v.joins} == {"Store", "Product"}
    assert r.placed("Total Revenue")[1].expr == "SUM(source.`AMOUNT`::DOUBLE)"


def test_store_aggregates_use_their_own_population():
    mx = measure("Max Capacity", "Sales", "MAX(Store[Capacity])", "MAX(STORE.`CAPACITY`::DOUBLE)")
    dc = measure("Store Names", "Sales", "DISTINCTCOUNT(Store[StoreName])", "COUNT(DISTINCT STORE.`STORENAME`)")
    r = plan_metric_views(store_sales([CAPACITY, mx, dc, STORES]))
    for name in ("Total Capacity", "Max Capacity", "Store Names", "Total Stores"):
        assert placed_in(r, name) == "store_metrics", name  # grain, not home table


def test_sumx_with_related_is_one_grain():
    m = measure("Revenue X", "Sales", "SUMX(Sales, Sales[Qty] * RELATED(Product[Price]))",
                "SUM(SALES.`QTY`::DOUBLE * PRODUCT.`PRICE`::DOUBLE)")
    r = plan_metric_views(store_sales([m]))
    view, plan = r.placed("Revenue X")
    assert view.name == "sales_metrics"
    assert plan.expr == "SUM(source.`QTY`::DOUBLE * `product`.`PRICE`::DOUBLE)"


def test_countrows_erased_table_is_taken_from_the_tree():
    r = plan_metric_views(store_sales([STORES, measure("Number of Sales", "Store", "COUNTROWS(Sales)", "COUNT(*)")]))
    assert placed_in(r, "Total Stores") == "store_metrics"
    assert placed_in(r, "Number of Sales") == "sales_metrics"


def test_tree_and_sql_disagreeing_is_left_out():
    m = measure("Odd", "Sales", "COUNTA(Sales[SaleID])", "COUNT(*)")
    r = plan_metric_views(store_sales([m]))
    assert reason(r, "Odd") == "analysis_mismatch"
    m2 = measure("Odd2", "Sales", "SUM(Sales[Amount])", "SUM(STORE.`CAPACITY`)")
    assert reason(plan_metric_views(store_sales([m2])), "Odd2") == "analysis_mismatch"


# --- mixed grain and Placement B -------------------------------------------------------------


PLUS = measure("Revenue Plus Capacity", "Sales", "SUM(Sales[Amount]) + SUM(Store[Capacity])",
               "CASE WHEN (SUM(SALES.`AMOUNT`::DOUBLE)) IS NULL AND (SUM(STORE.`CAPACITY`::DOUBLE)) IS NULL THEN NULL "
               "ELSE COALESCE((SUM(SALES.`AMOUNT`::DOUBLE)), 0) + COALESCE((SUM(STORE.`CAPACITY`::DOUBLE)), 0) END")
PER_STORE = measure("Revenue Per Store", "Sales", "DIVIDE(SUM(Sales[Amount]), COUNTROWS(Store))",
                    "(SUM(SALES.`AMOUNT`::DOUBLE)) / NULLIF((COUNT(*)), 0)")


def test_mixed_grain_is_left_out_until_composition_is_proven():
    r = plan_metric_views(store_sales([REVENUE, CAPACITY, PLUS]))
    lo = r.left_out_reason("Revenue Plus Capacity")
    assert lo.reason == "mixed_grain_needs_composition"
    assert any("Sales" in c for c in lo.components) and any("Store" in c for c in lo.components)
    assert placed_in(r, "Total Revenue") == "sales_metrics" and placed_in(r, "Total Capacity") == "store_metrics"


def test_revenue_plus_capacity_composed_in_store_view():
    r = plan_metric_views(store_sales([PLUS]), settings=PlannerSettings(capabilities=B_ON))
    view, plan = r.placed("Revenue Plus Capacity")
    assert view.name == "store_metrics" and plan.placement == "B" and view.version == "1.1"
    sales = next(j for j in view.joins if j.dataset == "Sales")
    assert sales.cardinality == "one_to_many" and sales.parent == "source"
    assert sales.on == [("STOREID", "STOREID")]
    assert "`sales`.`AMOUNT`" in plan.expr and "source.`CAPACITY`" in plan.expr
    assert "IS NULL AND" in plan.expr  # both blank stays blank (converter's DAX handling kept)
    assert not any(f.path == "sales" for f in view.fields)  # no fields from one-to-many tables


def test_revenue_per_store_counts_root_rows():
    r = plan_metric_views(store_sales([PER_STORE]), settings=PlannerSettings(capabilities=B_ON))
    view, plan = r.placed("Revenue Per Store")
    assert view.name == "store_metrics"
    assert "COUNT(*)" in plan.expr  # root rows = stores, Gamma included
    assert "NULLIF" in plan.expr     # DIVIDE's zero-denominator handling kept


def test_child_row_count_uses_join_key_marker():
    m = measure("Sales per Capacity", "Store", "DIVIDE(COUNTROWS(Sales), SUM(Store[Capacity]))",
                "(COUNT(*)) / NULLIF((SUM(STORE.`CAPACITY`::DOUBLE)), 0)")
    r = plan_metric_views(store_sales([m]), settings=PlannerSettings(capabilities=B_ON))
    view, plan = r.placed("Sales per Capacity")
    assert view.name == "store_metrics"
    assert plan.expr.startswith("(NULLIF(COUNT(`sales`.`STOREID`), 0))")


def test_budget_variance_composes_under_shared_dimension():
    mdl = model(
        [table("Calendar", "Month"), table("Sales", "SaleID", "Month", "Amount"), table("Budget", "BudgetID", "Month", "BudgetAmount")],
        [rel("Sales", "Month", "Calendar", "Month"), rel("Budget", "Month", "Calendar", "Month")],
        [measure("Budget Variance", "Sales", "SUM(Sales[Amount]) - SUM(Budget[BudgetAmount])",
                 "SUM(SALES.`AMOUNT`::DOUBLE) - SUM(BUDGET.`BUDGETAMOUNT`::DOUBLE)")],
    )
    assert reason(plan_metric_views(mdl), "Budget Variance") == "mixed_grain_needs_composition"
    view, plan = plan_metric_views(mdl, settings=PlannerSettings(capabilities=B_ON)).placed("Budget Variance")
    assert view.name == "calendar_metrics"
    assert {(j.dataset, j.cardinality) for j in view.joins} == {("Sales", "one_to_many"), ("Budget", "one_to_many")}


def test_several_shared_roots_are_declined():
    mdl = model(
        [table("Calendar", "Month"), table("Product", "ProductID"),
         table("Sales", "Month", "ProductID", "Amount"), table("Budget", "Month", "ProductID", "BudgetAmount")],
        [rel("Sales", "Month", "Calendar", "Month"), rel("Budget", "Month", "Calendar", "Month"),
         rel("Sales", "ProductID", "Product", "ProductID"), rel("Budget", "ProductID", "Product", "ProductID")],
        [measure("Variance", "Sales", "SUM(Sales[Amount]) - SUM(Budget[BudgetAmount])",
                 "SUM(SALES.`AMOUNT`) - SUM(BUDGET.`BUDGETAMOUNT`)")],
    )
    lo = plan_metric_views(mdl, settings=PlannerSettings(capabilities=B_ON)).left_out_reason("Variance")
    assert lo.reason == "ambiguous_composition_root" and "Calendar" in lo.detail and "Product" in lo.detail


def test_two_facts_never_joined_directly():
    mdl = model([table("Orders", "OrderID", "Month"), table("Returns", "ReturnID", "Month")], [],
                [measure("Return Rate", "Orders", "DIVIDE(COUNTROWS(Returns), COUNTROWS(Orders))",
                         "(COUNT(*)) / NULLIF((COUNT(*)), 0)")])
    r = plan_metric_views(mdl, settings=PlannerSettings(capabilities=B_ON))
    assert reason(r, "Return Rate") in ("needs_composition", "count_attribution_ambiguous")
    assert not r.views


def test_mixed_view_needs_its_own_proof():
    s = PlannerSettings(capabilities=B_ON)
    r = plan_metric_views(store_sales([CAPACITY, PLUS]), settings=s)
    assert placed_in(r, "Total Capacity") == "store_metrics"
    assert reason(r, "Revenue Plus Capacity") == "mixed_view_unproven"
    s2 = PlannerSettings(capabilities=B_ON | {Capability.MIXED_ROOT_VIEWS})
    r2 = plan_metric_views(store_sales([CAPACITY, PLUS]), settings=s2)
    assert placed_in(r2, "Revenue Plus Capacity") == "store_metrics"
    assert placed_in(r2, "Total Capacity") == "store_metrics"


# --- CALCULATE, filters, dependencies ---------------------------------------------------------


SALES_A = measure("Sales A", "Sales", 'CALCULATE([Total Revenue], Product[Category] = "A")',
                  "SUM(CASE WHEN PRODUCT.`CATEGORY` = 'A' THEN SALES.`AMOUNT`::DOUBLE ELSE NULL END)", [CALC])
SALES_A_PCT = measure("Sales A %", "Sales", "DIVIDE([Sales A], [Total Revenue])",
                      "(SUM(CASE WHEN PRODUCT.`CATEGORY` = 'A' THEN SALES.`AMOUNT`::DOUBLE ELSE NULL END)) / "
                      "NULLIF((SUM(SALES.`AMOUNT`::DOUBLE)), 0)")


def test_approximate_is_default_and_propagates_with_warning():
    r = plan_metric_views(store_sales([REVENUE, SALES_A, SALES_A_PCT]))
    a = r.placed("Sales A")[1]
    pct = r.placed("Sales A %")[1]
    assert a.semantic_status == "approximate" and a.comment and "CALCULATE" in a.comment
    assert pct.semantic_status == "approximate"
    assert any(c["kind"] == "conflicting_filter" and c["measure"] == pct.name for c in r.view("sales_metrics").checks)
    assert "`product`.`CATEGORY` = 'A'" in a.expr


def test_strict_leaves_out_flagged_measure_and_dependants_with_root_cause():
    r = plan_metric_views(store_sales([REVENUE, SALES_A, SALES_A_PCT]), settings=PlannerSettings(calculate_mode="strict"))
    assert reason(r, "Sales A") == "filter_semantics_differ"
    lo = r.left_out_reason("Sales A %")
    assert lo.reason == "depends_on" and "Sales A" in lo.root_cause
    assert placed_in(r, "Total Revenue") == "sales_metrics"


def test_user_excluded_also_excludes_dependants():
    """Integration batch 2 (A6): unticking a measure leaves out every measure
    built on it, with the unticked measure as the root cause (the earlier rule
    kept dependants placed)."""
    r = plan_metric_views(store_sales([REVENUE, SALES_A, SALES_A_PCT]),
                          settings=PlannerSettings(excluded_measures=("Sales A",)))
    assert reason(r, "Sales A") == "user_excluded"
    assert reason(r, "Sales A %") == "depends_on"
    lo = next(x for x in r.left_out if x.measure == "Sales A %")
    assert "Sales A" in (lo.root_cause or "")
    assert placed_in(r, "Total Revenue") == "sales_metrics"


def test_filter_removal_is_declined_in_both_modes():
    m = measure("All Product Sales", "Sales", "CALCULATE([Total Revenue], REMOVEFILTERS(Product))",
                "SUM(SALES.`AMOUNT`::DOUBLE)", [CALC])
    for mode in ("approximate", "strict"):
        r = plan_metric_views(store_sales([REVENUE, m]), settings=PlannerSettings(calculate_mode=mode))
        assert reason(r, "All Product Sales") in ("filter_removal_unsupported", "filter_semantics_differ")
    assert reason(plan_metric_views(store_sales([REVENUE, m])), "All Product Sales") == "filter_removal_unsupported"


def test_userelationship_and_unknown_functions_are_declined():
    u = measure("Ship Sales", "Sales", "CALCULATE([Total Revenue], USERELATIONSHIP(Sales[StoreID], Store[StoreID]))",
                "SUM(SALES.`AMOUNT`::DOUBLE)", [CALC])
    x = measure("Weird", "Sales", "PERCENTILEX.INC(Sales, Sales[Amount], 0.5)", "PERCENTILE(SALES.`AMOUNT`, 0.5)")
    r = plan_metric_views(store_sales([REVENUE, u, x]))
    assert reason(r, "Ship Sales") == "unrecognised_pattern"
    assert reason(r, "Weird") == "unrecognised_pattern"


def test_failed_conversion_and_cycles():
    bad = measure("Bad", "Sales", "SUM(Sales[Amount])", None, ok=False)
    a = measure("A", "Sales", "[B] + 1", "x")
    b = measure("B", "Sales", "[A] + 1", "x")
    r = plan_metric_views(store_sales([bad, a, b]))
    assert reason(r, "Bad") == "conversion_failed"
    assert reason(r, "A") == "dependency_cycle" and reason(r, "B") == "dependency_cycle"


# --- constants ------------------------------------------------------------------------------


TARGET = measure("New Stores Target", "Store", "14", "MAX(14)")


def test_constant_goes_to_home_view_with_zero_row_warning():
    r = plan_metric_views(store_sales([TARGET]))
    view, plan = r.placed("New Stores Target")
    assert view.name == "store_metrics" and plan.expr == "MAX(14)" and plan.semantic_status == "approximate"
    assert any(c["kind"] == "zero_row_behaviour" for c in view.checks)


def test_constant_in_strict_and_with_coalesce():
    assert reason(plan_metric_views(store_sales([TARGET]), settings=PlannerSettings(calculate_mode="strict")),
                  "New Stores Target") == "constant_zero_row"
    s = PlannerSettings(capabilities=frozenset({Capability.CONSTANT_COALESCE}))
    plan = plan_metric_views(store_sales([TARGET]), settings=s).placed("New Stores Target")[1]
    assert plan.expr == "COALESCE(MAX(14), 14)" and plan.semantic_status == "converted"


# --- joins, fields, names ---------------------------------------------------------------------


def test_ambiguous_region_blocks_only_region():
    mdl = model(
        [table("Sales", "CustomerID", "StoreID", "Amount"), table("Customer", "CustomerID", "RegionID"),
         table("Store", "StoreID", "RegionID"), table("Region", "RegionID", "Name")],
        [rel("Sales", "CustomerID", "Customer", "CustomerID"), rel("Sales", "StoreID", "Store", "StoreID"),
         rel("Customer", "RegionID", "Region", "RegionID"), rel("Store", "RegionID", "Region", "RegionID")],
        [REVENUE, measure("North Sales", "Sales", 'CALCULATE([Total Revenue], Region[Name] = "North")',
                          "SUM(CASE WHEN REGION.`NAME` = 'North' THEN SALES.`AMOUNT`::DOUBLE END)", [CALC])],
    )
    r = plan_metric_views(mdl)
    v = r.view("sales_metrics")
    assert placed_in(r, "Total Revenue") == "sales_metrics"
    assert "Region" not in {j.dataset for j in v.joins}
    assert {"Customer", "Store"} <= {j.dataset for j in v.joins}
    assert reason(r, "North Sales") == "ambiguous_path"


def test_inactive_and_two_way_relationships_are_noted():
    mdl = store_sales([REVENUE], extra_rels=[rel("Sales", "ProductID", "Store", "StoreID", active=False)])
    r = plan_metric_views(mdl)
    assert any("inactive" in n for n in r.notes)
    mdl2 = model([table("Sales", "StoreID", "Amount"), table("Store", "StoreID")],
                 [rel("Sales", "StoreID", "Store", "StoreID", both=True)], [REVENUE])
    r2 = plan_metric_views(mdl2)
    assert any("both ways" in n for n in r2.notes) and any("both ways" in n for n in r2.view("sales_metrics").notes)


def test_hidden_and_calculated_columns():
    mdl = model([table("Sales", "StoreID", col("Secret", hidden=True), col("Profit", calculated=True), "Amount")], [],
                [REVENUE, measure("Profit Sum", "Sales", "SUM(Sales[Profit])", "SUM(SALES.`PROFIT`::DOUBLE)")])
    # no table plan: a calculated column can't be read
    r = plan_metric_views(mdl)
    names = {f.column for f in r.view("sales_metrics").fields}
    assert "Secret" not in names and "Profit" not in names and "Amount" in names
    assert r.placed("Profit Sum")[1].source_verification == "unverified"
    # bound table with a real PROFIT column: kept, unverified
    uc = UcTable("workspace", "src", "sales", columns={"STOREID": ("StoreID", "int"), "AMOUNT": ("Amount", "double"),
                                                         "PROFIT": ("Profit", "double"), "SECRET": ("Secret", "int")})
    b = TableBinding("Sales", "SALES", "bind", uc, "name", column_map={"STOREID": "StoreID", "AMOUNT": "Amount",
                                                                        "SECRET": "Secret", "PROFIT": "Profit"})
    r2 = plan_metric_views(mdl, TablePlan(bindings=[b]))
    profit = next(f for f in r2.view("sales_metrics").fields if f.column == "Profit")
    assert profit.source_verification == "unverified" and profit.expr == "source.`Profit`"
    assert r2.view("sales_metrics").source_table == "workspace.src.sales"


def test_stub_tables_mark_readiness_per_object():
    mdl = store_sales([REVENUE, CAPACITY])
    from semabridge.target_backends.databricks.tables import plan_tables

    uc = {("w", "s"): {"SALES": UcTable("w", "s", "sales", columns={"SALEID": ("SaleID", "BIGINT"), "STOREID": ("StoreID", "BIGINT"),
                                          "PRODUCTID": ("ProductID", "BIGINT"), "AMOUNT": ("Amount", "DOUBLE"),
                                          "QTY": ("Qty", "BIGINT")}),
                       "PRODUCT": UcTable("w", "s", "product", columns={"PRODUCTID": ("ProductID", "BIGINT"), "CATEGORY": ("Category", "STRING"),
                                              "PRICE": ("Price", "DOUBLE")})}}
    r = plan_metric_views(mdl, plan_tables(mdl, uc, source_schemas=[("w", "s")], on_missing_source="stub"))
    assert r.placed("Total Revenue")[1].readiness == "real"
    assert r.placed("Total Capacity")[1].readiness == "structure_only"
    sales = r.view("sales_metrics")
    assert next(j for j in sales.joins if j.dataset == "Store").readiness == "structure_only"
    assert any("partly stub" in n for n in sales.notes)


def test_skipped_table_leaves_out_measures_that_need_it():
    from semabridge.target_backends.databricks.tables import plan_tables

    mdl = store_sales([REVENUE, CAPACITY])
    uc = {("w", "s"): {"SALES": UcTable("w", "s", "sales", columns={"SALEID": ("SaleID", "BIGINT"), "STOREID": ("StoreID", "BIGINT"),
                                          "PRODUCTID": ("ProductID", "BIGINT"), "AMOUNT": ("Amount", "DOUBLE"),
                                          "QTY": ("Qty", "BIGINT")}),
                       "PRODUCT": UcTable("w", "s", "product", columns={"PRODUCTID": ("ProductID", "BIGINT"), "CATEGORY": ("Category", "STRING"),
                                              "PRICE": ("Price", "DOUBLE")})}}
    r = plan_metric_views(mdl, plan_tables(mdl, uc, source_schemas=[("w", "s")], on_missing_source="skip"))
    assert reason(r, "Total Capacity") == "table_skipped"
    assert "Store" not in {j.dataset for j in r.view("sales_metrics").joins}


def test_name_clashes_and_reserved_names():
    mdl = model(
        [table("Sales", "DistrictID", "StoreID", "Amount"), table("Store", "StoreID", "DistrictID"),
         table("District", "DistrictID", "Name")],
        [rel("Sales", "StoreID", "Store", "StoreID"), rel("Store", "DistrictID", "District", "DistrictID")],
        [REVENUE, measure("Amount", "Sales", "SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`::DOUBLE)")],
    )
    r = plan_metric_views(mdl)
    v = r.view("sales_metrics")
    names = [x.name for x in v.fields + v.measures]
    assert len(names) == len({n.upper() for n in names})
    by = {(f.dataset, f.column): f.name for f in v.fields}
    assert by[("Sales", "DistrictID")] == "district_id"          # nearest the source keeps it
    assert by[("Store", "DistrictID")] == "store_district_id"
    assert by[("District", "DistrictID")] == "district_district_id"
    assert by[("Sales", "Amount")] == "sales_amount"              # the measure keeps amount
    assert {m.name for m in v.measures} == {"total_revenue", "amount"}
    # resync: an already deployed name stays with its source object (structured identity)
    from semabridge.target_backends.databricks.planner import SourceId

    reserved = {"Sales": {SourceId("field", "Store", "DistrictID"): "district_id"}}
    r2 = plan_metric_views(mdl, settings=PlannerSettings(reserved_names=reserved))
    by2 = {(f.dataset, f.column): f.name for f in r2.view("sales_metrics").fields}
    assert by2[("Store", "DistrictID")] == "district_id"
    assert by2[("Sales", "DistrictID")] == "sales_district_id"


def test_view_modes():
    mdl = store_sales([REVENUE, CAPACITY])
    with pytest.raises(PlannerError):
        plan_metric_views(mdl, settings=PlannerSettings(view_mode="primary_fact_only"))
    r = plan_metric_views(mdl, settings=PlannerSettings(view_mode="primary_fact_only", primary_fact="Sales"))
    assert [v.name for v in r.views] == ["sales_metrics"] and reason(r, "Total Capacity") == "view_not_selected"


def test_settings_validation_and_result_dict():
    with pytest.raises(PlannerError):
        PlannerSettings(calculate_mode="loose")
    with pytest.raises(PlannerError):
        PlannerSettings(capabilities=frozenset({"teleport"}))
    d = plan_metric_views(store_sales([REVENUE, PLUS, CAPACITY])).as_dict()
    assert d["summary"]["views"] == 2 and d["summary"]["measures_left_out"] == 1
    assert d["left_out"][0]["reason"] == "mixed_grain_needs_composition"
    assert any(n.startswith("security:") for n in d["notes"])


# --- SQL text helpers (string literals are never rewritten) -----------------------------------


def test_string_literals_are_left_alone():
    sql = "SUM(CASE WHEN SALES.`NOTE` = 'SALES.`AMOUNT`' THEN SALES.`AMOUNT` END)"
    assert sqltext.aliases(sql) == {"SALES"}
    assert sqltext.count_star("COUNT(*) + LENGTH('COUNT(*)')") == 1
    out = rewrite_measure_columns(sql, {"SALES": {"AMOUNT": "amount_real", "NOTE": "note_real"}})
    assert out == "SUM(CASE WHEN SALES.`note_real` = 'SALES.`AMOUNT`' THEN SALES.`amount_real` END)"


def test_composed_measure_built_from_measure_references():
    per_store = measure("Revenue Per Store", "Sales", "DIVIDE([Total Revenue], [Total Stores])",
                        "(SUM(SALES.`AMOUNT`::DOUBLE)) / NULLIF((COUNT(*)), 0)")
    s = PlannerSettings(capabilities=B_ON | {Capability.MIXED_ROOT_VIEWS})
    r = plan_metric_views(store_sales([REVENUE, STORES, per_store]), settings=s)
    view, plan = r.placed("Revenue Per Store")
    assert view.name == "store_metrics" and plan.placement == "B"
    assert plan.depends_on == ["Total Revenue", "Total Stores"]
    assert placed_in(r, "Total Stores") == "store_metrics" and placed_in(r, "Total Revenue") == "sales_metrics"


def test_engine_settings_from_behavior_and_project_override():
    from types import SimpleNamespace

    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.engine.targets.databricks import databricks_planner_settings

    behavior = ConnectorBehavior()
    assert behavior.databricks.planner_calculate_mode == "approximate"
    ctx = SimpleNamespace(behavior=behavior, config=SimpleNamespace(target=SimpleNamespace(
        planner={"calculate_mode": "strict", "excluded_measures": ["Sales A"]})))
    s = databricks_planner_settings(ctx)
    assert s.calculate_mode == "strict" and s.excluded_measures == ("Sales A",) and s.capabilities == frozenset()
    assert databricks_planner_settings(SimpleNamespace(config=SimpleNamespace(target=None))).calculate_mode == "approximate"


def test_real_model_patterns_countax_keepfilters_and_row_functions():
    """Shapes seen in the Artificial Intelligence sample."""
    opp = table("Opportunities", "OpportunitySeq", "Status", "PipelineStep", "Value")
    ms = [
        measure("Opportunity Count", "Opportunities", "COUNTAX(Opportunities, TRUE())", "COUNT(*)"),
        measure("Count of Won", "Opportunities",
                'COUNTAX(FILTER(KEEPFILTERS(Opportunities), Opportunities[Status] = "Won"), Opportunities[OpportunitySeq])',
                "COUNT(CASE WHEN OPPORTUNITIES.`STATUS` = 'Won' THEN OPPORTUNITIES.`OPPORTUNITYSEQ` END)"),
        measure("Pipeline", "Opportunities",
                'CALCULATE(SUMX(Opportunities, Opportunities[Value]), FILTER(Opportunities, '
                'Opportunities[Status] = "Open" && VALUE(LEFT(Opportunities[PipelineStep], 1)) >= 2))',
                "SUM(CASE WHEN (OPPORTUNITIES.`STATUS` = 'Open') AND (TRY_CAST(LEFT(OPPORTUNITIES.`PIPELINESTEP`, 1) "
                "AS DOUBLE) >= 2) THEN OPPORTUNITIES.`COL_VALUE` END::DOUBLE)"),
        measure("Pick", "Opportunities", "SELECTEDVALUE(Opportunities[Status], 0)", "MAX(OPPORTUNITIES.`STATUS`)"),
    ]
    r = plan_metric_views(model([opp], [], ms))
    for name in ("Opportunity Count", "Count of Won", "Pipeline"):
        assert placed_in(r, name) == "opportunities_metrics", name
    assert reason(r, "Pick") == "unrecognised_pattern"  # depends on the report selection
    assert "'Won'" in r.placed("Count of Won")[1].expr


# --- join key types ---------------------------------------------------------------------------


def typed(name: str, dtype: str) -> SMLColumn:
    from semabridge.sml.models import DataType

    return SMLColumn(unique_name=name, label=name, data_type=DataType(dtype))


def test_key_type_decisions():
    from semabridge.target_backends.databricks.planner import keytypes as k

    assert k.decide("BIGINT", "int") == (k.OK, None)
    assert k.decide("string", "integer") == (k.CAST, "right")
    assert k.decide("INT", "STRING") == (k.CAST, "left")
    assert k.decide("TIMESTAMP", "date") == (k.WARN, None)
    assert k.decide("date", "integer") == (k.BLOCK, None)
    assert k.decide(None, "date") == (k.OK, None)


def test_text_against_number_is_compared_as_text():
    mdl = model([table("Sales", typed("PeriodID", "string"), "Amount"), table("Time", typed("PeriodID", "integer"), "Year")],
                [rel("Sales", "PeriodID", "Time", "PeriodID")], [REVENUE])
    v = plan_metric_views(mdl).view("sales_metrics")
    j = next(j for j in v.joins if j.dataset == "Time")
    assert j.on_expr == "source.`PERIODID` = CAST(`time`.`PERIODID` AS STRING)"
    assert j.warnings and "compared as text" in j.warnings[0]
    assert any(c["kind"] == "key_types" for c in v.checks)


def test_date_time_against_date_is_kept_with_warning():
    mdl = model([table("Cases", typed("Created", "datetime"), "Amount"), table("Cal", typed("Date", "date"))],
                [rel("Cases", "Created", "Cal", "Date")],
                [measure("Total", "Cases", "SUM(Cases[Amount])", "SUM(CASES.`AMOUNT`)")])
    j = plan_metric_views(mdl).view("cases_metrics").joins[0]
    assert "CAST" not in j.on_expr and "midnight" in j.warnings[0]


def test_incomparable_keys_block_only_that_table():
    mdl = model(
        [table("Opp", typed("CloseDate", "date"), "ProductID", "Amount"), table("Cal", typed("DAY", "integer"), "Month"),
         table("Product", "ProductID", "Name")],
        [rel("Opp", "CloseDate", "Cal", "DAY"), rel("Opp", "ProductID", "Product", "ProductID")],
        [measure("Total", "Opp", "SUM(Opp[Amount])", "SUM(OPP.`AMOUNT`)"),
         measure("This Month", "Opp", 'CALCULATE(SUM(Opp[Amount]), Cal[Month] = "Jan")',
                 "SUM(CASE WHEN CAL.`MONTH` = 'Jan' THEN OPP.`AMOUNT` END)", [CALC])],
    )
    r = plan_metric_views(mdl)
    v = r.view("opp_metrics")
    assert {j.dataset for j in v.joins} == {"Product"}
    assert placed_in(r, "Total") == "opp_metrics"
    assert reason(r, "This Month") == "join_key_type_mismatch"
    assert any("can't be compared" in n for n in v.notes)


def test_real_types_from_unity_catalog_win_over_the_model():
    mdl = model([table("Sales", typed("PeriodID", "string"), "Amount"), table("Time", typed("PeriodID", "integer"))],
                [rel("Sales", "PeriodID", "Time", "PeriodID")], [REVENUE])
    binds = [TableBinding("Sales", "SALES", "bind", UcTable("w", "s", "sales", columns={"PERIODID": ("PeriodID", "INT"), "AMOUNT": ("Amount", "DOUBLE")}),
                          "name", column_map={"PERIODID": "PeriodID", "AMOUNT": "Amount"}),
             TableBinding("Time", "TIME", "bind", UcTable("w", "s", "time", columns={"PERIODID": ("PeriodID", "BIGINT")}),
                          "name", column_map={"PERIODID": "PeriodID"})]
    j = plan_metric_views(mdl, TablePlan(bindings=binds)).view("sales_metrics").joins[0]
    assert j.on_expr == "source.`PeriodID` = `time`.`PeriodID`" and not j.warnings


def test_stub_key_takes_the_type_it_joins_to():
    from semabridge.target_backends.databricks.tables import plan_tables

    mdl = model([table("Sales", typed("PeriodID", "string"), typed("Amount", "float")),
                 table("Time", typed("PeriodID", "integer"), typed("Year", "integer"))],
                [rel("Sales", "PeriodID", "Time", "PeriodID")], [REVENUE])
    uc = {("w", "s"): {"SALES": UcTable("w", "s", "sales", columns={"PERIODID": ("PeriodID", "STRING"), "AMOUNT": ("Amount", "DOUBLE")})}}
    plan = plan_tables(mdl, uc, source_schemas=[("w", "s")], on_missing_source="stub")
    time = next(b for b in plan.bindings if b.dataset == "Time")
    assert dict(time.stub_columns)["period_id"] == "STRING"    # aligned with Sales' real STRING key
    j = plan_metric_views(mdl, plan).view("sales_metrics").joins[0]
    assert "CAST" not in j.on_expr and not j.warnings


def test_two_stubs_keep_power_bi_types_so_the_planner_still_checks():
    from semabridge.target_backends.databricks.tables import plan_tables

    mdl = model([table("Opp", typed("CloseDate", "date"), typed("Amount", "float")),
                 table("Cal", typed("DAY", "integer"), typed("Month", "string")),
                 table("Camp", typed("CampaignSeq", "integer")),
                 table("Opp2", typed("CampaignSeq", "string"), typed("Amount", "float"))],
                [rel("Opp", "CloseDate", "Cal", "DAY"), rel("Opp2", "CampaignSeq", "Camp", "CampaignSeq")],
                [measure("Total", "Opp", "SUM(Opp[Amount])", "SUM(OPP.`AMOUNT`)"),
                 measure("Total2", "Opp2", "SUM(Opp2[Amount])", "SUM(OPP2.`AMOUNT`)")])
    plan = plan_tables(mdl, {("w", "s"): {}}, source_schemas=[("w", "s")], on_missing_source="stub")
    assert dict(next(b for b in plan.bindings if b.dataset == "Opp").stub_columns)["close_date"] == "DATE"
    r = plan_metric_views(mdl, plan)
    assert not r.view("opp_metrics").joins and any("can't be compared" in n for n in r.view("opp_metrics").notes)
    j = r.view("opp2_metrics").joins[0]
    assert "CAST(" in j.on_expr


def test_field_names_use_databricks_naming_v2():
    """Lowercase snake_case; ordinary words (date, value) stay; keywords get a suffix."""
    mdl = model([table("Sales", "Date", "Value", "Amount", "Select", "OrderDate")], [], [REVENUE])
    names = {f.column: f.name for f in plan_metric_views(mdl).view("sales_metrics").fields}
    assert names == {"Date": "date", "Value": "value", "Amount": "amount", "Select": "select_field",
                     "OrderDate": "order_date"}


# --- naming v2 and binding safety (Databricks naming policy revision 2) ------------------------

NORMALISER_CASES = [
    ("TotalSales", "measure", "total_sales"), ("Total Sales", "measure", "total_sales"),
    ("TotalSalesLY", "measure", "total_sales_ly"), ("Gross Margin This Year %", "measure", "gross_margin_this_year_pct"),
    ("Avg $/Unit TY", "measure", "avg_dollars_unit_ty"), ("# Orders", "measure", "num_orders"),
    ("R&D", "field", "r_and_d"), ("Age>=18", "field", "age_gte_18"), ("Sales [USD]", "field", "sales_usd"),
    ("SKUs Sold", "field", "skus_sold"), ("IDs", "field", "ids"), ("KPIs", "field", "kpis"),
    ("URLsVisited", "field", "urls_visited"), ("xCoordinate", "field", "x_coordinate"), ("yAxis", "field", "y_axis"),
    ("iPhone Sales", "field", "iphone_sales"), ("eBay Orders", "field", "ebay_orders"),
    ("HTTPStatusCode", "field", "http_status_code"), ("DistrictID", "field", "district_id"), ("DM_Pic", "field", "dm_pic"),
    ("Café Résumé", "field", "cafe_resume"), ("Straße", "field", "strasse"), ("2024 Sales", "measure", "measure_2024_sales"),
    ("Current Date", "field", "field_current_date"), ("Select", "field", "select_field"), ("Order", "field", "order_field"),
    ("Date", "field", "date"), ("Value", "field", "value"), ("Count", "field", "count"), ("TOTALSALES", "measure", "totalsales"),
    ("AddressLine2", "field", "address_line2"), ("Q1Revenue", "field", "q1_revenue"),
    ("Customer's Name", "field", "customers_name"), ("Revenue: Actual", "field", "revenue_actual"),
    ("Gross___Margin", "field", "gross_margin"), ("SellingAreaSize", "field", "selling_area_size"),
]


@pytest.mark.parametrize("label,kind,expected", NORMALISER_CASES)
def test_normaliser_pinned_names(label, kind, expected):
    from semabridge.target_rules.databricks.naming import normalize

    assert normalize(label, kind)[0] == expected


def test_normaliser_properties():
    from semabridge.target_rules.databricks.naming import is_generated_name, normalize

    labels = [c[0] for c in NORMALISER_CASES] + ["日本", "日本 Sales", "", "   ", "%%%", "a" * 400, "Ünïcode Name", "_metadata"]
    for label in labels:
        name, _ = normalize(label, "field", ("field", "T", label))
        assert is_generated_name(name), (label, name)
        assert normalize(label, "field", ("field", "T", label))[0] == name            # deterministic
    for label in [c[0] for c in NORMALISER_CASES]:
        name = normalize(label, "field")[0]
        assert normalize(name, "field")[0] == name                                     # idempotent
    a, _ = normalize("日本", "field", ("field", "T", "日本"))
    b, _ = normalize("中国", "field", ("field", "T", "中国"))
    assert a != b and a.startswith("field_")                                           # identity hash, not one fallback
    assert normalize("日本 Sales", "field", 1)[0].startswith("sales_")                 # readable part kept
    assert len(normalize("a" * 400, "field")[0]) == 255


def test_databricks_naming_is_independent_of_snowflake():
    from semabridge.target_rules import DATABRICKS_RULES, SNOWFLAKE_RULES

    assert DATABRICKS_RULES.naming.target_name("Gross Margin %", entity_kind="metric") == "gross_margin_pct"
    assert SNOWFLAKE_RULES.naming.target_name("Gross Margin %", entity_kind="metric") == "GROSS_MARGIN_PCT"
    assert DATABRICKS_RULES.naming.target_name("Date", entity_kind="column") == "date"


def _uc(name, *cols, types=None):
    types = types or {}
    return UcTable("w", "s", name, columns={c.upper(): (c, types.get(c, "STRING")) for c in cols})


def _plan(mdl, *tables, mode="stub"):
    from semabridge.target_backends.databricks.tables import plan_tables

    return plan_tables(mdl, {("w", "s"): {t.name.upper(): t for t in tables}}, source_schemas=[("w", "s")],
                       on_missing_source=mode)


def test_two_physical_candidates_are_ambiguous_not_guessed():
    mdl = model([table("Sales", "Sales Amount", "Qty", "ID", "Extra1", "Extra2")], [],
                [measure("Total", "Sales", "SUM(Sales[Sales Amount])", "SUM(SALES.`SALES_AMOUNT`::DOUBLE)")])
    tp = _plan(mdl, _uc("sales", "SalesAmount", "sales_amount", "Qty", "ID", "Extra1", "Extra2"))
    b = tp.bindings[0]
    assert b.action == "bind" and b.ambiguous_columns["Sales Amount"] == ["SalesAmount", "sales_amount"]
    r = plan_metric_views(mdl, tp)
    lo = r.left_out_reason("Total")
    assert lo.reason == "ambiguous_binding" and "SalesAmount" in lo.detail


def test_no_id_suffix_guess():
    mdl = model([table("Sales", "Store", "Amount", "A", "B", "C")], [],
                [measure("Stores", "Sales", "COUNT(Sales[Store])", "COUNT(SALES.`STORE`)")])
    tp = _plan(mdl, _uc("sales", "STORE_ID", "Amount", "A", "B", "C"))
    b = tp.bindings[0]
    assert "Store" in b.missing_columns and "STORE" not in b.column_map
    assert reason(plan_metric_views(mdl, tp), "Stores") == "column_missing_in_table"


def test_reverse_binding_two_sources_one_physical_column():
    mdl2 = model([table("Sales", "Revenue %", "Revenue_PCT", "Amount", "K1", "K2")], [], [REVENUE])
    b2 = _plan(mdl2, _uc("sales", "revenue_pct", "Amount", "K1", "K2", "Other")).bindings[0]
    assert set(b2.ambiguous_columns) == {"Revenue %", "Revenue_PCT"}       # two sources, one column: neither bound
    mdl3 = model([table("Sales", "revenue_pct", "Revenue %", "Amount", "K1", "K2")], [], [REVENUE])
    b3 = _plan(mdl3, _uc("sales", "revenue_pct", "Amount", "K1", "K2", "Other")).bindings[0]
    assert b3.column_match["revenue_pct"] == "exact" and "Revenue %" in b3.ambiguous_columns  # exact wins


def test_shared_converter_alias_is_declined():
    mdl = model([table("Sales Fact", "Amount"), table("Sales_Fact", "Amount")], [],
                [measure("T", "Sales Fact", "SUM('Sales Fact'[Amount])", "SUM(SALES_FACT.`AMOUNT`)")])
    r = plan_metric_views(mdl)
    assert reason(r, "T") == "ambiguous_table_alias"


def test_stub_references_resolve_to_allocated_columns():
    from semabridge.target_backends.databricks.tables import rewrite_measure_columns

    mdl = store_sales([REVENUE, CAPACITY])
    tp = _plan(mdl)  # nothing in Unity Catalog: all stubs
    rmap = tp.rewrite_map()
    for m in mdl.metrics:
        m.sql_expression = rewrite_measure_columns(m.sql_expression, rmap)   # what Step 6b does
    r = plan_metric_views(mdl, tp)
    assert r.placed("Total Revenue")[1].expr == "SUM(source.`amount`::DOUBLE)"
    store = next(b for b in tp.bindings if b.dataset == "Store")
    assert ("capacity", "DOUBLE") in store.stub_columns
    j = next(j for j in r.view("sales_metrics").joins if j.dataset == "Store")
    assert j.on_expr == "source.`store_id` = `store`.`store_id`"


def test_natural_names_first_and_deterministic():
    m1 = measure("TotalSales", "Sales", "SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`)")
    m2 = measure("Total Sales", "Sales", "SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`)")
    m3 = measure("Total Sales 2", "Sales", "SUM(Sales[Qty])", "SUM(SALES.`QTY`)")
    r = plan_metric_views(store_sales([m1, m2, m3]))
    names = {m.source_name: m.name for m in r.view("sales_metrics").measures}
    assert names == {"TotalSales": "total_sales", "Total Sales 2": "total_sales_2", "Total Sales": "total_sales_3"}
    dup = r.placed("Total Sales")[1]
    assert any("possible duplicate" in n for n in dup.notes)                 # kept, not merged
    r2 = plan_metric_views(store_sales([m3, m2, m1]))
    names2 = {m.source_name: m.name for m in r2.view("sales_metrics").measures}
    assert names2["Total Sales 2"] == "total_sales_2"                          # natural name never stolen


def test_implicit_measure_identity_and_name():
    from semabridge.sml.models import AggregationType

    s = SMLMetric(unique_name="SUM_OF_SELLINGAREASIZE", label="SUM_OF_SELLINGAREASIZE", dataset="Sales",
                  source_column="SellingAreaSize", aggregation=AggregationType.SUM,
                  expression="SUM(Sales[SellingAreaSize])",
                  sql_expression="SUM(SALES.`SELLINGAREASIZE`::DOUBLE)")
    r = plan_metric_views(model([table("Sales", "Amount", "SellingAreaSize")], [], [s]))
    m = r.placed("SUM_OF_SELLINGAREASIZE")[1]
    assert m.name == "sum_of_selling_area_size" and m.source_id.kind == "implicit_measure"
    assert (m.source_id.table, m.source_id.name, m.source_id.aggregation) == ("Sales", "SellingAreaSize", "SUM")


def test_join_alias_keyword_and_quoted_physical_names():
    mdl = model([table("Sales", "OrderKey", "Amount"), table("Order", "OrderKey", "Weird`Name")],
                [rel("Sales", "OrderKey", "Order", "OrderKey")], [REVENUE])
    v = plan_metric_views(mdl).view("sales_metrics")
    j = v.joins[0]
    assert j.name == "order_join" and j.on_expr == "source.`ORDERKEY` = `order_join`.`ORDERKEY`"
    f = next(f for f in v.fields if f.column == "Weird`Name")
    assert f.expr.startswith("`order_join`.`") and f.name == "weird_name"


def test_labels_respect_yaml_limits():
    from semabridge.target_backends.databricks.planner.names import label_metadata

    display, syns, notes = label_metadata("x" * 300, ["a", "A", "b" * 300] + [f"s{i}" for i in range(12)])
    assert len(display) == 255 and all(len(s) <= 255 for s in syns) and len(syns) == 10
    assert any("omitted" in n for n in notes) and any("shortened" in n for n in notes)
    assert sum(1 for s in syns if s.lower() == "a") == 1


def test_every_view_is_yaml_1_1():
    r = plan_metric_views(store_sales([REVENUE, CAPACITY]))
    assert {v.version for v in r.views} == {"1.1"}
    assert r.as_dict()["naming_policy_revision"] == 2
