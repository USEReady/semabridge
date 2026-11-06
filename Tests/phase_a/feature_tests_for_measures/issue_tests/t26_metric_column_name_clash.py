"""Regression guard: a metric named like a column of its own table; other metrics aggregating that column
are read by Snowflake as the metric (error 010220).
Expect: when a metric name equals a column name, other metrics use a non-clashing alias column."""
import re
from common import Result, cli
NAME = "T26 metric name equals column name (010220)"
def check(sv, pbix, args):
    r = Result(NAME)
    cols = {}
    for d in sv.dims: cols.setdefault(d.table, set()).add(d.column)
    names = {(m.table, m.name) for m in sv.metrics}
    for m in sv.metrics:
        if m.is_null: continue
        for t, n in names:
            if n == m.name and t == m.table: continue
            if re.search(rf"\b(SUM|AVG|COUNT|MIN|MAX)\s*\([^)]*\b{t}\.\"?{n}\"?\b", m.expr, re.I) and " OVER " not in m.expr.upper():
                r.fail(f"{m.table}.{m.name}: aggregates {t}.{n}, which is also a metric name")
    if not r.items: r.ok("no metric/column name clash inside aggregates")
    return r
if __name__ == "__main__": cli(check, NAME)
