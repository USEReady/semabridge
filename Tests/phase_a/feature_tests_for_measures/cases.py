"""
Feature test cases. Each case = a DAX pattern found in real models, written against the synthetic
fixture. The expected VALUE is computed independently from the data (oracle), so a translation passes
only if its SQL returns the right number - not just if it produces SQL.

expect:
  ("value", oracle)            SQL must evaluate to oracle() (tolerance 1e-6 relative)
  ("not_applicable", kind)     must NOT become a metric (text / what-if / ranking / filter context);
                               emitting CAST(NULL) also counts as "not emitted", a reason is checked if exposed
  ("no_sql", why)              malformed / partially parsed input must fail, never return partial SQL
expected_sql = reference Snowflake SQL (used by --selftest to prove the oracle and fixture agree)
context      = other measures this one references: {name: (home table, dax)}
"""
import math
from fixture import ORDERS as O

sold = O[O.Status == "Sold"]
shipped = O[O["Ship Date"].notna()]
step = O.Step.str[0].astype(int)
days = (O["Ship Date"].dropna().map(lambda d: d.toordinal()) - O.loc[O["Ship Date"].notna(), "Order Date"].map(lambda d: d.toordinal()))

def r(x, n=0):                       # DAX ROUND: half away from zero
    q = 10 ** n
    return math.copysign(math.floor(abs(x) * q + 0.5) / q, x)

BASE = {"Total Amount": ("Orders", "SUM(Orders[Amount])"),
        "Total Qty": ("Orders", "SUM(Orders[Qty])"),
        "Sold Amount": ("Measures", 'CALCULATE(SUM(Orders[Amount]), Orders[Status] = "Sold")')}

