"""Issue: a column without table name inside FILTER(T, ...) is looked up on the measure's home table.
Expect: in the SQL those columns are qualified with T's alias."""
import re
from common import Result, cli, func_calls, alias_for, squash
NAME = "T19 bare columns inside FILTER(T, ...)"
def check(sv, pbix, args):
    r = Result(NAME)
    for pm in pbix.measures:
        for a in func_calls(pm["dax"], "FILTER"):
            if len(a) < 2: continue
            tm = re.match(r"^\s*(?:KEEPFILTERS\s*\(\s*)?'?([^'\(\)\[]+?)'?\s*\)?\s*$", a[0])
            if not tm: continue
            t_alias = alias_for(sv, tm.group(1).strip())
            bare = re.findall(r"(?<![\w'\]])\[([^\]]+)\]", " ".join(a[1:]))
            mt = sv.metric(pm["name"])
            if not t_alias or not bare or mt is None or mt.is_null: continue
            for c in bare:
                hits = re.findall(rf'(\w+)\."?{squash(c)}"?\b', mt.expr.upper())
                wrong = [h for h in hits if h != t_alias]
                if wrong: r.fail(f"{pm['name']}: [{c}] resolved to {sorted(set(wrong))} instead of {t_alias}")
                elif hits: r.ok(f"{pm['name']}: [{c}] on {t_alias}")
    if not r.items: r.info("no bare columns inside FILTER")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
