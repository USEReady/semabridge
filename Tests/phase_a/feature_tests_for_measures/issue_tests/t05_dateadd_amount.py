"""Issue: DATEADD(date, -2/-7, DAY...) is read as -1, and unknown intervals become YEAR.
Expect: the SQL contains the real amount and interval, or the measure is NULL with a reason."""
import re
from common import Result, cli, func_calls
NAME = "T05 DATEADD amount and interval"
UNITS = {"YEAR", "QUARTER", "MONTH", "DAY"}
def _amount(s):
    s = s.replace(" ", "")
    return int(s) if re.fullmatch(r"[-+]?\d+", s) else None
def check(sv, pbix, args):
    r = Result(NAME)
    found = False
    for pm in pbix.measures:
        for a in func_calls(pm["dax"], "DATEADD"):
            if len(a) < 3: continue
            found = True
            amt, unit = _amount(a[1]), a[2].strip().strip('"').upper()
            mt = sv.metric(pm["name"])
            label = f"{pm['name']}: DATEADD({a[1]}, {unit})"
            if mt is None: r.fail(f"{label}: missing from the view"); continue
            if mt.is_null: r.info(f"{label}: NULL ({mt.comment[:70]})"); continue
            if unit not in UNITS: r.fail(f"{label}: unknown interval translated anyway"); continue
            if amt is None: r.warn(f"{label}: amount is not a number, check by hand"); continue
            if abs(amt) != 1 and not re.search(rf"(?<![\d.]){abs(amt)}(?![\d.])", mt.expr):
                r.fail(f"{label}: amount {amt} not in the SQL (treated as -1?)")
            elif unit == "DAY" and "DAY" not in mt.expr.upper():
                r.fail(f"{label}: day interval not in the SQL")
            else:
                r.ok(f"{label}: amount and interval kept")
    if not found: r.info("no DATEADD in this model")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
