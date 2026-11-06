"""Issue: one untranslated helper (e.g. _ShowValueForDates) or leave-one-out pattern blocks many measures.
Shows which NULL measures block the most others, and lists leave-one-out measures still NULL."""
import re
from common import Result, cli, measure_refs, squash
NAME = "T24 NULL measures blocking others / leave-one-out"
LOO = re.compile(r"FILTER\s*\(\s*ALL\s*\([^)]*\)\s*,[^)]*<>\s*SELECTEDVALUE", re.I)
def check(sv, pbix, args):
    r = Result(NAME)
    nulls = {squash(m.name) for m in sv.metrics if m.is_null}
    blocks = {}
    for pm in pbix.measures:
        if squash(pm["name"]) not in nulls: continue
        for d in set(measure_refs(pm["dax"])):
            if squash(d) in nulls and squash(d) != squash(pm["name"]):
                blocks.setdefault(d, []).append(pm["name"])
    for d, users in sorted(blocks.items(), key=lambda x: -len(x[1]))[:10]:
        (r.fail if len(users) >= 5 else r.warn)(f"[{d}] is NULL and blocks {len(users)} measure(s): {users[:5]}")
    for pm in pbix.measures:
        if LOO.search(pm["dax"]):
            mt = sv.metric(pm["name"])
            if mt is None or mt.is_null: r.warn(f"{pm['name']}: leave-one-out pattern still NULL")
            else: r.ok(f"{pm['name']}: leave-one-out translated - check values")
    if not r.items: r.ok("no NULL measure blocks others")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
