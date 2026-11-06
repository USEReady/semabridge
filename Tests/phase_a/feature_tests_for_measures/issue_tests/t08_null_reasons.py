"""Issue: NULL measures with no reason, a generic reason, or no mention of the measure they depend on.
Expect: every NULL measure has a specific reason; dependent measures name the dependency."""
import re
from common import Result, cli, measure_refs, squash
NAME = "T08 NULL measure reasons"
GENERIC = ["dax expression unsupported", "cast(null) detected inside larger expression",
           "no sql expression produced", "tier-5 (llm)", "not translatable'", "unsupported dax pattern detected"]
def check(sv, pbix, args):
    r = Result(NAME)
    nulls = {squash(m.name): m for m in sv.metrics if m.is_null}
    for m in nulls.values():
        c = m.comment.lower()
        if not c.strip(): r.fail(f"{m.table}.{m.name}: no reason")
        elif any(g in c + "'" for g in GENERIC): r.fail(f"{m.table}.{m.name}: generic reason '{m.comment[:70]}'")
    if pbix:
        for pm in pbix.measures:
            mt = nulls.get(squash(pm["name"]))
            if not mt: continue
            deps = [d for d in measure_refs(pm["dax"]) if squash(d) in nulls and squash(d) != squash(pm["name"])]
            if deps and not any(squash(d) in squash(mt.comment) for d in deps):
                r.warn(f"{pm['name']}: depends on NULL {deps} but the reason doesn't say so")
    if not r.items: r.ok(f"{len(nulls)} NULL measures, all with specific reasons")
    return r
if __name__ == "__main__": cli(check, NAME)
