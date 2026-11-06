"""Issue: Power BI hierarchy references 'T'[Date].[Year] are not understood.
Expect: those measures get SQL, and the SQL contains no leftover '.[' syntax."""
import re
from common import Result, cli
NAME = "T02 hierarchy references 'T'[Col].[Level]"
def check(sv, pbix, args):
    r = Result(NAME)
    hits = [m for m in pbix.measures if re.search(r"\]\s*\.\s*\[", m["dax"])]
    if not hits: r.info("no hierarchy references in this model"); return r
    for m in hits:
        mt = sv.metric(m["name"])
        if mt is None: r.fail(f"{m['name']}: missing from the view")
        elif mt.is_null:
            if "hierarchy" in mt.comment.lower() or ".[" in mt.comment or "could not be resolved" in mt.comment.lower():
                r.fail(f"{m['name']}: NULL ({mt.comment[:90]})")
            else:
                r.info(f"{m['name']}: NULL for non-hierarchy reason ({mt.comment[:90]})")
        elif ".[" in mt.expr: r.fail(f"{m['name']}: SQL still contains '.[': {mt.expr[:90]}")
        else: r.ok(f"{m['name']}: translated (check the value against Power BI)")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
