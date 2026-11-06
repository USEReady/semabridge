"""Issue: quick measures IF(ISFILTERED(...), ERROR(...), calc) stay NULL.
Expect: the calculation part is translated (value check against Power BI still needed)."""
import re
from common import Result, cli
NAME = "T04 quick-measure ERROR guard"
def check(sv, pbix, args):
    r = Result(NAME)
    hits = [m for m in pbix.measures if re.search(r"\bERROR\s*\(", m["dax"], re.I) and re.search(r"\bIF\s*\(", m["dax"], re.I)]
    if not hits: r.info("no IF(..., ERROR(...), ...) measures in this model"); return r
    for m in hits:
        mt = sv.metric(m["name"])
        if mt is None or mt.is_null: r.fail(f"{m['name']}: not translated ({(mt.comment if mt else 'missing')[:90]})")
        else: r.warn(f"{m['name']}: translated - compare the value with Power BI")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
