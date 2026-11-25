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
    assert placed_in(r, "Total Revenue") == "SALES_METRICS"
    v = r.view("SALES_METRICS")
    assert {j.dataset for j in v.joins} == {"Store", "Product"}
    assert r.placed("Total Revenue")[1].expr == "SUM(source.`AMOUNT`::DOUBLE)"


def test_store_aggregates_use_their_own_population():
    mx = measure("Max Capacity", "Sales", "MAX(Store[Capacity])", "MAX(STORE.`CAPACITY`::DOUBLE)")
    dc = measure("Store Names", "Sales", "DISTINCTCOUNT(Store[StoreName])", "COUNT(DISTINCT STORE.`STORENAME`)")
    r = plan_metric_views(store_sales([CAPACITY, mx, dc, STORES]))
    for name in ("Total Capacity", "Max Capacity", "Store Names", "Total Stores"):
        assert placed_in(r, name) == "STORE_METRICS", name  # grain, not home table


def test_sumx_with_related_is_one_grain():
    m = measure("Revenue X", "Sales", "SUMX(Sales, Sales[Qty] * RELATED(Product[Price]))",
                "SUM(SALES.`QTY`::DOUBLE * PRODUCT.`PRICE`::DOUBLE)")
    r = plan_metric_views(store_sales([m]))
    view, plan = r.placed("Revenue X")
    assert view.name == "SALES_METRICS"
    assert plan.expr == "SUM(source.`QTY`::DOUBLE * product.`PRICE`::DOUBLE)"


def test_countrows_erased_table_is_taken_from_the_tree():
    r = plan_metric_views(store_sales([STORES, measure("Number of Sales", "Store", "COUNTROWS(Sales)", "COUNT(*)")]))
    assert placed_in(r, "Total Stores") == "STORE_METRICS"
    assert placed_in(r, "Number of Sales") == "SALES_METRICS"


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
    assert placed_in(r, "Total Revenue") == "SALES_METRICS" and placed_in(r, "Total Capacity") == "STORE_METRICS"


def test_revenue_plus_capacity_composed_in_store_view():
    r = plan_metric_views(store_sales([PLUS]), settings=PlannerSettings(capabilities=B_ON))
    view, plan = r.placed("Revenue Plus Capacity")
    assert view.name == "STORE_METRICS" and plan.placement == "B" and view.version == "1.1"
    sales = next(j for j in view.joins if j.dataset == "Sales")
    assert sales.cardinality == "one_to_many" and sales.parent == "source"
    assert sales.on == [("STOREID", "STOREID")]
    assert "sales.`AMOUNT`" in plan.expr and "source.`CAPACITY`" in plan.expr
    assert "IS NULL AND" in plan.expr  # both blank stays blank (converter's DAX handling kept)
    assert not any(f.path == "sales" for f in view.fields)  # no fields from one-to-many tables


def test_revenue_per_store_counts_root_rows():
    r = plan_metric_views(store_sales([PER_STORE]), settings=PlannerSettings(capabilities=B_ON))
    view, plan = r.placed("Revenue Per Store")
    assert view.name == "STORE_METRICS"
    assert "COUNT(*)" in plan.expr  # root rows = stores, Gamma included
    assert "NULLIF" in plan.expr     # DIVIDE's zero-denominator handling kept


def test_child_row_count_uses_join_key_marker():
    m = measure("Sales per Capacity", "Store", "DIVIDE(COUNTROWS(Sales), SUM(Store[Capacity]))",
                "(COUNT(*)) / NULLIF((SUM(STORE.`CAPACITY`::DOUBLE)), 0)")
    r = plan_metric_views(store_sales([m]), settings=PlannerSettings(capabilities=B_ON))
    view, plan = r.placed("Sales per Capacity")
    assert view.name == "STORE_METRICS"
    assert plan.expr.startswith("(NULLIF(COUNT(sales.`STOREID`), 0))")


def test_budget_variance_composes_under_shared_dimension():
    mdl = model(
        [table("Calendar", "Month"), table("Sales", "SaleID", "Month", "Amount"), table("Budget", "BudgetID", "Month", "BudgetAmount")],
        [rel("Sales", "Month", "Calendar", "Month"), rel("Budget", "Month", "Calendar", "Month")],
        [measure("Budget Variance", "Sales", "SUM(Sales[Amount]) - SUM(Budget[BudgetAmount])",
                 "SUM(SALES.`AMOUNT`::DOUBLE) - SUM(BUDGET.`BUDGETAMOUNT`::DOUBLE)")],
    )
    assert reason(plan_metric_views(mdl), "Budget Variance") == "mixed_grain_needs_composition"
    view, plan = plan_metric_views(mdl, settings=PlannerSettings(capabilities=B_ON)).placed("Budget Variance")
    assert view.name == "CALENDAR_METRICS"
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
    assert placed_in(r, "Total Capacity") == "STORE_METRICS"
    assert reason(r, "Revenue Plus Capacity") == "mixed_view_unproven"
    s2 = PlannerSettings(capabilities=B_ON | {Capability.MIXED_ROOT_VIEWS})
    r2 = plan_metric_views(store_sales([CAPACITY, PLUS]), settings=s2)
    assert placed_in(r2, "Revenue Plus Capacity") == "STORE_METRICS"
    assert placed_in(r2, "Total Capacity") == "STORE_METRICS"


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
    assert any(c["kind"] == "conflicting_filter" and c["measure"] == pct.name for c in r.view("SALES_METRICS").checks)
    assert "product.`CATEGORY` = 'A'" in a.expr


