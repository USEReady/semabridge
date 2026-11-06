"""Issue: PREVIOUSYEAR/PARALLELPERIOD (whole previous period) translated like SAMEPERIODLASTYEAR (same dates last year).
Expect: their SQL differs from the same-period-last-year SQL of the same base measure."""
import re
from common import Result, cli
NAME = "T20 PREVIOUSYEAR vs SAMEPERIODLASTYEAR"
FULL = re.compile(r"\b(PREVIOUSYEAR|PREVIOUSQUARTER|PREVIOUSMONTH|PARALLELPERIOD)\s*\(", re.I)
def check(sv, pbix, args):
    r = Result(NAME)
    hits = [m for m in pbix.measures if FULL.search(m["dax"])]
    if not hits: r.info("no whole-previous-period functions"); return r
    for m in hits:
        mt = sv.metric(m["name"])
        if mt is None or mt.is_null: r.info(f"{m['name']}: NULL"); continue
        if re.search(r"SPLY", mt.expr, re.I) and not re.search(r"FULL|PREV", mt.expr, re.I):
            r.fail(f"{m['name']}: uses the same-period-last-year flag; Power BI uses the full previous period")
        else:
            r.warn(f"{m['name']}: check that the SQL covers the full previous period")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
