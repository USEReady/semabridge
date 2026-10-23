"""
Quick check of what SemaBridge's DAX translator returns for specific measures.

Run from the SemaBridge repo root:
    uv run python test_translator_cases.py

Uses only the translator (no Snowflake, no LLM). Each row shows:
  SUCCESS/FAIL, tier, the SQL it produced, and the SQL we EXPECT.
A row is flagged "MISMATCH" when it translated but the SQL differs from expected.
"""
import re

from semabridge.converter.dax_translator import DAXTranslator

# (case name, DAX, home table, expected Snowflake SQL or None if it should fail)
CASES = [
    # ---- the real measure ---------------------------------------------------
    ("Average Opportunity Days",
     "AVERAGEX(Sales, DATEDIFF(Sales[Sales Date], Sales[Closed Date], DAY))", "Sales",
     'AVG(DATEDIFF(DAY, sales."SALES_DATE", sales."CLOSED_DATE"))'),

    # ---- DATEDIFF alone (argument order) ------------------------------------
    ("DATEDIFF day (in MAX)",
     "MAX(DATEDIFF(Sales[Sales Date], Sales[Closed Date], DAY))", "Sales",
     'MAX(DATEDIFF(DAY, sales."SALES_DATE", sales."CLOSED_DATE"))'),
    ("DATEDIFF month",
     "AVERAGEX(Sales, DATEDIFF(Sales[Sales Date], Sales[Closed Date], MONTH))", "Sales",
     'AVG(DATEDIFF(MONTH, sales."SALES_DATE", sales."CLOSED_DATE"))'),

    # ---- other iterators ----------------------------------------------------
    ("SUMX simple",
     "SUMX(Sales, Sales[ProductRevenue] * 2)", "Sales",
     'SUM(sales."PRODUCTREVENUE" * 2)'),
    ("MAXX simple",
     "MAXX(Sales, Sales[ProductRevenue])", "Sales",
     'MAX(sales."PRODUCTREVENUE")'),

    # ---- controls: these already work today --------------------------------
    ("Plain AVERAGE (control)",
     "AVERAGE(Sales[ProductRevenue])", "Sales",
     'AVG(sales."PRODUCTREVENUE")'),
    ("Plain SUM (control)",
     "SUM(Sales[ProductRevenue])", "Sales",
     'SUM(sales."PRODUCTREVENUE")'),

    # ---- related bugs worth checking at the same time -----------------------
    ("Trailing -1 (lexer bug)",
     "ROUND(AVERAGE(Employee[TenureDays])/30, 1)-1", "Employee",
     'ROUND((AVG(employee."TENUREDAYS")) / 30, 1) - 1'),
    ("ROUND(AVERAGE()) (AVG Age)",
     "ROUND(AVERAGE(Employee[Age]), 0)", "Employee",
     'ROUND(AVG(employee."AGE"), 0)'),
    ("COUNT with NOT ISBLANK filter (Seps)",
     "CALCULATE(COUNT(Employee[EmplID]), FILTER(Employee, NOT(ISBLANK(Employee[TermDate]))))", "Employee",
     'COUNT(CASE WHEN employee."TERMDATE" IS NOT NULL THEN employee."EMPLID" END)'),

    # ---- real HR syntax: column without table name -------------------------
    ("AVG Age (real, unqualified)",
     "ROUND(AVERAGE([Age]), 0)", "Employee",
     'ROUND(AVG(employee."AGE"), 0)'),
    ("Seps (real, unqualified)",
     "CALCULATE(COUNT([EmplID]), FILTER(Employee, NOT(ISBLANK(Employee[TermDate]))))", "Employee",
     'COUNT(CASE WHEN employee."TERMDATE" IS NOT NULL THEN employee."EMPLID" END)'),
    ("AVERAGE unqualified (control)",
     "AVERAGE([TenureDays])", "Employee",
     'AVG(employee."TENUREDAYS")'),

    # ---- measures depending on another measure -----------------------------
    ("Actives (ISBLANK filter)",
     "CALCULATE([EmpCount], FILTER(Employee, ISBLANK(Employee[TermDate])))", "Employee",
     'COUNT(CASE WHEN employee."TERMDATE" IS NULL THEN employee."EMPLID" ELSE NULL END)'),
]

from types import SimpleNamespace
CONTEXT_METRICS = [
    SimpleNamespace(
        unique_name="EmpCount",
        name="EmpCount",
        expression="COUNT([EmplID])",
        dataset="Employee",
        sql_expression='COUNT(employee."EMPLID")',
        tier=1,
    )
]


def norm(sql):
    """Compare SQL loosely: ignore case, quotes, spaces, redundant brackets and ::FLOAT cast."""
    s = (sql or "").upper().replace('"', "").replace("::FLOAT", "")
    s = re.sub(r"\s+", "", s)
    return s.replace("(", "").replace(")", "")


def main():
    t = DAXTranslator()
    print(f"{'CASE':38} {'RESULT':8} {'TIER':4}  SQL")
    print("-" * 120)
    for name, dax, table, expected in CASES:
        try:
            res = t.translate(
                dax,
                table.lower(),
                table,
                metric_name=name,
                metrics_context=CONTEXT_METRICS,
                skip_tier5=True,
            )
            ok = bool(res.is_success and res.sql)
            sql, tier = (res.sql if ok else None), getattr(res, "tier", "?")
            reason = getattr(res, "failure_reason", None)
        except Exception as exc:          # translator crashed on this input
            ok, sql, tier, reason = False, None, "-", f"EXCEPTION: {exc}"

        if ok and expected and norm(sql) != norm(expected):
            verdict = "MISMATCH"
        else:
            verdict = "SUCCESS" if ok else "FAIL"

        print(f"{name:38} {verdict:8} {str(tier):4}  {sql or '-'}")
        if verdict != "SUCCESS":
            print(f"{'':38} {'':8} {'':4}  expected: {expected}")
            if reason:
                print(f"{'':38} {'':8} {'':4}  reason:   {reason}")
    print("-" * 120)
    print("SUCCESS = correct SQL | MISMATCH = translated but wrong SQL | FAIL = would become CAST(NULL AS DOUBLE)")


if __name__ == "__main__":
    main()