CASES = [
 # ---------------------------------------------------------------- F1 lexer & parser safety
 dict(id="F1.1", feature="F1 Lexer & parser", title="-- comment inside a FILTER",
      dax='CALCULATE(COUNT(Orders[OrderID]), FILTER(Orders, Orders[Status] = "Open"\n  -- && Orders[Step] IN {"3-Proposal"}\n))',
      home="Orders", expect=("value", lambda: (O.Status == "Open").sum()),
      expected_sql="COUNT(CASE WHEN orders.STATUS = 'Open' THEN orders.ORDERID END)"),
 dict(id="F1.2", feature="F1 Lexer & parser", title="// and /* */ comments",
      dax="/* total */ SUM(Orders[Qty]) // units", home="Orders", expect=("value", lambda: O.Qty.sum()),
      expected_sql="SUM(orders.QTY)"),
 dict(id="F1.3", feature="F1 Lexer & parser", title="TRUE() in a boolean filter",
      dax="CALCULATE(COUNTROWS(Orders), Orders[Is Priority] = TRUE())", home="Orders",
      expect=("value", lambda: O["Is Priority"].sum()),
      expected_sql="COUNT(CASE WHEN orders.IS_PRIORITY THEN orders.ORDERID END)"),
 dict(id="F1.4", feature="F1 Lexer & parser", title="FALSE() in a boolean filter",
      dax="CALCULATE(COUNTROWS(Orders), Orders[Is Priority] = FALSE())", home="Orders",
      expect=("value", lambda: (~O["Is Priority"]).sum()),
      expected_sql="COUNT(CASE WHEN NOT orders.IS_PRIORITY THEN orders.ORDERID END)"),
 dict(id="F1.5", feature="F1 Lexer & parser", title="trailing -1 after a function",
      dax="ROUND(AVERAGE(Orders[Qty]) / 2, 1)-1", home="Orders", expect=("value", lambda: r(O.Qty.mean() / 2, 1) - 1),
      expected_sql="ROUND(AVG(orders.QTY) / 2, 1) - 1"),
 dict(id="F1.6", feature="F1 Lexer & parser", title="DIVIDE(...)-1 (variance %)",
      dax="DIVIDE(SUM(Orders[Amount]), SUM(Orders[Qty]), 0)-1", home="Orders",
      expect=("value", lambda: O.Amount.sum() / O.Qty.sum() - 1),
      expected_sql="IFF(COALESCE(SUM(orders.QTY), 0) = 0, 0, SUM(orders.AMOUNT) / SUM(orders.QTY)) - 1"),
 dict(id="F1.7", feature="F1 Lexer & parser", title="negative literal after an operator / comma",
      dax="IF(SUM(Orders[Qty]) > 0, -1 * SUM(Orders[Qty]), -1)", home="Orders", expect=("value", lambda: -O.Qty.sum()),
      expected_sql="IFF(SUM(orders.QTY) > 0, -1 * SUM(orders.QTY), -1)"),
 dict(id="F1.8", feature="F1 Lexer & parser", title="leftover tokens must fail, not return partial SQL",
      dax="SUM(Orders[Qty]) SUM(Orders[Amount])", home="Orders", expect=("no_sql", "two expressions without an operator")),

 # ---------------------------------------------------------------- F2 references & naming
 dict(id="F2.1", feature="F2 References", title="bare [Column] inside a nested function",
      dax="ROUND(AVERAGE([Price]), 0)", home="Orders", expect=("value", lambda: r(O.Price.mean(), 0)),
      expected_sql="ROUND(AVG(orders.PRICE), 0)"),
 dict(id="F2.2", feature="F2 References", title="measure on an empty measure table keeps the DAX table",
      dax='CALCULATE(SUM(Orders[Amount]), Orders[Status] = "Sold")', home="Measures",
      expect=("value", lambda: sold.Amount.sum()),
      expected_sql="SUM(CASE WHEN orders.STATUS = 'Sold' THEN orders.AMOUNT END)"),
 dict(id="F2.3", feature="F2 References", title="column name with spaces and special characters",
      dax="AVERAGE(Orders[Unit Price ($)])", home="Orders", expect=("value", lambda: O["Unit Price ($)"].mean()),
      expected_sql="AVG(orders.UNIT_PRICE_DOL)"),
 dict(id="F2.4", feature="F2 References", title="several SUMs of different columns added together",
      dax="SUM(Orders[Qty]) + SUM(Orders[Amount]) + SUM(Orders[Price])", home="Measures",
      expect=("value", lambda: O.Qty.sum() + O.Amount.sum() + O.Price.sum()),
      expected_sql="SUM(orders.QTY) + SUM(orders.AMOUNT) + SUM(orders.PRICE)"),

 # ---------------------------------------------------------------- F3 iterators & filters
 dict(id="F3.1", feature="F3 Iterators & filters", title="SUMX over the own table",
      dax="SUMX(Orders, Orders[Qty] * Orders[Price])", home="Orders", expect=("value", lambda: (O.Qty * O.Price).sum()),
      expected_sql="SUM(orders.QTY * orders.PRICE)"),
 dict(id="F3.2", feature="F3 Iterators & filters", title="CALCULATE(SUMX, FILTER(own table))",
      dax='CALCULATE(SUMX(Orders, Orders[Amount]), FILTER(Orders, Orders[Status] = "Sold"))', home="Orders",
      expect=("value", lambda: sold.Amount.sum()),
      expected_sql="SUM(CASE WHEN orders.STATUS = 'Sold' THEN orders.AMOUNT END)"),
 dict(id="F3.3", feature="F3 Iterators & filters", title="COUNTAX(FILTER(KEEPFILTERS(...)))",
      dax='COUNTAX(FILTER(KEEPFILTERS(Orders), Orders[Status] = "Sold"), Orders[OrderID])', home="Orders",
      expect=("value", lambda: len(sold)), expected_sql="COUNT(CASE WHEN orders.STATUS = 'Sold' THEN orders.ORDERID END)"),
 dict(id="F3.4", feature="F3 Iterators & filters", title="COUNTAX(table, TRUE()) = row count",
      dax="COUNTAX(Orders, TRUE())", home="Orders", expect=("value", lambda: len(O)), expected_sql="COUNT(orders.ORDERID)"),
 dict(id="F3.5", feature="F3 Iterators & filters", title="COUNTROWS(table)",
      dax="COUNTROWS('Orders')", home="Orders", expect=("value", lambda: len(O)), expected_sql="COUNT(orders.ORDERID)"),
 dict(id="F3.6", feature="F3 Iterators & filters", title="CALCULATE(COUNT, FILTER(NOT(ISBLANK(...))))",
      dax="CALCULATE(COUNT([OrderID]), FILTER(Orders, NOT(ISBLANK(Orders[Ship Date]))))", home="Orders",
      expect=("value", lambda: len(shipped)),
      expected_sql="COUNT(CASE WHEN orders.SHIP_DATE IS NOT NULL THEN orders.ORDERID END)"),
 dict(id="F3.7", feature="F3 Iterators & filters", title="CALCULATE(COUNT, FILTER(ISBLANK(...)))",
      dax="CALCULATE(COUNT([OrderID]), FILTER(Orders, ISBLANK(Orders[Ship Date])))", home="Orders",
      expect=("value", lambda: O["Ship Date"].isna().sum()),
      expected_sql="COUNT(CASE WHEN orders.SHIP_DATE IS NULL THEN orders.ORDERID END)"),
 dict(id="F3.8", feature="F3 Iterators & filters", title="AVERAGEX with DATEDIFF",
      dax="AVERAGEX(Orders, DATEDIFF(Orders[Order Date], Orders[Ship Date], DAY))", home="Orders",
      expect=("value", lambda: days.mean()),
      expected_sql="AVG(DATEDIFF(DAY, orders.ORDER_DATE, orders.SHIP_DATE))"),
 dict(id="F3.9", feature="F3 Iterators & filters", title="MAXX / MINX",
      dax="MAXX(Orders, Orders[Qty] * Orders[Price]) - MINX(Orders, Orders[Price])", home="Orders",
      expect=("value", lambda: (O.Qty * O.Price).max() - O.Price.min()),
      expected_sql="MAX(orders.QTY * orders.PRICE) - MIN(orders.PRICE)"),

 # ---------------------------------------------------------------- F4 VAR ... RETURN
 dict(id="F4.1", feature="F4 VAR / RETURN", title="two variables, DIVIDE in RETURN",
      dax="VAR a = SUM(Orders[Amount])\nVAR b = SUM(Orders[Qty])\nRETURN DIVIDE(a, b)", home="Orders",
      expect=("value", lambda: O.Amount.sum() / O.Qty.sum()), expected_sql="SUM(orders.AMOUNT) / NULLIF(SUM(orders.QTY), 0)"),
 dict(id="F4.2", feature="F4 VAR / RETURN", title="variable using another variable",
      dax='VAR s = CALCULATE(SUM(Orders[Amount]), Orders[Status] = "Sold")\nVAR t = s * 2\nRETURN t + s', home="Orders",
      expect=("value", lambda: sold.Amount.sum() * 3),
      expected_sql="SUM(CASE WHEN orders.STATUS = 'Sold' THEN orders.AMOUNT END) * 3"),

 # ---------------------------------------------------------------- F5 scalar functions
 dict(id="F5.1", feature="F5 Functions", title="VALUE(LEFT(...)) inside a filter",
      dax="CALCULATE(SUM(Orders[Amount]), FILTER(Orders, VALUE(LEFT(Orders[Step], 1)) >= 3))", home="Orders",
      expect=("value", lambda: O[step >= 3].Amount.sum()),
      expected_sql="SUM(CASE WHEN TRY_TO_NUMBER(LEFT(orders.STEP, 1)) >= 3 THEN orders.AMOUNT END)"),
 dict(id="F5.2", feature="F5 Functions", title="MROUND",
      dax="MROUND(SUM(Orders[Amount]), 100)", home="Orders", expect=("value", lambda: r(O.Amount.sum() / 100) * 100),
      expected_sql="ROUND(SUM(orders.AMOUNT) / 100) * 100"),
 dict(id="F5.3", feature="F5 Functions", title="ROUNDUP / ROUNDDOWN",
      dax="ROUNDUP(AVERAGE(Orders[Price]), 0) + ROUNDDOWN(AVERAGE(Orders[Price]), 0)", home="Orders",
      expect=("value", lambda: math.ceil(O.Price.mean()) + math.floor(O.Price.mean())),
      expected_sql="CEIL(AVG(orders.PRICE)) + FLOOR(AVG(orders.PRICE))"),
 dict(id="F5.4", feature="F5 Functions", title="DIVIDE with alternate result on zero",
      dax='DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Qty]), Orders[Status] = "Missing"), 0)', home="Orders",
      expect=("value", lambda: 0),
      expected_sql="IFF(COALESCE(SUM(CASE WHEN orders.STATUS = 'Missing' THEN orders.QTY END), 0) = 0, 0, SUM(orders.AMOUNT) / SUM(CASE WHEN orders.STATUS = 'Missing' THEN orders.QTY END))"),

 # ---------------------------------------------------------------- F6 not applicable (must not become a NULL metric)
 dict(id="F6.1", feature="F6 Not applicable", title="text label", dax='CONCATENATE("Total: ", FORMAT(SUM(Orders[Qty]), "0"))',
      home="Measures", expect=("not_applicable", "text")),
 dict(id="F6.2", feature="F6 Not applicable", title="colour string", dax='IF(SUM(Orders[Qty]) > 10, "#00FF00", "#FF0000")',
      home="Measures", expect=("not_applicable", "text")),
 dict(id="F6.3", feature="F6 Not applicable", title="compare to the rest", dax="CALCULATE(SUM(Orders[Amount]), FILTER(ALL(Orders), Orders[Status] <> SELECTEDVALUE(Orders[Status])))",
      home="Orders", expect=("not_applicable", "needs window metric")),
 dict(id="F6.4", feature="F6 Not applicable", title="ranking", dax="RANKX(ALL(Product[Name]), [Total Amount],, DESC)",
      home="Measures", expect=("not_applicable", "ranking"), context={"Total Amount": BASE["Total Amount"]}),
 dict(id="F6.5", feature="F6 Not applicable", title="constant", dax="10000",
      home="Measures", expect=("not_applicable", "constant")),
 dict(id="F16.1", feature="F16 Selector split", title="IF over a disconnected selector",
      dax='IF(SUM(Selector[K]) = 1, SUM(Orders[Amount]), IF(SUM(Selector[K]) = 2, " ", CALCULATE(SUM(Orders[Qty]), Product[Category] = "Software")))',
      home="Selector", expect=("not_applicable", "selector"),
      solution="Selector over a table with no relationships -> original not applicable; numeric branches become their own metrics (opt-in)."),

 # ---------------------------------------------------------------- F10 SELECTEDVALUE
 dict(id="F10.1", feature="F10 SELECTEDVALUE", title="several values -> alternate (never ANY_VALUE)",
      dax="SELECTEDVALUE(Param[Value], 0)", home="Param", expect=("value", lambda: 0),
      expected_sql="IFF(COUNT(DISTINCT param.COL_VALUE) + MAX(IFF(param.COL_VALUE IS NULL, 1, 0)) = 1, MAX(param.COL_VALUE), 0)"),
 dict(id="F10.2", feature="F10 SELECTEDVALUE", title="exactly one value -> that value",
      dax="SELECTEDVALUE(Scenario[Value], 0)", home="Scenario", expect=("value", lambda: 5),
      expected_sql="IFF(COUNT(DISTINCT scenario.COL_VALUE) + MAX(IFF(scenario.COL_VALUE IS NULL, 1, 0)) = 1, MAX(scenario.COL_VALUE), 0)"),
 dict(id="F10.3", feature="F10 SELECTEDVALUE", title="one number + blank -> alternate (blank counts as a value)",
      dax="SELECTEDVALUE(Nullable[Value], 0)", home="Nullable", expect=("value", lambda: 0),
      expected_sql="IFF(COUNT(DISTINCT nullable.COL_VALUE) + MAX(IFF(nullable.COL_VALUE IS NULL, 1, 0)) = 1, MAX(nullable.COL_VALUE), 0)"),
 dict(id="F10.4", feature="F10 SELECTEDVALUE", title="what-if measure used in another measure -> default constant",
      dax="SUM(Orders[Qty]) * (1 + Param[Adjustment Value] / 100)", home="Orders",
      context={"Adjustment Value": ("Param", "SELECTEDVALUE(Param[Value], 0)")},
      expect=("value", lambda: O.Qty.sum()),
      expected_sql="SUM(orders.QTY) * (1 + (0) / 100)"),
 dict(id="F10.5", feature="F10 SELECTEDVALUE", title="same, measure referenced without table prefix",
      dax="VAR r = SUM(Orders[Amount]) RETURN r + r * ([Adjustment Value] / 100)", home="Orders",
      context={"Adjustment Value": ("Param", "SELECTEDVALUE(Param[Value], 0)")},
      expect=("value", lambda: O.Amount.sum()),
      expected_sql="(SUM(orders.AMOUNT)) + (SUM(orders.AMOUNT)) * ((0) / 100)"),

 # ---------------------------------------------------------------- F7 measure dependencies
 dict(id="F7.1", feature="F7 Dependencies", title="arithmetic over two base measures",
      dax="[Total Amount] - [Total Qty]", home="Orders", expect=("value", lambda: O.Amount.sum() - O.Qty.sum()),
      context={k: BASE[k] for k in ("Total Amount", "Total Qty")},
      expected_sql="SUM(orders.AMOUNT) - SUM(orders.QTY)"),
 dict(id="F7.2", feature="F7 Dependencies", title="ratio over a base measure that lives on the measure table",
      dax="DIVIDE([Sold Amount], [Total Amount], 0)", home="Measures", expect=("value", lambda: sold.Amount.sum() / O.Amount.sum()),
      context={k: BASE[k] for k in ("Sold Amount", "Total Amount")},
      expected_sql="IFF(COALESCE(SUM(orders.AMOUNT), 0) = 0, 0, SUM(CASE WHEN orders.STATUS = 'Sold' THEN orders.AMOUNT END) / SUM(orders.AMOUNT))"),
 # ---------------------------------------------------------------- F17 division safety
 dict(id="F17.1", feature="F17 Division safety", title="DAX '/' with zero denominator -> BLANK, not an error",
      dax='CALCULATE(COUNT(Orders[OrderID]), Orders[Status] = "X") / (CALCULATE(COUNT(Orders[OrderID]), Orders[Status] = "X") + CALCULATE(COUNT(Orders[OrderID]), Orders[Status] = "Y"))',
      home="Orders", expect=("value", lambda: None),
      expected_sql="COUNT(CASE WHEN orders.STATUS = 'X' THEN orders.ORDERID END) / NULLIF(COUNT(CASE WHEN orders.STATUS = 'X' THEN orders.ORDERID END) + COUNT(CASE WHEN orders.STATUS = 'Y' THEN orders.ORDERID END), 0)",
      solution="DAX a / b -> a / NULLIF(b, 0): Snowflake raises 'Division by zero', DAX returns BLANK."),
 dict(id="F17.2", feature="F17 Division safety", title="DIVIDE without alternate -> NULL",
      dax='DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Qty]), Orders[Status] = "X"))',
      home="Orders", expect=("value", lambda: None),
      expected_sql="SUM(orders.AMOUNT) / NULLIF(SUM(CASE WHEN orders.STATUS = 'X' THEN orders.QTY END), 0)"),
 dict(id="F17.3", feature="F17 Division safety", title="blank numerator keeps BLANK (not the alternate)",
      dax='DIVIDE(CALCULATE(SUM(Orders[Amount]), Orders[Status] = "X"), SUM(Orders[Qty]), 0)',
      home="Orders", expect=("value", lambda: None),
      expected_sql="IFF(COALESCE(SUM(orders.QTY), 0) = 0, 0, SUM(CASE WHEN orders.STATUS = 'X' THEN orders.AMOUNT END) / SUM(orders.QTY))",
      solution="DIVIDE(a, b, alt) -> IFF(COALESCE(b,0)=0, alt, a/b) - not COALESCE(a/b, alt)."),

]

from cases_cross_table import build as _build_cross_table
CASES += _build_cross_table(BASE)