def test_strict_leaves_out_flagged_measure_and_dependants_with_root_cause():
    r = plan_metric_views(store_sales([REVENUE, SALES_A, SALES_A_PCT]), settings=PlannerSettings(calculate_mode="strict"))
    assert reason(r, "Sales A") == "filter_semantics_differ"
    lo = r.left_out_reason("Sales A %")
    assert lo.reason == "depends_on" and "Sales A" in lo.root_cause
    assert placed_in(r, "Total Revenue") == "SALES_METRICS"


def test_user_excluded_does_not_drop_dependants():
    r = plan_metric_views(store_sales([REVENUE, SALES_A, SALES_A_PCT]),
                          settings=PlannerSettings(excluded_measures=("Sales A",)))
    assert reason(r, "Sales A") == "user_excluded"
    assert placed_in(r, "Sales A %") == "SALES_METRICS"


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
    assert view.name == "STORE_METRICS" and plan.expr == "MAX(14)" and plan.semantic_status == "approximate"
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
    v = r.view("SALES_METRICS")
    assert placed_in(r, "Total Revenue") == "SALES_METRICS"
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
    assert any("both ways" in n for n in r2.notes) and any("both ways" in n for n in r2.view("SALES_METRICS").notes)


def test_hidden_and_calculated_columns():
    mdl = model([table("Sales", "StoreID", col("Secret", hidden=True), col("Profit", calculated=True), "Amount")], [],
                [REVENUE, measure("Profit Sum", "Sales", "SUM(Sales[Profit])", "SUM(SALES.`PROFIT`::DOUBLE)")])
    # no table plan: a calculated column can't be read
    r = plan_metric_views(mdl)
    names = {f.column for f in r.view("SALES_METRICS").fields}
    assert "Secret" not in names and "Profit" not in names and "Amount" in names
    assert r.placed("Profit Sum")[1].source_verification == "unverified"
    # bound table with a real PROFIT column: kept, unverified
    uc = UcTable("workspace", "src", "sales", columns={"STOREID": ("StoreID", "int"), "AMOUNT": ("Amount", "double"),
                                                         "PROFIT": ("Profit", "double"), "SECRET": ("Secret", "int")})
    b = TableBinding("Sales", "SALES", "bind", uc, "name", column_map={"STOREID": "StoreID", "AMOUNT": "Amount",
                                                                        "SECRET": "Secret", "PROFIT": "Profit"})
    r2 = plan_metric_views(mdl, TablePlan(bindings=[b]))
    profit = next(f for f in r2.view("SALES_METRICS").fields if f.column == "Profit")
    assert profit.source_verification == "unverified" and profit.expr == "source.`Profit`"
    assert r2.view("SALES_METRICS").source_table == "workspace.src.sales"


def test_stub_tables_mark_readiness_per_object():
    mdl = store_sales([REVENUE, CAPACITY])
    binds = [TableBinding("Sales", "SALES", "bind", UcTable("w", "s", "sales"), "name"),
             TableBinding("Store", "STORE", "stub", stub_columns=[("StoreID", "INT")]),
             TableBinding("Product", "PRODUCT", "bind", UcTable("w", "s", "product"), "name")]
    r = plan_metric_views(mdl, TablePlan(bindings=binds))
    assert r.placed("Total Revenue")[1].readiness == "real"
    assert r.placed("Total Capacity")[1].readiness == "structure_only"
    sales = r.view("SALES_METRICS")
    assert next(j for j in sales.joins if j.dataset == "Store").readiness == "structure_only"
    assert any("partly stub" in n for n in sales.notes)


def test_skipped_table_leaves_out_measures_that_need_it():
    binds = [TableBinding("Sales", "SALES", "bind", UcTable("w", "s", "sales"), "name"),
             TableBinding("Store", "STORE", "skip"), TableBinding("Product", "PRODUCT", "bind", UcTable("w", "s", "p"), "name")]
    r = plan_metric_views(store_sales([REVENUE, CAPACITY]), TablePlan(bindings=binds))
    assert reason(r, "Total Capacity") == "table_skipped"
    assert "Store" not in {j.dataset for j in r.view("SALES_METRICS").joins}


