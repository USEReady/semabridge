"""Issue: an enriched view is used even when nothing is added; measure-only tables get views.
Expect: every *_ENRICHED table has at least one column that the Power BI table does not have."""
from common import Result, cli, squash
NAME = "T11 unneeded enriched views"
def check(sv, pbix, args):
    r = Result(NAME)
    pb_cols = {squash(t): {squash(c) for c in cs} for t, cs in pbix.columns.items()}
    measure_only = {squash(m["table"]) for m in pbix.measures} - {t for t, cs in pb_cols.items() if cs - {"ID"}}
    for a, t in sv.tables.items():
        key = squash(t["source"].replace("_ENRICHED", ""))
        if key in measure_only:
            r.warn(f"{a}: measure-only table emitted ({t['source']})")
        if not t["source"].endswith("_ENRICHED"): continue
        base = pb_cols.get(key) or pb_cols.get(squash(a)) or set()
        extra = {d.column for d in sv.dims if d.table == a and squash(d.column) not in base}
        if not extra:
            r.fail(f"{a}: {t['source']} adds no column - use the base table")
        else:
            r.ok(f"{a}: adds {sorted(extra)[:4]}")
    if not r.items: r.info("no enriched views")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
