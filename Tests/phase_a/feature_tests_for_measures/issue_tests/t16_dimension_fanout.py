"""Issue: a dimension column copied onto every fact row is summed/counted/averaged (counted once per fact row).
Expect: SUM/COUNT/AVERAGE of a column of the 'one' side table is not computed on the fact table."""
import re
from common import Result, cli, alias_for, squash
NAME = "T16 dimension values summed on the fact (fan-out)"
AGG = re.compile(r"\b(SUM|AVERAGE|COUNT|COUNTA)\s*\(\s*'?([^'\[\(]+?)'?\s*\[([^\]]+)\]", re.I)
def check(sv, pbix, args):
    r = Result(NAME)
    for pm in pbix.measures:
        mt = sv.metric(pm["name"])
        if mt is None or mt.is_null: continue
        for fn, tbl, col in AGG.findall(pm["dax"]):
            dim = alias_for(sv, tbl.strip())
            if not dim or dim == mt.table or not sv.related(mt.table, dim): continue
            pat = rf"\b(SUM|AVG|COUNT)\s*\(\s*{mt.table}\.\"?\w*{squash(col)}\w*\"?"
            if re.search(pat, re.sub(r'"', '', mt.expr), re.I) or re.search(pat, mt.expr, re.I):
                r.fail(f"{pm['name']}: {fn}({tbl}[{col}]) computed on {mt.table} rows - counts {dim} values once per {mt.table} row")
    if not r.items: r.ok("no related-table values aggregated on the fact")
    return r
if __name__ == "__main__": cli(check, NAME, needs_pbix=True)
