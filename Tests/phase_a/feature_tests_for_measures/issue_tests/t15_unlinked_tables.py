"""Issue: a generated date table (or any table) is added with no relationship.
Expect: a table without relationships is allowed only if it is a Power BI table that has no relationships there either."""
from common import Result, cli, squash, alias_for
NAME = "T15 unlinked tables"
def check(sv, pbix, args):
    r = Result(NAME)
    linked = {f for _, f, _, _, _ in sv.rels} | {t for _, _, _, t, _ in sv.rels}
    pb_linked = set()
    pb_tables = {}
    if pbix:
        for x in pbix.rels:
            pb_linked |= {squash(x["from_t"]), squash(x["to_t"])}
        pb_tables = {squash(t): t for t in pbix.columns}
    for a, t in sv.tables.items():
        if a in linked: continue
        key = squash(t["source"].replace("_ENRICHED", ""))
        in_pbix = key in pb_tables or squash(a) in pb_tables or squash(a).replace("COL", "", 1) in pb_tables
        if pbix and not in_pbix:
            r.fail(f"{a} ({t['source']}): not a Power BI table and not linked (generated table?)")
        elif pbix and (key in pb_linked or squash(a) in pb_linked):
            r.fail(f"{a}: linked in Power BI but not in the view")
        else:
            r.info(f"{a}: standalone (also standalone in Power BI)" if pbix else f"{a}: no relationship")
    if not any(s == "FAIL" for s, _ in r.items): r.ok("no unexpected unlinked tables")
    return r
if __name__ == "__main__": cli(check, NAME)
