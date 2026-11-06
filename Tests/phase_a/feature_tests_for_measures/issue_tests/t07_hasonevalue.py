"""Issue: IF(HASONEVALUE(col), calc) measures.
Expect: if translated, value check needed (Power BI shows the value only when one item is selected)."""
import re
from common import Result, cli
NAME = "T07 HASONEVALUE-guarded measures"
def check(sv, pbix, args):
    r = Result(NAME)
    hits = [m for m in pbix.measures if re.search(r"\bHASONEVALUE\s*\(", m["dax"], re.I)]
    if not hits: r.info("no HASONEVALUE in this model"); return r
    for m in hits:
        mt = sv.metric(m["name"])
        if mt is None: r.fail(f"{m['name']}: missing from the view")
        elif mt.is_null: r.info(f"{m['name']}: NULL ({mt.comment[:80]})")
        else: r.warn(f"{m['name']}: translated - check totals: Power BI shows blank when several items are selected")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
