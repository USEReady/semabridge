"""
Cross-table feature cases (F11-F15). Every case says which FEATURE it belongs to, the expected value
(computed independently from the synthetic data), the expected Snowflake SQL, and the generic SOLUTION the
implementation must follow. Generic names only; the rename test (--mutate) renames everything.

Extra fields understood by the runners:
  anchor        table the metric must be anchored on in the DDL (pipeline test) - not the DAX home table
  last_period   (table, column): value is checked at the last value of that column (NON ADDITIVE BY)
  ddl_contains  text the metric's DDL line must contain (pipeline test)
"""
import math
from fixture import TABLES

O = TABLES["Orders"]
S = TABLES["Snapshot"]
month = lambda s: s.map(lambda d: d.month if d is not None and d == d else None)


def build(BASE):
    sold = O[O.Status == "Sold"]
    p1 = O[O.ProductID == 1].Price.mean(); p3 = O[O.ProductID == 3].Price.mean()
    last = S[S["Period No"] == S["Period No"].max()]
    ctx_snap = {
        "Headcount": ("Snapshot", "CALCULATE(COUNT(Snapshot[Emp ID]), FILTER(ALL(Period[Period No]), Period[Period No] = MAX(Period[Period No])))"),
        "Actives":   ("Snapshot", "CALCULATE([Headcount], FILTER(Snapshot, ISBLANK(Snapshot[End Date])))"),
        "Leavers":   ("Snapshot", "CALCULATE(COUNT(Snapshot[Emp ID]), FILTER(Snapshot, NOT(ISBLANK(Snapshot[End Date]))))"),
    }
    return [
 # ======================================================================= F11 anchor by lineage
 dict(id="F11.1", feature="F11 Anchor by lineage", title="declared on a dimension, data from the fact",
      dax="SUM(Orders[Amount])", home="Product", anchor="Orders",
      expect=("value", lambda: O.Amount.sum()), expected_sql="SUM(orders.AMOUNT)",
      solution="Anchor = the table the physical columns come from (after inlining measures), never the DAX home "
               "table. Apply in translation AND at DDL emission."),
 dict(id="F11.2", feature="F11 Anchor by lineage", title="declared on a dimension, built from fact measures",
      dax="DIVIDE([Sold Amount], [Total Amount], 0)", home="Product", anchor="Orders",
      context={k: BASE[k] for k in ("Sold Amount", "Total Amount")},
      expect=("value", lambda: sold.Amount.sum() / O.Amount.sum()),
      expected_sql="IFF(COALESCE(SUM(orders.AMOUNT), 0) = 0, 0, SUM(CASE WHEN orders.STATUS = 'Sold' THEN orders.AMOUNT END) / SUM(orders.AMOUNT))",
      solution="Inline the referenced measures first, then anchor on the one table all columns come from."),
 dict(id="F11.3", feature="F11 Anchor by lineage", title="measure table + filter on a related dimension",
      dax='CALCULATE(SUM(Orders[Qty]), Product[Category] = "Hardware")', home="Measures", anchor="Orders",
      expect=("value", lambda: O[O.ProductID.isin(TABLES["Product"][TABLES["Product"].Category == "Hardware"].ProductID)].Qty.sum()),
      expected_sql="SUM(CASE WHEN orders.PRODUCT_CATEGORY = 'Hardware' THEN orders.QTY ELSE 0 END)",
      solution="Anchor on the fact (Orders): it reaches Product through a many-to-one relationship, so the "
               "dimension filter is valid there (join or enriched copy)."),

 # ======================================================================= F12 dimension filters
 dict(id="F12.1", feature="F12 Dimension filters", title="filter on the dimension KEY -> fact foreign key",
      dax="CALCULATE(SUM(Orders[Amount]), Product[ProductID] = 2)", home="Orders",
      expect=("value", lambda: O[O.ProductID == 2].Amount.sum()),
      expected_sql="SUM(CASE WHEN orders.PRODUCTID = 2 THEN orders.AMOUNT ELSE 0 END)",
      solution="If the filtered column is the 'to' key of a relationship fact->dim, rewrite the condition onto "
               "the fact's 'from' column (same table, no join)."),
 dict(id="F12.2", feature="F12 Dimension filters", title="filter on a dimension ATTRIBUTE",
      dax='CALCULATE(SUM(Orders[Amount]), Product[Category] = "Software")', home="Orders",
      expect=("value", lambda: O[O.ProductID.isin(TABLES["Product"][TABLES["Product"].Category == "Software"].ProductID)].Amount.sum()),
      expected_sql="SUM(CASE WHEN orders.PRODUCT_CATEGORY = 'Software' THEN orders.AMOUNT ELSE 0 END)",
      solution="Non-key attribute of a related dimension (reachable many-to-one): use the enriched copy "
               "<DIM>_<COLUMN> on the fact, or the dimension column through the relationship. Unreachable -> NULL + reason."),
 dict(id="F12.3", feature="F12 Dimension filters", title="two fixed-key filters subtracted with ISBLANK guards",
      dax="IF(NOT(ISBLANK(CALCULATE(AVERAGE(Orders[Price]), Product[ProductID] = 1))) && "
          "NOT(ISBLANK(CALCULATE(AVERAGE(Orders[Price]), Product[ProductID] = 3))), "
          "CALCULATE(AVERAGE(Orders[Price]), Product[ProductID] = 1) - CALCULATE(AVERAGE(Orders[Price]), Product[ProductID] = 3))",
      home="Orders", expect=("value", lambda: p1 - p3),
      expected_sql="CASE WHEN AVG(CASE WHEN orders.PRODUCTID = 1 THEN orders.PRICE END) IS NOT NULL "
                   "AND AVG(CASE WHEN orders.PRODUCTID = 3 THEN orders.PRICE END) IS NOT NULL "
                   "THEN AVG(CASE WHEN orders.PRODUCTID = 1 THEN orders.PRICE END) - AVG(CASE WHEN orders.PRODUCTID = 3 THEN orders.PRICE END) END",
      solution="Same key rewrite inside each CALCULATE; NOT ISBLANK(x) -> x IS NOT NULL; IF without else -> NULL."),
 dict(id="F12.4", feature="F12 Dimension filters", title="filter on a date-dimension attribute (active relationship)",
      dax="CALCULATE(SUM(Orders[Amount]), Calendar[Month No] = 3)", home="Orders",
      expect=("value", lambda: O[month(O["Order Date"]) == 3].Amount.sum()),
      expected_sql="SUM(CASE WHEN orders.CALENDAR_MONTH_NO = 3 THEN orders.AMOUNT ELSE 0 END)",
      solution="Date dimension attribute through the ACTIVE relationship (Order Date): enriched copy or join."),

 # ======================================================================= F13 inactive relationships
 dict(id="F13.1", feature="F13 Inactive relationships", title="USERELATIONSHIP on the inactive date path",
      dax="CALCULATE(SUM(Orders[Amount]), USERELATIONSHIP(Orders[Ship Date], Calendar[Cal Date]), Calendar[Month No] = 3)",
      home="Orders", expect=("value", lambda: O[month(O["Ship Date"]) == 3].Amount.sum()),
      expected_sql="SUM(CASE WHEN calendar_ship_date.MONTH_NO = 3 THEN orders.AMOUNT ELSE 0 END)",
      solution="Emit each inactive relationship as an alias of the dimension named '<Dim> <FK column>' "
               "(here calendar_ship_date) joined on that FK; USERELATIONSHIP(...) switches the filter to that alias."),
 dict(id="F13.2", feature="F13 Inactive relationships", title="USERELATIONSHIP with a row count",
      dax="CALCULATE(COUNTROWS(Orders), USERELATIONSHIP(Orders[Ship Date], Calendar[Cal Date]), Calendar[Month No] = 1)",
      home="Orders", expect=("value", lambda: int((month(O["Ship Date"]) == 1).sum())),
      expected_sql="COUNT(CASE WHEN calendar_ship_date.MONTH_NO = 1 THEN orders.ORDERID END)",
      solution="Same alias rule; counts use COUNT(CASE WHEN ... THEN key END) with no ELSE."),
 dict(id="F13.3", feature="F13 Inactive relationships", title="without USERELATIONSHIP the ACTIVE path is used",
      dax="CALCULATE(COUNTROWS(Orders), Calendar[Month No] = 1)", home="Orders",
      expect=("value", lambda: int((month(O["Order Date"]) == 1).sum())),
      expected_sql="COUNT(CASE WHEN orders.CALENDAR_MONTH_NO = 1 THEN orders.ORDERID END)",
      solution="The alias must not change measures that do not ask for it."),

 # ======================================================================= F14 share of total (until window metrics)
 dict(id="F14.1", feature="F14 Share of total (ALL)", title="ALL(dimension table) in the denominator",
      dax="DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Amount]), ALL(Product)))", home="Orders",
      expect=("not_applicable", "needs window metric"),
      solution="Until window metrics are available: CAST(NULL AS DOUBLE) + reason 'needs window metric'. "
               "Never drop silently, never emit a constant (1.0 is only right at grand total)."),
 dict(id="F14.2", feature="F14 Share of total (ALL)", title="ALL(one column) with measure references",
      dax="DIVIDE([Total Amount], CALCULATE([Total Amount], ALL(Product[Category])))", home="Orders",
      context={"Total Amount": BASE["Total Amount"]}, expect=("not_applicable", "needs window metric"),
      solution="Same as F14.1."),

 # ======================================================================= F15 non-additive (closing balance)
 dict(id="F15.1", feature="F15 Non-additive", title="closing balance (value at the last period)",
      dax=ctx_snap["Headcount"][1], home="Snapshot", last_period=("Period", "Period No"),
      ddl_contains="NON ADDITIVE BY",
      expect=("value", lambda: len(last)), expected_sql="COUNT(snapshot.EMP_ID)",
      solution="FILTER(ALL(T[p]), T[p] = MAX(T[p])) -> plain aggregate + NON ADDITIVE BY (T.p, direction proven "
               "to take the LAST period)."),
 dict(id="F15.2", feature="F15 Non-additive", title="filtered closing balance (actives)",
      dax=ctx_snap["Actives"][1], home="Snapshot", last_period=("Period", "Period No"),
      context={"Headcount": ctx_snap["Headcount"]}, ddl_contains="NON ADDITIVE BY",
      expect=("value", lambda: int(last["End Date"].isna().sum())),
      expected_sql="COUNT(CASE WHEN snapshot.END_DATE IS NULL THEN snapshot.EMP_ID END)",
      solution="A filter on top of a non-additive measure keeps its NON ADDITIVE BY."),
 dict(id="F15.3", feature="F15 Non-additive", title="ratio over a non-additive measure",
      dax="DIVIDE([Leavers], [Actives])", home="Snapshot",
      context={k: ctx_snap[k] for k in ("Headcount", "Actives", "Leavers")},
      expect=("not_applicable", "depends on non-additive metric"),
      solution="Never inline a non-additive metric's SQL into another metric (loses NON ADDITIVE BY -> counts every "
               "period). Reference the metric by name if supported, else CAST(NULL) + reason."),
 dict(id="F15.4", feature="F15 Non-additive", title="arithmetic between two non-additive measures",
      dax="[Headcount] - [Actives]", home="Snapshot",
      context={k: ctx_snap[k] for k in ("Headcount", "Actives")},
      expect=("not_applicable", "depends on non-additive metric"),
      solution="Same as F15.3."),
    ]
