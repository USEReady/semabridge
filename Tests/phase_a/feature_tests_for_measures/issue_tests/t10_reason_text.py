"""Issue: reasons cut mid-word, column catalogs in reasons, quoted names in comments.
Expect: short complete reasons, no 'Available columns' lists, no quoted identifiers."""
import re
from common import Result, cli
NAME = "T10 reason and comment text"
def check(sv, pbix, args):
    r = Result(NAME)
    for m in sv.metrics:
        c = m.comment
        if not c: continue
        if re.search(r"[A-Za-z]\.\.\.$", c): r.fail(f"{m.table}.{m.name}: cut mid-word '...{c[-40:]}'")
        if "available columns" in c.lower(): r.fail(f"{m.table}.{m.name}: lists table columns ({len(c)} chars)")
        elif len(c) > 300: r.warn(f"{m.table}.{m.name}: {len(c)} characters")
        if re.search(r'\w\."\w', c): r.warn(f"{m.table}.{m.name}: quoted identifier in comment")
    if not r.items: r.ok("all reasons short and clean")
    return r
if __name__ == "__main__": cli(check, NAME)
