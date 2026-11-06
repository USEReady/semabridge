"""Issue: helper columns/metrics created by SemaBridge are visible (and copies of other tables' columns).
Expect: helper dimensions (copies, flags, max dates) and helper metrics (_NUM/_DEN/_SUM/_CNT) are PRIVATE;
real Power BI columns stay public."""
import re
from common import Result, cli, squash
NAME = "T13 helper columns and metrics private"
FLAG = re.compile(r"^(IS_|MAX_DATE$|_|CURRENT_)", re.I)
HELPER_METRIC = re.compile(r"_(NUM|DEN|SUM|CNT)$")
def check(sv, pbix, args):
    r = Result(NAME)
    pb_cols = {}
    if pbix:
        for t, cs in pbix.columns.items():
            pb_cols[squash(t)] = {squash(c) for c in cs}
    pb_measures = {squash(m["name"]) for m in pbix.measures} if pbix else set()
    aliases = set(sv.tables)
    for d in sv.dims:
        own = pb_cols.get(squash(sv.tables.get(d.table, {}).get("source", d.table).replace("_ENRICHED", "")), None)
        is_copy = any(d.column.startswith(a + "_") and d.column != a for a in aliases if a != d.table)
        col_keys = {squash(d.column), squash(re.sub(r"^COL_", "", d.column))}
        is_new = own is not None and not (col_keys & own)
        helper = (FLAG.match(d.column) and (own is None or is_new)) or (is_copy and (own is None or is_new)) or (is_new and pbix)
        if helper and not d.private:
            r.fail(f"{d.table}.{d.name} ({d.column}): helper column is public")
        if d.private and own is not None and not is_new and not d.name.startswith("_COL_"):
            r.fail(f"{d.table}.{d.name}: real Power BI column is PRIVATE")
    for m in sv.metrics:
        if HELPER_METRIC.search(m.name) and squash(m.name) not in pb_measures and not m.private:
            r.fail(f"{m.table}.{m.name}: helper metric is public")
    if not r.items: r.ok("helpers private, real columns public")
    return r
if __name__ == "__main__": cli(check, NAME)