def test_name_clashes_and_reserved_names():
    mdl = model(
        [table("Sales", "DistrictID", "StoreID", "Amount"), table("Store", "StoreID", "DistrictID"),
         table("District", "DistrictID", "Name")],
        [rel("Sales", "StoreID", "Store", "StoreID"), rel("Store", "DistrictID", "District", "DistrictID")],
        [REVENUE, measure("Amount", "Sales", "SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`::DOUBLE)")],
    )
    r = plan_metric_views(mdl)
    v = r.view("SALES_METRICS")
    names = [x.name for x in v.fields + v.measures]
    assert len(names) == len({n.upper() for n in names})
    by = {(f.dataset, f.column): f.name for f in v.fields}
    assert by[("Sales", "DistrictID")] == "DISTRICTID"          # nearest the source keeps it
    assert by[("Store", "DistrictID")] == "STORE_DISTRICTID"
    assert by[("District", "DistrictID")] == "DISTRICT_DISTRICTID"
    assert by[("Sales", "Amount")] == "SALES_AMOUNT"              # the measure keeps AMOUNT
    # resync: a new source column must not take an already deployed name
    reserved = {"SALES_METRICS": {"field:Store.DistrictID": "DISTRICTID"}}
    r2 = plan_metric_views(mdl, settings=PlannerSettings(reserved_names=reserved))
    by2 = {(f.dataset, f.column): f.name for f in r2.view("SALES_METRICS").fields}
    assert by2[("Store", "DistrictID")] == "DISTRICTID"
    assert by2[("Sales", "DistrictID")] == "SALES_DISTRICTID"


def test_view_modes():
    mdl = store_sales([REVENUE, CAPACITY])
    with pytest.raises(PlannerError):
        plan_metric_views(mdl, settings=PlannerSettings(view_mode="primary_fact_only"))
    r = plan_metric_views(mdl, settings=PlannerSettings(view_mode="primary_fact_only", primary_fact="Sales"))
    assert [v.name for v in r.views] == ["SALES_METRICS"] and reason(r, "Total Capacity") == "view_not_selected"


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
    assert view.name == "STORE_METRICS" and plan.placement == "B"
    assert plan.depends_on == ["Total Revenue", "Total Stores"]
    assert placed_in(r, "Total Stores") == "STORE_METRICS" and placed_in(r, "Total Revenue") == "SALES_METRICS"


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
        assert placed_in(r, name) == "OPPORTUNITIES_METRICS", name
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
    v = plan_metric_views(mdl).view("SALES_METRICS")
    j = next(j for j in v.joins if j.dataset == "Time")
    assert j.on_expr == "source.`PERIODID` = CAST(time.`PERIODID` AS STRING)"
    assert j.warnings and "compared as text" in j.warnings[0]
    assert any(c["kind"] == "key_types" for c in v.checks)


def test_date_time_against_date_is_kept_with_warning():
    mdl = model([table("Cases", typed("Created", "datetime"), "Amount"), table("Cal", typed("Date", "date"))],
                [rel("Cases", "Created", "Cal", "Date")],
                [measure("Total", "Cases", "SUM(Cases[Amount])", "SUM(CASES.`AMOUNT`)")])
    j = plan_metric_views(mdl).view("CASES_METRICS").joins[0]
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
    v = r.view("OPP_METRICS")
    assert {j.dataset for j in v.joins} == {"Product"}
    assert placed_in(r, "Total") == "OPP_METRICS"
    assert reason(r, "This Month") == "join_key_type_mismatch"
    assert any("can't be compared" in n for n in v.notes)


def test_real_types_from_unity_catalog_win_over_the_model():
    mdl = model([table("Sales", typed("PeriodID", "string"), "Amount"), table("Time", typed("PeriodID", "integer"))],
                [rel("Sales", "PeriodID", "Time", "PeriodID")], [REVENUE])
    binds = [TableBinding("Sales", "SALES", "bind", UcTable("w", "s", "sales", columns={"PERIODID": ("PeriodID", "INT"), "AMOUNT": ("Amount", "DOUBLE")}),
                          "name", column_map={"PERIODID": "PeriodID", "AMOUNT": "Amount"}),
             TableBinding("Time", "TIME", "bind", UcTable("w", "s", "time", columns={"PERIODID": ("PeriodID", "BIGINT")}),
                          "name", column_map={"PERIODID": "PeriodID"})]
    j = plan_metric_views(mdl, TablePlan(bindings=binds)).view("SALES_METRICS").joins[0]
    assert j.on_expr == "source.`PeriodID` = time.`PeriodID`" and not j.warnings


def test_stub_key_takes_the_type_it_joins_to():
    from semabridge.target_backends.databricks.tables import plan_tables

    mdl = model([table("Sales", typed("PeriodID", "string"), typed("Amount", "float")),
                 table("Time", typed("PeriodID", "integer"), typed("Year", "integer"))],
                [rel("Sales", "PeriodID", "Time", "PeriodID")], [REVENUE])
    uc = {("w", "s"): {"SALES": UcTable("w", "s", "sales", columns={"PERIODID": ("PeriodID", "STRING"), "AMOUNT": ("Amount", "DOUBLE")})}}
    plan = plan_tables(mdl, uc, source_schemas=[("w", "s")], on_missing_source="stub")
    time = next(b for b in plan.bindings if b.dataset == "Time")
    assert dict(time.stub_columns)["PERIODID"] == "STRING"     # aligned with Sales' real STRING key
    j = plan_metric_views(mdl, plan).view("SALES_METRICS").joins[0]
    assert "CAST" not in j.on_expr and not j.warnings
