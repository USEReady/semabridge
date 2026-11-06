"""Issue: CALCULATE(..., ALL(Table)) / FILTER(ALL(Table), ...) ('ignore every filter') computed per group.
Expect: a whole-table constant (precomputed _ALL_ column), a window, or NULL - never a plain filtered aggregate."""
import re
from common import Result, cli
NAME = "T18 whole-table ALL computed per group"
ALLT = re.compile(r"\b(ALL|REMOVEFILTERS)\s*\(\s*'?[^'\[\),]+'?\s*\)", re.I)
def check(sv, pbix, args):
    r = Result(NAME)
    for pm in pbix.measures:
        if not ALLT.search(pm["dax"]) or "SELECTEDVALUE" in pm["dax"].upper(): continue
        mt = sv.metric(pm["name"])
        if mt is None: continue
        if mt.is_null: r.info(f"{pm['name']}: NULL ({mt.comment[:60]})"); continue
        e = mt.expr.upper()
        if " OVER " in e or "_ALL_" in e: r.ok(f"{pm['name']}: whole-table value via window/precompute")
        else: r.fail(f"{pm['name']}: ALL(table) ignored - plain aggregate per group: {mt.expr[:70]}")
    if not r.items: r.info("no whole-table ALL measures")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
