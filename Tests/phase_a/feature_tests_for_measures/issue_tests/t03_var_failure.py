"""Issue: one VAR that cannot be translated stops the whole measure.
Expect: no 'Failed to render variable' reasons; measures whose only problem is an unused VAR are translated."""
import re
from common import Result, cli
NAME = "T03 VAR failure stops the whole measure"
def check(sv, pbix, args):
    r = Result(NAME)
    for m in sv.metrics:
        if "failed to render variable" in m.comment.lower():
            r.fail(f"{m.table}.{m.name}: {m.comment[:100]}")
    if pbix:
        for pm in pbix.measures:
            names = re.findall(r"\bVAR\s+([\w_]+)\s*=", pm["dax"], re.I)
            unused = [v for v in names if len(re.findall(rf"\b{re.escape(v)}\b", pm["dax"])) == 1]
            mt = sv.metric(pm["name"])
            if unused and mt is not None and mt.is_null:
                r.warn(f"{pm['name']}: NULL and has unused variable(s) {unused} - check the reason: {mt.comment[:80]}")
    if not any(s == "FAIL" for s, _ in r.items): r.ok("no measure failed because of a variable")
    return r
if __name__ == "__main__": cli(check, NAME)
