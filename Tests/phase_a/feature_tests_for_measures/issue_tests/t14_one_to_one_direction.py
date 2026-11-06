"""Issue: a 1:1 relationship points away from the table that holds the measures.
Expect: for every 1:1 relationship, the table with more metrics references the other one."""
from common import Result, cli, alias_for
NAME = "T14 one-to-one relationship direction"
def check(sv, pbix, args):
    r = Result(NAME)
    counts = {}
    for m in sv.metrics:
        if not m.is_null: counts[m.table] = counts.get(m.table, 0) + 1
    ones = [x for x in pbix.rels if x["active"] and ("one" in x["card"].lower() and "many" not in x["card"].lower()
            or x["card"].replace(" ", "") in ("1:1", "OneToOne", "One-One"))]
    if not ones: r.info("no 1:1 relationships"); return r
    for x in ones:
        a, b = alias_for(sv, x["from_t"]), alias_for(sv, x["to_t"])
        if not a or not b: r.warn(f"{x['from_t']} - {x['to_t']}: table not in the view"); continue
        if sv.related(a, b): src, dst = a, b
        elif sv.related(b, a): src, dst = b, a
        else: r.fail(f"{a} - {b}: 1:1 relationship missing"); continue
        if counts.get(dst, 0) > counts.get(src, 0):
            r.fail(f"{src} -> {dst}: metrics are on {dst} ({counts.get(dst,0)}) but cannot use {src} columns; reverse it")
        else:
            r.ok(f"{src} -> {dst}: direction fits the metrics ({counts.get(src,0)} vs {counts.get(dst,0)})")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
