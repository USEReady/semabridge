"""Issue: LASTNONBLANK 'latest value' measures are not translated.
Expect: if translated, they carry NON ADDITIVE BY; values must still be checked against Power BI."""
import re
from common import Result, cli
NAME = "T06 LASTNONBLANK latest-value measures"
def check(sv, pbix, args):
    r = Result(NAME)
    hits = [m for m in pbix.measures if re.search(r"\bLASTNONBLANK\b", m["dax"], re.I)]
    if not hits: r.info("no LASTNONBLANK in this model"); return r
    for m in hits:
        mt = sv.metric(m["name"])
        if mt is None: r.fail(f"{m['name']}: missing from the view")
        elif mt.is_null: r.info(f"{m['name']}: NULL ({mt.comment[:80]})")
        elif not mt.non_additive: r.fail(f"{m['name']}: translated without NON ADDITIVE BY (sums across periods)")
        else: r.warn(f"{m['name']}: NON ADDITIVE BY ({mt.non_additive}) - compare the value with Power BI")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
