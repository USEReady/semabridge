"""Regression guard: COUNTROWS(FILTER(T, cond)) translated as COUNT(*) (filter lost), as in the ontology branch.
Expect: a filtered count (COUNT(CASE WHEN ...)) on T's table."""
import re
from common import Result, cli, func_calls, alias_for
NAME = "T25 COUNTROWS(FILTER(...)) keeps its filter"
def check(sv, pbix, args):
    r = Result(NAME)
    for pm in pbix.measures:
        for a in func_calls(pm["dax"], "COUNTROWS"):
            if not a or not re.match(r"^\s*FILTER\s*\(", a[0], re.I): continue
            mt = sv.metric(pm["name"])
            if mt is None or mt.is_null: r.info(f"{pm['name']}: NULL"); continue
            if re.fullmatch(r"\s*COUNT\s*\(\s*\*\s*\)\s*", mt.expr): r.fail(f"{pm['name']}: COUNT(*) - filter lost")
            elif "CASE" not in mt.expr.upper(): r.warn(f"{pm['name']}: no CASE in SQL, check the filter")
            else: r.ok(f"{pm['name']}: filtered count")
            ft = func_calls(a[0], "FILTER")
            t = alias_for(sv, ft[0][0].strip(" '")) if ft and ft[0] else None
            if t and mt.table != t: r.fail(f"{pm['name']}: metric on {mt.table}, rows counted are {t}")
    if not r.items: r.info("no COUNTROWS(FILTER(...)) in this model")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
