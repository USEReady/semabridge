"""Regression guard: a time-shifted measure (last year / previous month ...) emitted with the same SQL as its base measure.
Expect: shifted measures never have the same SQL as the measure they shift, and never plain T.<BASE_METRIC>."""
import re
from common import Result, cli, measure_refs
NAME = "T27 time-shifted measure equals base measure"
SHIFT = re.compile(r"\b(SAMEPERIODLASTYEAR|DATEADD|PARALLELPERIOD|PREVIOUSYEAR|PREVIOUSQUARTER|PREVIOUSMONTH|DATESYTD|DATESQTD|DATESMTD|TOTALYTD|TOTALQTD|TOTALMTD|DATESINPERIOD)\s*\(", re.I)
def check(sv, pbix, args):
    r = Result(NAME)
    for pm in pbix.measures:
        if not SHIFT.search(pm["dax"]): continue
        mt = sv.metric(pm["name"])
        if mt is None or mt.is_null: continue
        for ref in measure_refs(pm["dax"]):
            base = sv.metric(ref)
            if base is None or base is mt: continue
            if mt.expr.strip() == base.expr.strip() or re.fullmatch(rf"\s*{base.table}\.\"?{base.name}\"?\s*", mt.expr):
                r.fail(f"{pm['name']}: same SQL as [{ref}] - time shift lost")
        if re.search(r"CURRENT_DATE\s*\(", mt.expr, re.I):
            r.warn(f"{pm['name']}: anchored on CURRENT_DATE() - Power BI uses the latest date in the data")
    if not any(s == "FAIL" for s, _ in r.items): r.ok("time-shifted measures differ from their base")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
