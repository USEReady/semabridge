"""Issue: BLANK-safe wrapper repeated at every level makes SQL huge, and is used even around COUNT.
Expect: no metric above the size limit; no wrapper around COUNT operands.
Also guards the opposite mistake: SUM +/- SUM with no wrapper (empty side makes the result NULL)."""
import re
from common import Result, cli
NAME = "T09 SQL size and BLANK-safe wrapper"
def check(sv, pbix, args):
    r = Result(NAME)
    lim = args.limit if args and getattr(args, "limit", None) else 10000
    for m in sv.metrics:
        n = len(m.expr)
        if n > lim: r.fail(f"{m.table}.{m.name}: {n:,} characters (limit {lim:,})")
        elif n > lim // 2: r.warn(f"{m.table}.{m.name}: {n:,} characters")
        if re.search(r"\(\(COUNT\([^()]*(\([^()]*\))*[^()]*\)\)\)\s+IS\s+NULL", m.expr, re.I):
            r.warn(f"{m.table}.{m.name}: BLANK wrapper around COUNT (COUNT is never NULL)")
        if re.search(r"\)\)\s*[+-]\s*\(+SUM\(", m.expr) and "COALESCE" not in m.expr.upper():
            r.warn(f"{m.table}.{m.name}: SUM +/- SUM without NULL handling - empty side gives NULL")
    if not r.items: r.ok(f"{len(sv.metrics)} metrics within size and wrapper rules")
    return r
if __name__ == "__main__":
    cli(check, NAME, extra=[(["--limit"], {"type": int, "default": 10000})])
