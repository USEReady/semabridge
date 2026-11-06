"""Issue: AVERAGEX/SUMX over VALUES/DISTINCT/SUMMARIZE (one step per group) turned into a plain row aggregate.
Expect: NULL with a reason, or a window/precompute; MINX/MAXX of the same column over VALUES may be plain MIN/MAX."""
import re
from common import Result, cli, func_calls
NAME = "T17 iteration grain (average over groups)"
ITER = ["AVERAGEX", "SUMX", "MINX", "MAXX", "COUNTX"]
GROUPS = re.compile(r"^\s*(VALUES|DISTINCT|SUMMARIZE|SUMMARIZECOLUMNS|ALL)\s*\(", re.I)
def check(sv, pbix, args):
    r = Result(NAME)
    for pm in pbix.measures:
        for it in ITER:
            for a in func_calls(pm["dax"], it):
                if len(a) < 2: continue
                first = a[0]
                while re.match(r"^\s*(KEEPFILTERS|FILTER)\s*\(", first, re.I):
                    first = func_calls(first, re.match(r"^\s*(\w+)", first).group(1))[0][0]
                if not GROUPS.match(first): continue
                inner = re.sub(r"^\s*\w+\s*\(|\)\s*$", "", first).strip()
                if it in ("MINX", "MAXX") and inner.replace(" ", "") == a[1].replace(" ", ""):
                    continue
                mt = sv.metric(pm["name"])
                if mt is None or mt.is_null: r.ok(f"{pm['name']}: {it}({a[0][:30]}) left NULL"); continue
                if " OVER " in mt.expr.upper() or "_ALL_" in mt.expr.upper():
                    r.warn(f"{pm['name']}: uses a window/precompute - check the value")
                else:
                    r.fail(f"{pm['name']}: {it} over groups translated as a plain aggregate: {mt.expr[:70]}")
    if not r.items: r.info("no iterators over groups in this model")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
