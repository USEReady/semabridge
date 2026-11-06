"""Issue: a table name gets "_ENRICHED" twice. Expect: no source ends with _ENRICHED_ENRICHED."""
from common import Result, cli
NAME = "T01 double _ENRICHED in table names"
def check(sv, pbix, args):
    r = Result(NAME)
    bad = [f"{a} -> {t['source']}" for a, t in sv.tables.items() if t["source"].endswith("_ENRICHED_ENRICHED")]
    for b in bad: r.fail(b)
    if not bad: r.ok(f"{len(sv.tables)} tables, none with a doubled suffix")
    return r
if __name__ == "__main__": cli(check, NAME)
