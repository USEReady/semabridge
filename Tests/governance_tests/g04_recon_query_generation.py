"""G04 - Reconciliation query generation: are the DAX and SQL queries valid? (POST /api/reconciliation/generate-queries)

    uv run python governance_tests/g04_recon_query_generation.py

Static checks on the generated text (no execution):
  Q1  metric name with spaces / % -> DAX uses [Metric Name]; SQL alias quoted ("Metric Name")
  Q2  dimension 'Table.Column'    -> DAX 'Table'[Column]; SQL keeps the table (Table.Column), not just Column
  Q3  dimension without a table   -> DAX must not invent 'Column'[Column]
  Q4  no dimensions               -> DAX ROW(...) total; SQL without GROUP BY
  Q5  dimensions                  -> SQL GROUP BY / ORDER BY count matches dimensions
  Q6  names with quotes / brackets -> escaped (no broken or injectable query)
  Q7  semantic view target        -> SQL uses SEMANTIC_VIEW(... METRICS ... DIMENSIONS ...) or the metric
                                     name, not a pasted expression on a semantic view
  Q8  filters                     -> appear in both queries
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

a = base_args(__doc__).parse_args()
api = Api(a.base, a.token)
rep = Report("G04 reconciliation query generation")


def gen(metric, expr, target, dims=None, filters=None):
    r = api.post("/api/reconciliation/generate-queries", {
        "metric_name": metric, "metric_sql_expression": expr, "target_table_or_view": target,
        "dimensions": dims or [], "filters": filters or []})
    if not r.ok:
        rep.fail(f"generate {metric}", body_text(r))
        return {"dax_query": "", "sql_query": ""}
    return r.json()


q = gen("Net Sales %", "SUM(t.amount)", "DB.SCH.FACT", ["Sales.Region"])
dax, sql = q["dax_query"], q["sql_query"]
rep.check("[Net Sales %]" in dax, "Q1 DAX measure reference [Net Sales %]")
rep.check(re.search(r'AS\s+"Net Sales %"', sql) is not None, "Q1 SQL alias with spaces/% is quoted", sql.split("FROM")[0][-60:])
rep.check("'Sales'[Region]" in dax, "Q2 DAX 'Table'[Column]")
rep.check(re.search(r"\bSales\.Region\b", sql) is not None, "Q2 SQL keeps the table qualifier", "dimension reduced to 'Region'")

q = gen("M", "SUM(x)", "DB.SCH.FACT", ["Region"])
rep.check("'Region'[Region]" not in q["dax_query"], "Q3 DAX does not invent 'Region'[Region] for an unqualified dimension",
          "unqualified dimension becomes a table with the same name")

q = gen("M", "SUM(x)", "DB.SCH.FACT", [])
rep.check("ROW(" in q["dax_query"], "Q4 no dimensions -> DAX ROW()")
rep.check("GROUP BY" not in q["sql_query"].upper(), "Q4 no dimensions -> SQL without GROUP BY")

q = gen("M", "SUM(x)", "DB.SCH.FACT", ["T.A", "T.B", "T.C"])
gb = re.search(r"GROUP BY\s+([\d,\s]+)", q["sql_query"])
rep.check(bool(gb) and len(re.findall(r"\d+", gb.group(1))) == 3, "Q5 GROUP BY covers all 3 dimensions")

q = gen("Bad]Name\"x'y", "SUM(x)", "DB.SCH.FACT", ["T'1.Col]2"])
dax, sql = q["dax_query"], q["sql_query"]
rep.check("Bad]Name" not in dax or "Bad]]Name" in dax, "Q6 DAX escapes ']' in measure names", dax[:120])
rep.check("T'1" not in dax or "T''1" in dax, "Q6 DAX escapes quotes in table names")
rep.check('Bad]Name"x\'y' not in sql or '""' in sql, "Q6 SQL alias escapes double quotes", sql[:160])

q = gen("CASE_COUNT", "COUNT(*)", "DB.SCH.MY_MODEL_SEMANTIC", ["CASES.AGENT"])
sql = q["sql_query"].upper()
rep.check("SEMANTIC_VIEW(" in sql or not sql.strip(), "Q7 semantic view target uses SEMANTIC_VIEW(...)",
          "pastes the metric expression on a semantic view: 'SELECT COUNT(*) FROM <view>' is not a valid view query")

q = gen("M", "SUM(x)", "DB.SCH.FACT", ["T.A"], ["T.A = 'x'"])
rep.check("T.A = 'x'" in q["sql_query"], "Q8 filter in SQL")
rep.check("'x'" in q["dax_query"], "Q8 filter in DAX", "SQL predicate pasted into DAX unchanged" if "T.A = 'x'" in q["dax_query"] else "")
if "T.A = 'x'" in q["dax_query"]:
    rep.warn("Q8 the same SQL text is used as a DAX filter", "needs translation to TREATAS / FILTER syntax")
sys.exit(rep.done())
